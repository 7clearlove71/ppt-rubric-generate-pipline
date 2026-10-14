"""Offline, comparison-only reporting for the final contract experiment."""
from __future__ import annotations

import argparse
import itertools
import json
import math
from collections import Counter
from pathlib import Path

from .common import digest, read_json, write_json
from .data import select_cases, validate_manifest
from .evaluation import agreement
from .label_reliability import analyze_label_reliability
from .rubric import DIMENSIONS


EPSILON = 1e-9  # Score-point tolerance for ordinary floating-point aggregation.


def _finite(value):
    return type(value) in {int, float} and math.isfinite(value)


def _interval(summary):
    low, high = summary.get('score_lower'), summary.get('score_upper')
    if (not _finite(low) or not _finite(high) or low < -EPSILON or high > 100 + EPSILON
            or low > high):
        raise ValueError('Score interval must have finite ordered bounds within 0..100')
    return max(0, min(100, low)), max(0, min(100, high))


def _validate_summary(summary):
    low, high = _interval(summary)
    status, score = summary.get('status'), summary.get('score')
    unresolved = summary.get('unresolved_weight')
    if (status not in {'complete', 'incomplete'} or not _finite(unresolved) or unresolved < 0
            or not math.isclose(high - low, unresolved, rel_tol=0, abs_tol=EPSILON)):
        raise ValueError('Score completion and unresolved weight disagree with the interval')
    if status == 'complete':
        if (unresolved != 0 or not _finite(score)
                or not math.isclose(score, low, rel_tol=0, abs_tol=EPSILON)):
            raise ValueError('A complete score must equal its resolved interval')
    elif score is not None or unresolved <= 0:
        raise ValueError('An incomplete assessment must retain a nonempty score interval')


def interval_preference(a, b, margin=3):
    """A robust result must hold for every completion of the unresolved items."""
    if not _finite(margin) or margin < 0:
        raise ValueError('Tie margin must be finite and nonnegative')
    al, au = _interval(a)
    bl, bu = _interval(b)
    low, high = al - bu, au - bl
    prediction = ('A' if low > margin + EPSILON else 'B' if high < -margin - EPSILON
                  else 'tie' if low >= -margin - EPSILON and high <= margin + EPSILON else None)
    return {'prediction': prediction, 'difference_interval': [low, high], 'tie_margin': margin,
            'floating_point_tolerance': EPSILON,
            'status': 'robust_to_unresolved_items' if prediction else 'unresolved'}


def _candidate_preference(a, b, candidate_ids, margin=3):
    """Translate ordered pair positions back into verified manifest identifiers."""
    result = interval_preference(a, b, margin)
    result['prediction'] = {'A': candidate_ids[0], 'B': candidate_ids[1]}.get(
        result['prediction'], result['prediction'])
    result['candidate_order'] = list(candidate_ids)
    return result


