"""Transactional state for the resumable batch runner (not historical runs)."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .common import canonical, digest, file_digest


@contextmanager
def run_lock(directory):
    """The OS releases this lock even after SIGKILL or a machine restart."""
    import fcntl
    with (Path(directory) / '.batch.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('该运行目录已有批量进程在执行，请等待它结束') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class BatchState:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL,
                result TEXT, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS responses (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
                provider TEXT NOT NULL, invocation TEXT NOT NULL, id INTEGER NOT NULL,
                status TEXT NOT NULL, record TEXT NOT NULL,
                PRIMARY KEY(provider, invocation, id));
            CREATE TABLE IF NOT EXISTS artifacts (
                path TEXT PRIMARY KEY, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY, started REAL NOT NULL, ended REAL,
                status TEXT NOT NULL, details TEXT NOT NULL);
        ''')

    def close(self):
        with self.lock:
            self.db.close()

    def add_tasks(self, cases):
        with self.lock, self.db:
            self.db.executemany('INSERT OR IGNORE INTO tasks VALUES (?, ?, ?, NULL, ?)',
                                [(c['id'], 'pending', '等待处理', time.time()) for c in cases])

    def task(self, case_id, status, stage, result=None, artifacts=()):
        hashes = [(str(Path(path).resolve()), file_digest(path)) for path in artifacts]
        with self.lock, self.db:
            self.db.executemany('INSERT OR REPLACE INTO artifacts VALUES (?,?)', hashes)
            self.db.execute('UPDATE tasks SET status=?, stage=?, result=?, updated=? WHERE id=?',
                            (status, stage, canonical(result) if result is not None else None,
                             time.time(), case_id))

    def tasks(self):
        with self.lock:
            return {row[0]: {'status': row[1], 'stage': row[2],
                             'result': json.loads(row[3]) if row[3] else None}
                    for row in self.db.execute('SELECT id,status,stage,result FROM tasks ORDER BY id')}

    def counts(self):
        with self.lock:
            return dict(self.db.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status'))

    def response(self, key):
        with self.lock:
            row = self.db.execute('SELECT value,hash FROM responses WHERE key=?', (key,)).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if digest(value) != row[1]:
            raise ValueError('批量请求结果完整性校验失败')
        return value

    def save_requests(self, provider, records, responses=()):
        # A successful receipt and its response commit together, before the
        # provider call returns. Resume is independent of the disposable cache.
        with self.lock, self.db:
            for record in records:
                self.db.execute('INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?)',
                                (provider, record['invocation_id'], record['request_id'],
                                 record['status'], canonical(record)))
            for key, value in responses:
                self.db.execute('INSERT OR REPLACE INTO responses VALUES (?,?,?)',
                                (key, canonical(value), digest(value)))

    def save_response(self, key, value):
        self.save_requests('', [], [(key, value)])

    def accounting(self, provider):
        with self.lock:
            rows = self.db.execute('SELECT status,record FROM requests WHERE provider=?', (provider,))
            result = {'requests': 0, 'success': 0, 'failed': 0, 'started': 0,
                      'known_total_tokens': 0, 'missing_usage': 0, 'interrupted': 0}
            for status, raw in rows:
                row = json.loads(raw)
                result['requests'] += 1
                result[status] += 1
                result['interrupted'] += bool(row.get('remote_outcome_unknown'))
                usage = row.get('usage') or {}
                if isinstance(usage.get('total_tokens'), (int, float)):
                    result['known_total_tokens'] += usage['total_tokens']
                else:
                    result['missing_usage'] += 1
        return result

    def request_count(self, provider):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM requests WHERE provider=?', (provider,)).fetchone()[0]

    def recover_interrupted(self):
        """Only called under the exclusive run lock; never invent remote usage."""
        with self.lock, self.db:
            interrupted = list(self.db.execute("SELECT provider,invocation,id,record FROM requests WHERE status='started'"))
            for provider, invocation, number, raw in interrupted:
                row = json.loads(raw)
                row.update(status='failed', previous_status='started', remote_outcome_unknown=True,
                           error_category='process_interrupted', failure_stage='process_interruption',
                           reconciled_unix=time.time())
                self.db.execute('UPDATE requests SET status=?,record=? WHERE provider=? AND invocation=? AND id=?',
                                ('failed', canonical(row), provider, invocation, number))
            self.db.execute("UPDATE tasks SET status='interrupted',stage='等待续跑' WHERE status='running'")
            self.db.execute("UPDATE sessions SET status='interrupted',ended=? WHERE status='running'", (time.time(),))
        return len(interrupted)

    def artifact(self, path):
        path = str(Path(path).resolve())
        sha = file_digest(path)
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO artifacts VALUES (?,?)', (path, sha))

    def verify_artifacts(self):
        with self.lock:
            rows = list(self.db.execute('SELECT path,hash FROM artifacts'))
        for path, sha in rows:
            if not Path(path).is_file() or file_digest(path) != sha:
                raise ValueError(f'已保存的运行产物缺失或变化：{path}；请保留运行目录，不要重建检查点')

    def verify_responses(self):
        with self.lock:
            for value, sha in self.db.execute('SELECT value,hash FROM responses'):
                if digest(json.loads(value)) != sha:
                    raise ValueError('已保存的请求结果完整性校验失败；未发送新请求')

    def start_session(self, details):
        with self.lock, self.db:
            cursor = self.db.execute('INSERT INTO sessions(started,status,details) VALUES (?,?,?)',
                                     (time.time(), 'running', canonical(details)))
            return cursor.lastrowid

    def end_session(self, session, status):
        with self.lock, self.db:
            self.db.execute('UPDATE sessions SET ended=?,status=? WHERE id=?', (time.time(), status, session))

    def export_requests(self, provider, path):
        """Stream an interoperable snapshot; never rewrite it per HTTP attempt."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.requests-', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output, self.lock:
                output.write('[\n')
                for index, (raw,) in enumerate(self.db.execute(
                        'SELECT record FROM requests WHERE provider=? ORDER BY rowid', (provider,))):
                    output.write((',' if index else '') + raw + '\n')
                output.write(']\n')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
