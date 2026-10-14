"""Compact read-only progress for the authorized scoring run."""
import json
from pathlib import Path
from control import budget
ROOT=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
limits=budget();rows=read(ROOT/'judgments/requests.json')
result={'maas_requests':limits['maas']['actual_requests'],'bailian_requests':limits['bailian']['actual_requests'],
        'recent':[{k:r.get(k) for k in ('request_id','role','status','http_status','validation_error')} for r in rows[-2:]],'cases':[]}
for event,row in zip(result['recent'],rows[-2:]):
    cache=ROOT.parents[1]/'.cache/api'/(row['cache_key']+'.json')
    if row['status']=='success' and cache.exists():
        parsed=read(cache)['parsed']
        event.update({k:parsed[k] for k in ('page','answer','consistent') if k in parsed})
for cid in ['01','02','03','05','06','09','10']:
    directory=ROOT/'judgments'/cid;record={'case':cid,'candidates':{}}
    rubric=read(ROOT/'rubrics'/cid/'rubric.json');record['criteria']=len(rubric['items'])
    for candidate in 'AB':
        verdict_file=directory/(candidate+'-initial.json');evidence_file=directory/(candidate+'-evidence.json');final_file=directory/(candidate+'.json')
        v=read(verdict_file) if verdict_file.exists() else {};e=read(evidence_file) if evidence_file.exists() else {}
        f=read(final_file) if final_file.exists() else {}
        if not f and verdict_file.exists() and evidence_file.exists() and verdict_file.stat().st_mtime_ns < evidence_file.stat().st_mtime_ns:
            v={}  # Retained previous-run intermediate output is not live progress.
        summary=f.get('summary')
        record['candidates'][candidate]={'judged':len(v),'initial_unknown':sum(x['answer']=='unknown' for x in v.values()),
            'evidence_pages':len(e.get('pages',[])),'evidence_complete':e.get('complete'),
            'final':{k:summary[k] for k in ('status','score','unresolved_weight')} if summary else None,
            'consistency':f.get('consistency_summary')}
    p=directory/'consistency.json';record['reviewed']=len(read(p)) if p.exists() else 0
    evidence_times=[f.stat().st_mtime_ns for f in directory.glob('*-evidence.json')]
    if p.exists() and evidence_times and p.stat().st_mtime_ns < max(evidence_times):record['reviewed']=0
    result['cases'].append(record)
print(json.dumps(result,ensure_ascii=False))