def weight_sensitivity(a, b, weights, margin=3):
    """Predeclared ±20% dimension perturbations, not a search for matching labels."""
    interval_preference(a, b, margin)  # Validate intervals and the frozen margin.
    if (not isinstance(weights, dict) or set(weights) != DIMENSIONS
            or any(not _finite(w) or w <= 0 for w in weights.values())
            or not math.isclose(sum(weights.values()), 100, abs_tol=EPSILON)):
        raise ValueError('Sensitivity requires exactly five positive dimension budgets summing to 100')
    dims = sorted(weights)
    for summary in (a, b):
        lower = unresolved = 0
        if not isinstance(summary.get('dimensions'), dict) or set(summary['dimensions']) != DIMENSIONS:
            raise ValueError('Summary must cover all five dimensions')
        for dim in dims:
            row = summary['dimensions'][dim]
            values = [row.get(k) for k in ('earned', 'possible', 'unresolved')]
            if (any(not _finite(v) for v in values) or values[0] < -EPSILON or values[2] < -EPSILON
                    or not math.isclose(values[1], weights[dim], abs_tol=EPSILON)
                    or values[0] + values[2] > values[1] + EPSILON):
                raise ValueError('Dimension results must preserve the approved scoring budgets')
            lower += values[0]
            unresolved += values[2]
        if (not math.isclose(lower, summary['score_lower'], abs_tol=EPSILON)
                or not math.isclose(lower + unresolved, summary['score_upper'], abs_tol=EPSILON)):
            raise ValueError('Dimension results disagree with the reported score interval')
    outcomes = Counter()
    for factors in itertools.product((.8, 1.2), repeat=len(dims)):
        modified = {d: weights[d] * factor for d, factor in zip(dims, factors)}
        total = sum(modified.values())
        modified = {d: w * 100 / total for d, w in modified.items()}
        scores = []
        for summary in (a, b):
            lower = upper = 0
            for dim in dims:
                row = summary['dimensions'][dim]
                lower += modified[dim] * row['earned'] / row['possible']
                upper += modified[dim] * (row['earned'] + row['unresolved']) / row['possible']
            scores.append({'score_lower': lower, 'score_upper': upper})
        outcomes[interval_preference(*scores, margin)['prediction'] or 'unresolved'] += 1
    return {'policy': 'All 32 corners of independently multiplied 0.8/1.2 dimension weights, renormalized to 100',
            'corner_count': 32, 'factors': [.8, 1.2], 'tie_margin': margin,
            'outcomes': dict(outcomes), 'stable': len(outcomes) == 1 and 'unresolved' not in outcomes}


def accounting(root):
    # These are the sole live journals. Historical copied snapshots never count twice.
    paths = [Path(root) / stage / provider / 'requests.json'
             for stage in ('rubrics', 'judgments', 'local-regression', 'holdout-rubrics', 'holdout-judgments')
             for provider in ('primary-api', 'verification-api')]
    totals, journals, seen, duplicates = {}, [], {}, []
    for path in paths:
        if not path.exists():
            continue
        requests = read_json(path)
        if not isinstance(requests, list) or any(not isinstance(row, dict) for row in requests):
            raise ValueError('Request journals must contain an array of attempt records')
        journal = {'path': str(path), 'requests': len(requests), 'unique_requests': 0}
        journals.append(journal)
        for row in requests:
            model, status = row.get('model'), row.get('status')
            if not isinstance(model, str) or not model or status not in {'started', 'success', 'failed'}:
                raise ValueError('Every attempt needs a model and a supported request status')
            invocation, request_id = row.get('invocation_id'), row.get('request_id')
            if not isinstance(invocation, str) or not invocation or type(request_id) is not int or request_id < 1:
                raise ValueError('Every attempt needs its invocation_id and positive request_id for deduplication')
            identity = (invocation, request_id)
            if identity in seen:
                if seen[identity]['record'] != row:
                    raise ValueError('Conflicting copies of the same request attempt; reconcile the authoritative journal')
                duplicates.append({'path': str(path), 'original_path': seen[identity]['path'],
                                   'invocation_id': invocation, 'request_id': request_id})
                continue
            seen[identity] = {'path': str(path), 'record': row}
            journal['unique_requests'] += 1
            record = totals.setdefault(model, {'requests': 0, 'statuses': Counter(), 'reported_tokens': 0,
                                               'missing_usage': 0, 'failure_stages': Counter()})
            record['requests'] += 1
            record['statuses'][status] += 1
            if row.get('failure_stage'):
                record['failure_stages'][row['failure_stage']] += 1
            usage = row.get('usage')
            if isinstance(usage, dict) and _finite(usage.get('total_tokens')) and usage['total_tokens'] >= 0:
                record['reported_tokens'] += usage['total_tokens']
            else:
                record['missing_usage'] += 1
    return {'models': totals, 'journals': journals, 'excluded_duplicate_attempts': duplicates,
            'total_unique_requests': len(seen), 'cost_estimate': None,
            'missing_usage_policy': 'Unknown, never zero. A started entry counts as an actual attempt; audit interruption separately.'}


