"""Describe human-label provenance for comparison; never adjudicate preferences.

This module reads only the supplied in-memory structures. It never opens review
paths, calls a model, or changes a canonical winner. Optional ``review_records``
are provenance records, not replacement votes. Optional ``reliability`` fields
use ``{status: ..., evidence: [nonempty descriptions or artifact references]}``.
Evidence is reported as documentation, not independently verified truth.
"""
from __future__ import annotations

from collections.abc import Mapping

from .data import select_cases, validate_manifest


_PROTOCOL_STATUSES = {
    'blindness': {'blinded', 'nonblind', 'partial', 'unknown'},
    'independent_review': {'independent', 'not_independent', 'unknown'},
    'repeat_review': {'repeated', 'not_repeated', 'unknown'},
    'same_render': {'matched', 'different', 'unknown'},
}


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _source(record):
    return {
        'path': _text(record.get('source_path')),
        'sha256': _text(record.get('source_sha256')),
    }


def _protocol_evidence(metadata):
    result = {}
    for field, allowed in _PROTOCOL_STATUSES.items():
        supplied = metadata.get(field)
        if supplied is None:
            result[field] = {'status': 'unknown', 'documented': False, 'evidence': []}
            continue
        if not isinstance(supplied, Mapping) or supplied.get('status') not in allowed:
            raise ValueError(f'Invalid reliability metadata: {field}')
        evidence = supplied.get('evidence', [])
        if not isinstance(evidence, list) or any(not _text(e) for e in evidence):
            raise ValueError(f'{field} evidence must be a list of nonempty strings')
        claimed = supplied['status']
        documented = claimed != 'unknown' and bool(evidence)
        result[field] = {
            'status': claimed if documented else 'unknown',
            'claimed_status': claimed,
            'documented': documented,
            'evidence': list(evidence),
        }
    return result


