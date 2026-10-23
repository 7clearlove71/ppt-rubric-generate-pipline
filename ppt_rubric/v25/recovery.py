"""Integrity checks for recovering failed requests without repeating judgments."""
from __future__ import annotations

import copy
from pathlib import Path

from ..common import digest, file_digest, read_json


def image_references(images, strict=False):
    result = []
    for label, path in images:
        path = Path(path)
        if strict and not path.is_file():
            raise ValueError('Recovery image is missing; no requests were sent')
        result.append({'label': label, 'path': str(path),
                       'sha256': file_digest(path) if path.is_file() else None})
    return result


def request_binding(config):
    return {key: copy.deepcopy(config.get(key)) for key in (
        'base_url', 'judge_model', 'max_output_tokens', 'role_max_output_tokens',
        'request_options', 'role_request_options', 'role_supports_images')}


def assessment_binding(deck, rubric, catalog, config, prompts, strict=False):
    from .materials import material_images
    reference_images = material_images(catalog)
    pdf = Path(deck['pdf']) if deck.get('pdf') else None
    if strict and pdf is not None and not pdf.is_file():
        raise ValueError('Recovery PDF is missing; no requests were sent')
    return digest({'document': deck, 'rubric_hash': rubric['rubric_hash'],
                   'source_catalog': catalog, 'prompts': prompts,
                   'material_images': image_references(reference_images, strict),
                   'request_profile': request_binding(config),
                   'focus_profile': {key: config.get(key) for key in
                                     ('max_images_per_request', 'max_visual_focus_images', 'dpi')},
                   'pdf_sha256': file_digest(pdf) if pdf is not None and pdf.is_file() else None,
                   'images': image_references([(str(s['page']), s['image']) for s in deck['slides']], strict)})


def request_hash(config, system, payload, images):
    return digest({'profile': request_binding(config), 'system': system, 'payload': payload,
                   'images': image_references(images)})


def validate_checkpoint(path, binding, parse_answer):
    previous = read_json(path)
    if previous.get('recovery_binding') != binding:
        raise ValueError('Recovery checkpoint lacks a matching input/request binding; keep the original run intact')
    if previous.get('checkpoint_hash') != digest({k: v for k, v in previous.items() if k != 'checkpoint_hash'}):
        raise ValueError('Recovery checkpoint integrity check failed')
    content = previous['content']['records']
    visual = previous['visual_batches']
    if (len({r['rubric_id'] for r in content}) != len(content)
            or len({tuple(r['pages']) for r in visual}) != len(visual)):
        raise ValueError('Recovery checkpoint contains duplicate requests')
    for record in content + visual:
        if record.get('status') == 'success':
            if 'rubric_ids' in record:
                from .judge_batch import parse_batch_answer
                if parse_batch_answer(record.get('raw'), record['rubric_ids']) != record.get('verdicts'):
                    raise ValueError('Recovery checkpoint verdicts differ from the original batch output')
            elif parse_answer(record.get('raw')) != record.get('answer'):
                raise ValueError('Recovery checkpoint answer differs from its original boxed output')
        elif record.get('status') != 'unavailable':
            raise ValueError('Recovery checkpoint has an unsupported request status')
        if not record.get('input_hash'):
            raise ValueError('Recovery checkpoint has no request input hash')
        for image in record.get('image_refs', []):
            if not image.get('sha256') or file_digest(image['path']) != image['sha256']:
                raise ValueError('Recovery image changed after the original request')
    return previous


def recovery_history(source, _visited=None):
    """Read immutable prior accounting; a recovery never reopens old authorization."""
    source = Path(source).resolve()
    judgments = source / 'judgments' if (source / 'judgments').is_dir() else source
    roots = [source]
    if judgments == source and source.name == 'judgments':
        roots.append(source.parent)
    ledgers = [p for root in roots for p in (root / 'primary-api/requests.json', root / 'bailian-api/requests.json') if p.is_file()]
    if len(ledgers) != 1:
        raise ValueError('Recovery requires exactly one identifiable prior candidate-provider ledger')
    ledger = ledgers[0]
    run = ledger.parent.parent
    visited = set(_visited or ())
    if run in visited:
        raise ValueError('Recovery accounting lineage contains a cycle')
    visited.add(run)
    for marker in ('authorization.json', 'recovery-authorization.json',
                   'diagnostic-authorization.json', 'run-state.json'):
        if (run / marker).exists() and read_json(run / marker).get('status') == 'active':
            raise ValueError('Prior run is still active; wait for it to close before recovery')
    rows = read_json(ledger)
    if (not isinstance(rows, list) or any(not isinstance(r, dict) or r.get('status') not in {'started', 'success', 'failed'} for r in rows)
            or any(r.get('status') == 'started' for r in rows)):
        raise ValueError('Prior run has unresolved in-flight requests; reconcile it before recovery')
    earlier = read_json(run / 'recovery.json') if (run / 'recovery.json').exists() else {}
    if earlier and earlier.get('status') != 'closed':
        raise ValueError('Prior recovery is still active')
    if type(earlier.get('prior_api_requests', 0)) is not int or earlier.get('prior_api_requests', 0) < 0:
        raise ValueError('Prior recovery has invalid cumulative accounting')
    if earlier:
        parent_ledger = Path(earlier.get('prior_ledger', ''))
        if not parent_ledger.is_file() or file_digest(parent_ledger) != earlier.get('prior_ledger_sha256'):
            raise ValueError('Prior recovery ledger lineage changed or is missing')
        parent_recovery = parent_ledger.parent.parent / 'recovery.json'
        parent_hash = file_digest(parent_recovery) if parent_recovery.is_file() else None
        if parent_hash != earlier.get('prior_recovery_sha256'):
            raise ValueError('Prior recovery accounting metadata changed')
        parent_history = recovery_history(parent_ledger.parent.parent, visited)[2]
        if parent_history['prior_api_requests'] != earlier['prior_api_requests']:
            raise ValueError('Prior recovery cumulative request count differs from its ledger lineage')
    return judgments, run, {'prior_api_requests': earlier.get('prior_api_requests', 0) + len(rows),
                            'prior_ledger': str(ledger), 'prior_ledger_sha256': file_digest(ledger),
                            'prior_recovery_sha256': file_digest(run / 'recovery.json') if earlier else None}