def source_semantic_qa(root, manifest, rows, suites, split):
    """Attach separate Codex audit evidence without changing scores or coverage."""
    path = Path(root) / 'final-source-semantic-qa.json'
    case_ids = [row['case_id'] for row in rows]
    limitations = [
        'This is a Codex source-semantic review, not new human annotation or factual ground truth.',
        'No confirmed counterexample does not establish exhaustive semantic correctness.',
        'QA findings never alter automatic approval, scores, case selection, or agreement denominators.',
    ]
    result = {'status': 'missing', 'source_file': str(path), 'source_digest': None,
              'evidence_kind': 'codex_source_semantic_review', 'audit_status': None,
              'limitations': limitations, 'cases': [
                  {'case_id': cid, 'audit_status': 'not_reviewed',
                   'rubric_hash': suites[cid]['rubric_hash'] if cid in suites else None,
                   'rubric_hash_verified': False, 'findings': []} for cid in case_ids]}
    reviewed_states = {'reviewed_no_confirmed_material_counterexample',
                       'reviewed_with_confirmed_material_findings'}
    if path.exists():
        document = read_json(path)
        if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
                or document['schema_version'] != 1
                or document.get('audit_status') not in {'complete', 'in_progress'}):
            raise ValueError('Source semantic QA has an unsupported schema or audit status')
        targets, records = document.get('target_cases'), document.get('cases')
        if (not isinstance(targets, list) or not targets
                or any(not isinstance(cid, str) or not cid for cid in targets)
                or len(set(targets)) != len(targets)):
            raise ValueError('Source semantic QA target case IDs must be nonempty and unique')
        known_cases = {case['id']: case['split'] for case in manifest['cases']}
        if set(targets) - set(known_cases):
            raise ValueError('Source semantic QA refers to a nonexistent case')
        target_splits = {known_cases[cid] for cid in targets}
        if len(target_splits) != 1:
            raise ValueError('Source semantic QA cannot mix development and holdout cases')
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ValueError('Source semantic QA cases must be an array of records')
        lookup, finding_ids = {}, set()
        for record in records:
            cid = record.get('case_id')
            if not isinstance(cid, str) or cid not in targets:
                raise ValueError('Source semantic QA case must belong to its declared targets')
            if cid in lookup:
                raise ValueError('Source semantic QA has duplicate case IDs')
            status, findings = record.get('audit_status'), record.get('findings')
            if status not in reviewed_states | {'pending', 'in_progress', 'not_reviewed'}:
                raise ValueError('Source semantic QA case has an unsupported audit status')
            if not isinstance(findings, list) or any(not isinstance(finding, dict) for finding in findings):
                raise ValueError('Source semantic QA findings must be an array of records')
            for finding in findings:
                fid = finding.get('id')
                if not isinstance(fid, str) or not fid or fid in finding_ids:
                    raise ValueError('Source semantic QA finding IDs must be nonempty and unique')
                finding_ids.add(fid)
                if any(not isinstance(finding.get(key), str) or not finding[key] for key in ('severity', 'type')):
                    raise ValueError('Source semantic QA finding requires severity and type')
            material = sum(finding['severity'] == 'material' for finding in findings)
            if ((status == 'reviewed_with_confirmed_material_findings' and not material)
                    or (status == 'reviewed_no_confirmed_material_counterexample' and material)):
                raise ValueError('Source semantic QA case status contradicts its material findings')
            lookup[cid] = record
        if (document['audit_status'] == 'complete'
                and (set(lookup) != set(targets)
                     or any(record['audit_status'] not in reviewed_states for record in records))):
            raise ValueError('Complete source semantic QA must finish every declared target case')
        source_limits = document.get('limitations', [])
        if not isinstance(source_limits, list) or any(not isinstance(note, str) for note in source_limits):
            raise ValueError('Source semantic QA limitations must be an array of strings')
        result.update(source_digest=digest(document), audit_status=document['audit_status'],
                      target_cases=targets, target_version=document.get('target_version'),
                      method=document.get('method'), scope=document.get('scope'),
                      limitations=limitations + source_limits)
        if target_splits != {split}:
            # Do not open another split's rubrics to attach an irrelevant audit.
            result['status'] = 'not_applicable'
            result['limitations'].append('This QA covers another split; no case in this report is reviewed by it.')
        else:
            target_version = document.get('target_version')
            if not isinstance(target_version, str) or not target_version:
                raise ValueError('Source semantic QA must identify its target version')
            verified_bindings = set()
            for cid, record in lookup.items():
                if (record['audit_status'] not in reviewed_states
                        and record.get('rubric_hash') is None and not record['findings']):
                    if record.get('prompt_version') not in (None, target_version):
                        raise ValueError('Source semantic QA version differs from its target version')
                    continue  # An explicit pending placeholder has no reviewed artifact to bind.
                suite = suites.get(cid)
                if suite is None:
                    raise ValueError('Source semantic QA case has no current rubric artifact')
                if record.get('rubric_hash') != suite['rubric_hash']:
                    raise ValueError('Source semantic QA rubric hash differs from the current rubric')
                if (record.get('prompt_version') != target_version
                        or suite.get('provenance', {}).get('prompt_version') != target_version):
                    raise ValueError('Source semantic QA version differs from the current rubric')
                verified_bindings.add(cid)
            for case in result['cases']:
                record = lookup.get(case['case_id'])
                if record is None:
                    continue
                case.update(audit_status=record['audit_status'],
                            rubric_hash=record.get('rubric_hash') or case['rubric_hash'],
                            rubric_hash_verified=case['case_id'] in verified_bindings, findings=[
                                {key: finding[key] for key in ('id', 'type', 'severity', 'criteria',
                                    'consequence', 'qualification', 'counterexample_probe') if key in finding}
                                for finding in record['findings']])
            result['status'] = ('complete' if document['audit_status'] == 'complete'
                                and set(targets) == set(case_ids) else 'partial')
    reviewed = [case for case in result['cases'] if case['audit_status'] in reviewed_states]
    with_findings = [case['case_id'] for case in reviewed
                     if case['audit_status'] == 'reviewed_with_confirmed_material_findings']
    result['summary'] = {
        'report_cases': len(case_ids), 'reviewed_cases': len(reviewed),
        'coverage': len(reviewed) / len(case_ids) if case_ids else None,
        'cases_with_confirmed_material_findings': len(with_findings),
        'case_ids_with_confirmed_material_findings': with_findings,
        'confirmed_material_findings': sum(finding['severity'] == 'material'
                                           for case in reviewed for finding in case['findings']),
        'cases_without_confirmed_counterexample': [case['case_id'] for case in reviewed
            if case['audit_status'] == 'reviewed_no_confirmed_material_counterexample'],
        'unreviewed_cases': [case['case_id'] for case in result['cases']
                            if case['audit_status'] not in reviewed_states],
        'conclusion_status': 'final_for_declared_scope' if result['status'] == 'complete' else 'not_final',
    }
    if result['status'] == 'partial':
        result['limitations'].append('This QA is partial or in progress; its findings are not a completed review conclusion.')
    return result