def analyze_label_reliability(manifest, labels, split='dev', allow_holdout=False):
    """Return structural facts/unknowns about selected canonical human labels.

    ``review_records`` may contain ``reviewer_id`` (or ``reviewer``),
    ``source_path`` and ``source_sha256``. If absent, the canonical imported
    record itself is the sole observed provenance record. This counts records
    actually supplied, not unobserved assessors or claimed consensus.

    A documented protocol status requires an explicit status and evidence in
    ``label['reliability']``. Missing metadata and unsupported claims remain
    unknown. No human reliability coefficient is estimated from single labels.
    """
    validate_manifest(manifest)
    if split not in {'dev', 'holdout', 'all'}:
        raise ValueError('Invalid split')
    cases = select_cases(manifest, split=split, allow_holdout=allow_holdout)
    if labels.get('manifest_id') != manifest['id']:
        raise ValueError('Human labels use a different manifest / blind mapping')
    raw_rows = labels.get('labels', [])
    if not isinstance(raw_rows, list):
        raise ValueError('Human labels must be a list')
    selected = {case['id'] for case in cases}
    lookup = {}
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise ValueError('Each human label must be a mapping')
        # Do not access any held-out label content beyond the routing ID.
        cid = row.get('case_id')
        if cid not in selected:
            continue
        if cid in lookup:
            raise ValueError('Duplicate human labels for a selected case')
        lookup[cid] = row

    rows = []
    for case in cases:
        cid = case['id']
        label = lookup.get(cid)
        if label is None:
            rows.append({'case_id': cid, 'canonical_label': None, 'facts': {},
                         'unknowns': ['canonical_label', 'reviewer_count',
                                      'blindness', 'independent_review',
                                      'repeat_review', 'same_render', 'tie_semantics']})
            continue
        expected = {c['id']: c['sha256'] for c in case['candidates']}
        if label.get('candidate_hashes') != expected:
            raise ValueError(f'Case {cid}: human labels refer to different candidate files')
        winner = label.get('winner')
        status = label.get('label_status')
        if status is None:
            status = 'decided' if winner in {*expected, 'tie'} else 'unlabeled'
        if status not in {'decided', 'undecided', 'unlabeled'}:
            raise ValueError(f'Case {cid}: invalid canonical label status')
        if status == 'decided' and winner not in {*expected, 'tie'}:
            raise ValueError(f'Case {cid}: decided label requires an explicit valid winner')
        if status != 'decided' and winner is not None:
            raise ValueError(f'Case {cid}: nondecided label cannot carry a winner')

        records = label.get('review_records')
        record_basis = 'explicit_review_records'
        if records is None:
            records = [label]
            record_basis = 'canonical_import_record'
        if not isinstance(records, list) or not records or any(not isinstance(r, Mapping) for r in records):
            raise ValueError(f'Case {cid}: review_records must be a nonempty list of mappings')
        provenance = []
        names = set()
        for record in records:
            reviewer = _text(record.get('reviewer_id')) or _text(record.get('reviewer'))
            if reviewer:
                names.add(reviewer)
            provenance.append({'reviewer': reviewer, 'source': _source(record)})
        metadata = label.get('reliability', {})
        if not isinstance(metadata, Mapping):
            raise ValueError(f'Case {cid}: reliability must be a mapping')
        protocol = _protocol_evidence(metadata)
        tie_semantics = _text(metadata.get('tie_semantics'))
        unknowns = [field for field, value in protocol.items() if value['status'] == 'unknown']
        if any(record['reviewer'] is None for record in provenance):
            unknowns.append('reviewer_count')
        if not tie_semantics:
            unknowns.append('tie_semantics')
        unknowns.extend(['unobserved_reviewers', 'evidence_independently_verified'])
        if not all(_source(label).values()):
            unknowns.append('canonical_source_identity')
        rows.append({
            'case_id': cid,
            'canonical_label': {'winner': winner, 'label_status': status},
            'facts': {
                'candidate_hashes_match_manifest': True,
                'canonical_source': _source(label),
                'reviewer_to_manifest': dict(label.get('reviewer_to_manifest') or {}),
                'observed_review_record_count': len(records),
                'observed_named_reviewer_count': len(names),
                'reviewer_count_basis': record_basis,
                'review_records': provenance,
                'protocol_documentation': protocol,
                'tie_semantics': tie_semantics,
                'raw_rating_disagreement': _raw_rating_disagreement(records),
                'explicit_adjudication_present': isinstance(label.get('adjudication'), Mapping),
            },
            'unknowns': unknowns,
        })

    labelled = [row for row in rows if row['canonical_label'] is not None]
    decided = [row for row in labelled if row['canonical_label']['label_status'] == 'decided']
    return {
        'schema_version': 1,
        'manifest_id': manifest['id'],
        'split': split,
        'comparison_only': True,
        'policy': 'Canonical choices preserved; no winner inferred from reasons; '
                  'missing protocol evidence is unknown; evidence references are not opened or verified.',
        'summary': {
            'selected_cases': len(rows),
            'canonical_label_records': len(labelled),
            'effective_decided_cases': len(decided),
            'ties': sum(row['canonical_label']['winner'] == 'tie' for row in decided),
            'undecided': sum(row['canonical_label']['label_status'] == 'undecided' for row in labelled),
            'unlabeled': len(rows) - len(decided) - sum(
                row['canonical_label']['label_status'] == 'undecided' for row in labelled),
            'single_observed_record_cases': sum(
                row['facts']['observed_review_record_count'] == 1 for row in labelled),
            'multiple_named_reviewer_cases': sum(
                row['facts']['observed_named_reviewer_count'] >= 2 for row in labelled),
            'documented_blinded_cases': sum(
                row['facts']['protocol_documentation']['blindness']['status'] == 'blinded'
                for row in labelled),
            'documented_repeated_cases': sum(
                row['facts']['protocol_documentation']['repeat_review']['status'] == 'repeated'
                for row in labelled),
            'inter_rater_reliability': None,
            'inter_rater_reliability_reason': 'Canonical labels and provenance do not establish independent raw repeated ratings.',
            'raw_rating_disagreement_cases': sum(
                row['facts']['raw_rating_disagreement'].get('disagreement') is True for row in labelled),
        },
        'cases': rows,
    }


def _raw_rating_disagreement(records):
    """Describe supplied schema-2 raw choices without voting or changing labels.

    Repeats by one person are kept as records; this summary does not call those
    independent raters or estimate a population reliability coefficient.
    """
    ratings = [record for record in records
               if record.get('schema_version') == 2 and record.get('actor_kind') == 'human'
               and record.get('role') == 'independent_review']
    choices = [(record.get('label_status'), record.get('winner'), record.get('choice'))
               for record in ratings]
    return {'raw_rating_records': len(ratings),
            'disagreement': len(set(choices)) > 1 if len(ratings) >= 2 else None,
            'canonical_choice_inferred': False,
            'meaning': 'Observed raw choice disagreement; no majority vote, independence inference, or adjudication.'}
