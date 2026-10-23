"""Prepare blinded static packets and import explicit multi-human reviews offline.

Only submitted human records are imported. Blank packets are never ratings.
The organizer mapping must be kept separate from the distributed reviewer folder.
No consensus or majority choice is inferred, including when reviewers agree.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from datetime import datetime
from pathlib import Path

from .common import digest, file_digest, read_json, write_json
from .data import select_cases, validate_manifest
from .label_reliability import _protocol_evidence


TIE_POLICY = {'quality_equivalent': 'tie', 'tradeoff_unresolved': 'undecided',
              'insufficient_evidence': 'undecided'}
_SAFE = re.compile(r'^[A-Za-z0-9_-]+$')


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _check_candidate_files(case):
    for candidate in case['candidates']:
        if file_digest(candidate['pptx']) != candidate['sha256']:
            raise ValueError(f"Case {case['id']}: reviewed PPTX differs from frozen candidate")


def freeze_render_bundle(case, decks):
    """Hash every static page and PDF, preserving render identity and page count."""
    import fitz
    expected = {candidate['id']: candidate['sha256'] for candidate in case['candidates']}
    if set(decks) != set(expected):
        raise ValueError('Render bundle must contain exactly the frozen candidates')
    frozen, identity = {}, {}
    for cid, deck in decks.items():
        render_id, slides = deck.get('identity'), deck.get('slides')
        if (not isinstance(render_id, dict) or render_id.get('sha256') != expected[cid]
                or not _text(render_id.get('renderer')) or not isinstance(slides, list) or not slides):
            raise ValueError('Render identity must match frozen PPTX and specify its renderer')
        if [slide.get('page') for slide in slides] != list(range(1, len(slides) + 1)):
            raise ValueError('Render pages must be complete, contiguous, and ordered')
        pdf = Path(deck['pdf']).resolve()
        with fitz.open(pdf) as document:
            if len(document) != len(slides):
                raise ValueError('Render PDF and static page counts differ')
        pages = [{'page': slide['page'], 'path': str(Path(slide['image']).resolve()),
                  'sha256': file_digest(slide['image'])} for slide in slides]
        frozen[cid] = {'renderer_identity': render_id, 'pdf_path': str(pdf),
                       'pdf_sha256': file_digest(pdf), 'pages': pages}
        identity[cid] = {'renderer_identity': render_id, 'pdf_sha256': frozen[cid]['pdf_sha256'],
                         'page_sha256': [page['sha256'] for page in pages]}
    return {'sha256': digest({'case_id': case['id'], 'candidates': identity}),
            'candidates': frozen}


def _verify_render_bundle(case, bundle):
    if not isinstance(bundle, dict) or not isinstance(bundle.get('candidates'), dict):
        raise ValueError('Explicit render bundle required')
    decks = {}
    for cid, entry in bundle['candidates'].items():
        if file_digest(entry['pdf_path']) != entry['pdf_sha256']:
            raise ValueError('Render PDF hash mismatch')
        for page in entry['pages']:
            if file_digest(page['path']) != page['sha256']:
                raise ValueError('Render static page hash mismatch')
        decks[cid] = {'identity': entry['renderer_identity'], 'pdf': entry['pdf_path'],
                      'slides': [{'page': page['page'], 'image': page['path']} for page in entry['pages']]}
    actual = freeze_render_bundle(case, decks)
    if actual['sha256'] != bundle.get('sha256'):
        raise ValueError('Render bundle identity mismatch')
    return actual


def prepare_review_packets(manifest, prepared, out, reviewers, seed, split='dev', allow_holdout=False):
    """Copy actual render bytes to anonymous static folders; create blank forms.

    ``prepared`` uses the existing prepare output mapping ``case/candidate`` to
    deck objects. A new output directory is mandatory; nothing is overwritten.
    """
    validate_manifest(manifest)
    if split not in {'dev', 'holdout', 'all'}:
        raise ValueError('Invalid split')
    cases = select_cases(manifest, split=split, allow_holdout=allow_holdout)
    if (len(reviewers) < 2 or len(set(reviewers)) != len(reviewers)
            or any(not isinstance(name, str) or not _SAFE.fullmatch(name) for name in reviewers)):
        raise ValueError('At least two distinct safe anonymous reviewer ids required')
    if type(seed) is not int:
        raise ValueError('An explicit integer randomization seed is required')
    # Validate source material before creating any deliverable directories.
    bundles = {}
    for case in cases:
        if len(case['candidates']) != 2 or not _SAFE.fullmatch(case['id']):
            raise ValueError('Review packets require safe case ids and exactly two candidates')
        _check_candidate_files(case)
        bundles[case['id']] = freeze_render_bundle(case, {
            candidate['id']: prepared[f"{case['id']}/{candidate['id']}"] for candidate in case['candidates']})
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    private = out / 'organizer'
    private.mkdir(mode=0o700)
    collection = {'schema_version': 2, 'manifest_id': manifest['id'], 'protocol_id': 'human-same-render-v2',
                  'tie_policy': TIE_POLICY, 'tie_policy_sha256': digest(TIE_POLICY),
                  'randomization_seed': seed, 'cases': []}
    rng = random.Random(seed)
    for case in cases:
        cid, bundle = case['id'], bundles[case['id']]
        row = {'case_id': cid, 'candidate_hashes': {c['id']: c['sha256'] for c in case['candidates']},
               'render_bundle': bundle, 'reviews': [], 'adjudication': None}
        for reviewer in reviewers:
            order = [c['id'] for c in case['candidates']]
            rng.shuffle(order)
            mapping = {f'candidate_{number}': candidate for number, candidate in enumerate(order, 1)}
            packet = out / 'reviewers' / reviewer / cid
            packet.mkdir(parents=True)
            (packet / 'task.txt').write_text(case['prompt'], encoding='utf-8')
            attachment_info = []
            for number, attachment in enumerate(case.get('attachments', []), 1):
                source = Path(attachment['path'])
                if file_digest(source) != attachment['sha256']:
                    raise ValueError('Task attachment differs from frozen manifest')
                name = f'attachment-{number:03d}{source.suffix}'
                shutil.copyfile(source, packet / name)
                attachment_info.append({'file': name, 'sha256': attachment['sha256']})
            public_candidates = {}
            for alias, original in mapping.items():
                directory = packet / alias
                directory.mkdir()
                data = bundle['candidates'][original]
                shutil.copyfile(data['pdf_path'], directory / 'slides.pdf')
                for page in data['pages']:
                    shutil.copyfile(page['path'], directory / f"page-{page['page']:03d}.png")
                public_candidates[alias] = {'page_count': len(data['pages']),
                                            'page_sha256': [p['sha256'] for p in data['pages']]}
            write_json(packet / 'packet.json', {'schema_version': 2, 'case_id': cid,
                                                'render_bundle_sha256': bundle['sha256'],
                                                'candidates': public_candidates, 'attachments': attachment_info,
                                                'tie_policy': TIE_POLICY})
            form = {'schema_version': 2, 'record_id': f'{cid}-{reviewer}-round1',
                    'case_id': cid, 'reviewer_id': reviewer, 'actor_kind': 'human', 'round_id': 'round1',
                    'submitted_at': None, 'render_bundle_sha256': bundle['sha256'],
                    'viewed_all_pages': None, 'choice': None, 'preferred_candidate': None,
                    'evidence_records': [], 'reason_evidence_ids': [], 'reasons': None,
                    'reliability': {'blindness': {'status': 'unknown', 'evidence': []},
                                    'independent_review': {'status': 'unknown', 'evidence': []},
                                    'repeat_review': {'status': 'unknown', 'evidence': []}}}
            write_json(packet / 'review.json', form)
            (packet / 'INSTRUCTIONS.txt').write_text(
                '请独立完整查看 task.txt、附件、两个匿名候选的全部静态 PNG/PDF。\n'
                '先填写 evidence_records，再填写整体 choice 及理由。未知内容保留未知。\n'
                'choice: better / quality_equivalent / tradeoff_unresolved / insufficient_evidence。\n'
                'better 必须填写 preferred_candidate=candidate_1 或 candidate_2，其他选择填 null。\n'
                '每条证据：id, candidate, page, observation, basis, impact, certainty。\n'
                'certainty: confirmed / uncertain / preference。理由引用 reason_evidence_ids。\n'
                '填实际提交时间、是否看完全部页面、盲法/独立性/是否重复评测及实际证据。\n'
                '这是空白真人表单；模型不得代填。只向本评测者发此文件夹，不发 organizer。\n', encoding='utf-8')
            row['reviews'].append({'source_path': str(packet / 'review.json'),
                                   'packet_path': str(packet),
                                   'anonymous_to_manifest': mapping})
        collection['cases'].append(row)
    write_json(private / 'collection.json', collection)
    (private / 'collection.json').chmod(0o600)
    return {'out': str(out), 'collection_path': str(private / 'collection.json'),
            'cases': len(cases), 'reviewer_count': len(reviewers), 'submitted_review_count': 0,
            'warning': 'Distribute only each named reviewer folder; organizer contains unblinding mappings.'}


def _read_review(entry, directory, case, bundle, adjudication=False):
    source = Path(entry['source_path'])
    source = source if source.is_absolute() else directory / source
    actual_hash = file_digest(source)
    if entry.get('source_sha256') is not None and actual_hash != entry['source_sha256']:
        raise ValueError('Submitted human source hash mismatch')
    record = read_json(source)
    expected = {candidate['id'] for candidate in case['candidates']}
    mapping = entry.get('anonymous_to_manifest')
    if (not isinstance(mapping, dict) or len(mapping) != 2 or set(mapping.values()) != expected
            or any(not _text(key) for key in mapping)):
        raise ValueError('Explicit bijective anonymous candidate mapping required')
    packet_path = entry.get('packet_path')
    if not _text(packet_path):
        raise ValueError('A submitted review must identify its static packet directory')
    packet = Path(packet_path)
    packet = packet if packet.is_absolute() else directory / packet
    if (packet / 'task.txt').read_text(encoding='utf-8') != case['prompt']:
        raise ValueError('Human packet task differs from frozen manifest')
    for number, attachment in enumerate(case.get('attachments', []), 1):
        name = f"attachment-{number:03d}{Path(attachment['path']).suffix}"
        if file_digest(packet / name) != attachment['sha256']:
            raise ValueError('Human packet task attachment hash mismatch')
    for alias, cid in mapping.items():
        frozen = bundle['candidates'][cid]
        if file_digest(packet / alias / 'slides.pdf') != frozen['pdf_sha256']:
            raise ValueError('Human packet PDF differs from registered render')
        for page in frozen['pages']:
            if file_digest(packet / alias / f"page-{page['page']:03d}.png") != page['sha256']:
                raise ValueError('Human packet static page differs from registered render')
    if (record.get('schema_version') != 2 or record.get('case_id') != case['id']
            or record.get('actor_kind') != 'human'
            or any(not _text(record.get(key)) for key in ('record_id', 'reviewer_id', 'round_id', 'submitted_at', 'reasons'))
            or type(record.get('viewed_all_pages')) is not bool):
        raise ValueError('Only completed explicitly human review records may be imported')
    try:
        timestamp = datetime.fromisoformat(record['submitted_at'].replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Human submission time must be an ISO 8601 timestamp with timezone') from None
    if timestamp.tzinfo is None:
        raise ValueError('Human submission time must be an ISO 8601 timestamp with timezone')
    if record.get('render_bundle_sha256') != bundle['sha256']:
        raise ValueError('Reviewer used a different render bundle')
    choice, preferred = record.get('choice'), record.get('preferred_candidate')
    if choice == 'better':
        if preferred not in mapping:
            raise ValueError('Better choice requires an explicit anonymous candidate')
        winner, status = mapping[preferred], 'decided'
    elif choice in TIE_POLICY and preferred is None:
        winner = 'tie' if TIE_POLICY[choice] == 'tie' else None
        status = 'decided' if winner == 'tie' else 'undecided'
    else:
        raise ValueError('Choose exactly one preregistered human choice; no preference inferred')
    observations, evidence_ids = record.get('evidence_records'), set()
    if not isinstance(observations, list) or not observations:
        raise ValueError('Human review requires explicit evidence records before an overall choice')
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError('Human evidence must be an object')
        eid, alias, page = observation.get('id'), observation.get('candidate'), observation.get('page')
        if (not _text(eid) or eid in evidence_ids or alias not in mapping or type(page) is not int
                or not 1 <= page <= len(bundle['candidates'][mapping[alias]]['pages'])
                or observation.get('certainty') not in {'confirmed', 'uncertain', 'preference'}
                or any(not _text(observation.get(field)) for field in ('observation', 'basis', 'impact'))):
            raise ValueError('Invalid, duplicated, or unlocated human evidence record')
        evidence_ids.add(eid)
    reasons = record.get('reason_evidence_ids')
    if (not isinstance(reasons, list) or not reasons or len(set(reasons)) != len(reasons)
            or any(eid not in evidence_ids for eid in reasons)):
        raise ValueError('Overall reasons must refer to recorded evidence ids')
    reliability = record.get('reliability', {})
    if not isinstance(reliability, dict):
        raise ValueError('Human reliability metadata must be an object')
    _protocol_evidence(reliability)
    if adjudication and not isinstance(record.get('reviewed_record_ids'), list):
        raise ValueError('Explicit adjudication must list reviewed original record ids')
    return {**record, 'winner': winner, 'label_status': status,
            'reviewer_to_manifest': mapping, 'source_path': str(source.resolve()),
            'packet_path': str(packet.resolve()), 'packet_hashes_verified': True,
            'source_sha256': actual_hash, 'role': 'adjudication' if adjudication else 'independent_review'}


def import_review_bundle(manifest, source, split='dev', allow_holdout=False, expected_render_hashes=None):
    """Import all raw reviews, preserving disagreement and optional explicit adjudication.

    Unadjudicated cases remain unlabeled even when all reviewers agree. This
    returns a *new* schema-2 comparison set; it never merges or overwrites history.
    ``expected_render_hashes`` optionally pins the actual automated render bundle.
    Without that independently supplied registry, same-render-with-model is unknown.
    """
    validate_manifest(manifest)
    if split not in {'dev', 'holdout', 'all'}:
        raise ValueError('Invalid split')
    cases = select_cases(manifest, split=split, allow_holdout=allow_holdout)
    source = Path(source).resolve()
    document = read_json(source)
    if (document.get('schema_version') != 2 or document.get('manifest_id') != manifest['id']
            or document.get('tie_policy') != TIE_POLICY or document.get('tie_policy_sha256') != digest(TIE_POLICY)
            or not _text(document.get('protocol_id'))):
        raise ValueError('Human bundle requires schema 2, matching manifest and frozen tie policy')
    selected = {case['id'] for case in cases}
    raw_cases = document.get('cases')
    if not isinstance(raw_cases, list):
        raise ValueError('Human bundle cases must be a list')
    lookup = {}
    for row in raw_cases:
        cid = row.get('case_id')
        if cid not in selected:
            continue
        if cid in lookup:
            raise ValueError('Duplicate selected human case; no silent override')
        lookup[cid] = row
    labels = []
    for case in cases:
        cid = case['id']
        if cid not in lookup:
            continue
        row = lookup[cid]
        expected = {candidate['id']: candidate['sha256'] for candidate in case['candidates']}
        if row.get('candidate_hashes') != expected:
            raise ValueError('Human bundle candidate hashes differ from frozen manifest')
        _check_candidate_files(case)
        bundle = _verify_render_bundle(case, row.get('render_bundle'))
        if expected_render_hashes is not None and expected_render_hashes.get(cid) != bundle['sha256']:
            raise ValueError('Human and automated render bundle hashes differ or registry entry is missing')
        entries = row.get('reviews')
        if not isinstance(entries, list) or len(entries) < 2:
            raise ValueError('Schema-2 collection requires at least two submitted human reviews')
        reviews = [_read_review(entry, source.parent, case, bundle) for entry in entries]
        ids, people = [review['record_id'] for review in reviews], {review['reviewer_id'] for review in reviews}
        if len(ids) != len(set(ids)) or len(people) < 2:
            raise ValueError('Require unique record ids and at least two distinct human reviewers')
        adjudication = None
        if row.get('adjudication') is not None:
            adjudication = _read_review(row['adjudication'], source.parent, case, bundle, adjudication=True)
            if (adjudication['record_id'] in ids or adjudication['reviewer_id'] in people
                    or len(adjudication['reviewed_record_ids']) != len(ids)
                    or set(adjudication['reviewed_record_ids']) != set(ids)):
                raise ValueError('Third-party adjudication must explicitly cover every original review')
        protocol = {}
        for field in ('blindness', 'independent_review', 'repeat_review'):
            states = [_protocol_evidence(review.get('reliability', {}))[field] for review in reviews]
            statuses = {state['status'] for state in states}
            if len(statuses) == 1 and 'unknown' not in statuses:
                protocol[field] = {'status': states[0]['status'],
                                   'evidence': [e for state in states for e in state['evidence']]}
        protocol['same_render'] = {'status': 'matched' if expected_render_hashes is not None else 'unknown',
                                   'evidence': [f'Verified model render registry: {bundle["sha256"]}']
                                   if expected_render_hashes is not None else []}
        protocol['tie_semantics'] = 'quality_equivalent=tie; tradeoff_unresolved and insufficient_evidence=undecided'
        labels.append({'case_id': cid,
                       'winner': adjudication['winner'] if adjudication else None,
                       'label_status': adjudication['label_status'] if adjudication else 'unlabeled',
                       'reasons': adjudication['reasons'] if adjudication else 'Awaiting explicit human adjudication.',
                       'candidate_hashes': expected, 'review_records': reviews,
                       'adjudication': adjudication, 'reliability': protocol,
                       'render_bundle_sha256': bundle['sha256'], 'same_render_among_reviewers_verified': True,
                       'source_path': str(source), 'source_sha256': file_digest(source),
                       'reviewer_to_manifest': adjudication['reviewer_to_manifest'] if adjudication else {},
                       'reasons_candidate_ids': 'raw anonymous ids; use each record reviewer_to_manifest',
                       'raw_preferences': [{'record_id': r['record_id'], 'reviewer_id': r['reviewer_id'],
                                            'choice': r['choice'], 'winner': r['winner'], 'label_status': r['label_status']}
                                           for r in reviews]})
    return {'schema_version': 2, 'manifest_id': manifest['id'], 'split': split,
            'protocol_id': document['protocol_id'], 'tie_policy': TIE_POLICY, 'labels': labels,
            'decided': sum(label['label_status'] == 'decided' for label in labels),
            'undecided': [label['case_id'] for label in labels if label['label_status'] == 'undecided'],
            'awaiting_adjudication': [label['case_id'] for label in labels if label['adjudication'] is None],
            'policy': 'New human collection, separate from history; no inferred consensus or majority; '
                      'human identity, viewing, blindness and independence declarations are not independently proven.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'import'):
        command = sub.add_parser(name)
        command.add_argument('--manifest', required=True)
        command.add_argument('--out', required=True)
        command.add_argument('--split', choices=['dev', 'holdout', 'all'], default='dev')
        command.add_argument('--allow-holdout', action='store_true')
        if name == 'prepare':
            command.add_argument('--prepared', required=True, help='Existing prepare output; no new render or API')
            command.add_argument('--reviewers', required=True, help='At least two comma-separated anonymous human ids')
            command.add_argument('--seed', type=int, required=True)
        else:
            command.add_argument('--source', required=True, help='Organizer collection JSON, completed reviews referenced')
            command.add_argument('--model-render-hashes', help='Optional independently pinned case-to-render-bundle hash JSON')
    args = parser.parse_args(argv)
    manifest = read_json(args.manifest)
    if args.command == 'prepare':
        result = prepare_review_packets(manifest, read_json(args.prepared), args.out,
                                        args.reviewers.split(','), args.seed, args.split, args.allow_holdout)
    else:
        result = import_review_bundle(manifest, args.source, args.split, args.allow_holdout,
                                       read_json(args.model_render_hashes) if args.model_render_hashes else None)
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        result = {'out': str(output), 'decided': result['decided'],
                  'awaiting_adjudication': result['awaiting_adjudication']}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