def summarize(manifest, root, labels=None, split='dev', allow_holdout=False):
    validate_manifest(manifest)
    if split not in {'dev', 'holdout'}:
        raise ValueError('Report dev and holdout separately; do not mix their artifact directories')
    root = Path(root)
    cases = select_cases(manifest, split=split, allow_holdout=allow_holdout)
    rubric_root = root / ('holdout-rubrics' if split == 'holdout' else 'rubrics')
    judgment_root = root / ('holdout-judgments' if split == 'holdout' else 'judgments')
    rows, counts, suites = [], Counter(), {}
    item_agreement = Counter()
    for case in cases:
        cid = case['id']
        row = {'case_id': cid, 'rubric_status': 'missing', 'candidates': {}}
        path = rubric_root / cid / 'rubric.json'
        suite = read_json(path) if path.exists() else None
        if suite:
            if (suite.get('case_id') != cid or suite.get('evaluation_protocol') != 'contract_v2'
                    or suite.get('rubric_hash') != digest({k: v for k, v in suite.items() if k != 'rubric_hash'})):
                raise ValueError('Rubric hash, case, or protocol is invalid')
            row.update(rubric_status=suite['status'], rubric_items=len(suite['items']), rubric_hash=suite['rubric_hash'])
            suites[cid] = suite
        elif (rubric_root / cid / 'status.json').exists():
            row['rubric_status'] = read_json(rubric_root / cid / 'status.json')['status']
        counts['rubrics_approved'] += row['rubric_status'] == 'approved'
        reports = {}
        for candidate in case['candidates']:
            path = judgment_root / cid / f'{candidate["id"]}.json'
            if not path.exists():
                continue
            report = read_json(path)
            if candidate['sha256'] != report['candidate_hash'] or report.get('candidate_id') != candidate['id'] or report.get('case_id') != cid:
                raise ValueError('Candidate hash differs from manifest')
            if (not suite or suite['status'] != 'approved' or report.get('rubric_hash') != suite['rubric_hash']
                    or report.get('source_hash') != suite.get('provenance', {}).get('source_hash')
                    or report.get('evaluation_protocol') != 'contract_v2'):
                raise ValueError('Evaluation does not belong to this approved rubric/source/protocol')
            _validate_summary(report['summary'])
            _validate_summary(report['primary']['summary'])
            reports[candidate['id']] = report
            counts['primary_complete'] += report['primary']['summary']['status'] == 'complete'
            counts['combined_complete'] += report['summary']['status'] == 'complete'
            independent = set(report.get('consensus', {}).get('independently_evaluated_items', []))
            nonvisual = {item['id'] for item in suite['items'] if item['dimension'] != 'visual'}
            for verdict in report['verdicts']:
                if verdict['rubric_id'] in independent & nonvisual:
                    item_agreement[verdict['assessment_status']] += 1
            local = {item['id']: Counter(v['answer'] for p in report['primary']['visual_pages']
                                        for v in p['verdicts'] if v['rubric_id'] == item['id'])
                     for item in suite['items'] if item['dimension'] == 'visual'} if suite else {}
            row['candidates'][candidate['id']] = {'primary': report['primary']['summary'],
                'combined': report['summary'], 'visual_page_answers': local,
                'ownership_conflicts': report['ownership_conflicts']}
        expected_candidates = {candidate['id'] for candidate in case['candidates']}
        if len(reports) == 2 and set(reports) == expected_candidates:
            candidate_ids = sorted(reports)
            a, b = (reports[c] for c in candidate_ids)
            if any(a.get(key) != b.get(key) for key in ('rubric_hash', 'config_hash', 'judge_model',
                    'verification_model', 'judge_validator_version', 'judge_prompt_fingerprint', 'evaluation_protocol')):
                raise ValueError('Cannot compare mismatched candidate evaluations')
            row['combined_interval_preference'] = _candidate_preference(a['summary'], b['summary'], candidate_ids)
            row['primary_preference'] = _candidate_preference(a['primary']['summary'], b['primary']['summary'], candidate_ids)
            if suite:
                row['weight_sensitivity'] = weight_sensitivity(a['summary'], b['summary'], suite['dimension_weights'])
        rows.append(row)
    result = {'split': split, 'cases': rows, 'counts': dict(counts), 'total_cases': len(cases),
              'total_decks': sum(len(c['candidates']) for c in cases),
              'independent_content_answers': dict(item_agreement), 'accounting': accounting(root),
              'interpretation': ['Primary score is one model judgment, not verified truth.',
                 'Combined scores retain independent model disagreements as intervals; local visuals may be singly assessed.',
                 'Robust interval preference is not a complete score, and partial coverage is reported explicitly.',
                 'Human preference is a comparison target, not the oracle for factual correctness.']}
    result['source_semantic_qa'] = source_semantic_qa(root, manifest, rows, suites, split)
    if labels is not None:
        result['human_reliability'] = analyze_label_reliability(manifest, labels, split=split, allow_holdout=allow_holdout)
        result['complete_score_agreement'] = agreement(manifest, labels, judgment_root, split=split)
        lookup = {r['case_id']: r for r in labels['labels']}
        comparable, matches = 0, 0
        single_rows, single_skipped, single_labels = [], [], Counter()
        decided_labels = 0
        for row in rows:
            label = lookup.get(row['case_id'])
            if not label or label.get('label_status') == 'undecided' or not label.get('winner'):
                continue
            expected = {c['id']: c['sha256'] for c in next(c for c in cases if c['id'] == row['case_id'])['candidates']}
            if label.get('candidate_hashes') != expected:
                raise ValueError('Human label candidate identity mismatch')
            decided_labels += 1
            prediction = row.get('combined_interval_preference', {}).get('prediction')
            row['human_label'] = label['winner']
            row['robust_interval_match'] = prediction == label['winner'] if prediction else None
            comparable += bool(prediction)
            matches += bool(prediction) and prediction == label['winner']
            primary = {cid: candidate['primary'] for cid, candidate in row['candidates'].items()}
            if len(expected) != 2:
                raise ValueError('Pairwise single-assessor comparison expects two candidates')
            if set(primary) != set(expected):
                single_skipped.append({'case_id': row['case_id'], 'reason': 'missing primary candidate assessment'})
                continue
            if any(s['status'] != 'complete' or s['score'] is None for s in primary.values()):
                single_skipped.append({'case_id': row['case_id'], 'reason': 'incomplete primary score'})
                continue
            ordered = sorted(expected)
            single = _candidate_preference(primary[ordered[0]], primary[ordered[1]], ordered)
            single_prediction = single['prediction']
            row['single_assessor_match'] = single_prediction == label['winner']
            single_rows.append({'case_id': row['case_id'], 'candidate_hashes': expected,
                                'scores': {cid: primary[cid]['score'] for cid in ordered},
                                'prediction': single_prediction, 'human': label['winner'],
                                'match': row['single_assessor_match']})
            single_labels[label['winner']] += 1
        result['robust_interval_agreement'] = {'n': comparable, 'matches': matches,
            'agreement': matches / comparable if comparable else None,
            'meaning': 'Separate descriptive interval comparison; not interchangeable with complete-score agreement.'}
        single_n = len(single_rows)
        single_matches = sum(row['match'] for row in single_rows)
        majority_count = max(single_labels.values(), default=0)
        result['single_assessor_agreement'] = {
            'n': single_n, 'matches': single_matches,
            'agreement': single_matches / single_n if single_n else None,
            'decided_labels': decided_labels, 'coverage': single_n / decided_labels if decided_labels else None,
            'tie_margin': 3, 'assessment': 'primary_only_not_independently_verified',
            'meaning': 'Descriptive comparison using complete primary scores only; this is not independent verification.',
            'cases': single_rows, 'skipped': single_skipped,
            'majority_label_baseline': {
                'n': single_n, 'label_counts': dict(single_labels),
                'majority_labels': sorted(k for k, count in single_labels.items() if count == majority_count),
                'matches': majority_count, 'agreement': majority_count / single_n if single_n else None,
                'meaning': 'Label frequency on exactly this comparable subset; not out-of-sample accuracy or a tuning target.'}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--manifest', default='data/manifest.json')
    parser.add_argument('--labels')
    parser.add_argument('--split', choices=['dev', 'holdout'], default='dev')
    parser.add_argument('--allow-holdout', action='store_true')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = summarize(read_json(args.manifest), args.run, read_json(args.labels) if args.labels else None,
                       args.split, args.allow_holdout)
    write_json(args.out, result)
    print(json.dumps({'counts': result['counts'], 'accounting': result['accounting']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
