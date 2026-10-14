"""Necessary judge-output checks; these do not prove evidence is correct."""
import re

JUDGE_VALIDATOR_VERSION = 9


def validate_judge_verdict(result, page_count, visual_checks=None):
    if not isinstance(result, dict):
        raise ValueError('Invalid judge verdict')
    answer = result.get('answer')
    evidence = result.get('evidence')
    if answer not in {'yes', 'no', 'unknown'} or not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('Invalid judge verdict')
    pages = result.get('pages')
    if not isinstance(pages, list) or any(type(p) is not int or p < 1 or p > page_count for p in pages):
        raise ValueError('Judge cited invalid page numbers')
    if answer in {'yes', 'no'} and not pages:
        raise ValueError('Decisive verdict requires localized evidence')
    # Only an explicit terminal declaration is checked. Negations, quoted slide
    # text and ordinary descriptions of defects are not inferred verdicts.
    conclusion = re.search(r'(?:本条|最终)(?:判(?:定)?|答案)(?:为)?\s*'
                           r'(yes|no|unknown)\s*[。.!！]?\s*$', evidence, re.I)
    if conclusion and conclusion[1].lower() != answer:
        raise ValueError('Judge answer contradicts explicit terminal conclusion')
    if visual_checks is not None:
        concerns = {c['page']: c for c in visual_checks if c['status'] in {'fail', 'unknown'}}
        resolutions = result.get('visual_resolutions', [])
        if (not isinstance(resolutions, list)
                or any(not isinstance(r, dict) or type(r.get('page')) is not int for r in resolutions)
                or len(resolutions) != len(concerns)
                or {r['page'] for r in resolutions} != set(concerns)):
            raise ValueError(f'Judge must resolve every visual concern exactly once; expected pages {sorted(concerns)}')
        for r in resolutions:
            if r.get('decision') not in {'uphold', 'dispute'} or not isinstance(r.get('evidence'), str) or not r['evidence'].strip():
                raise ValueError('Visual resolution requires a decision and evidence')
            if r['decision'] == 'uphold':
                status = concerns[r['page']]['status']
                if (status == 'fail' and answer != 'no') or (status == 'unknown' and answer == 'yes'):
                    raise ValueError('Judge answer contradicts upheld visual concern')


def validate_visual_page(result, page, rubric_ids):
    if not isinstance(result, dict) or type(result.get('page')) is not int or result['page'] != page:
        raise ValueError('Visual check must preserve original page number')
    checks = result.get('checks')
    if (not isinstance(checks, list) or any(not isinstance(c, dict) or not isinstance(c.get('rubric_id'), str) for c in checks)
            or len(checks) != len(rubric_ids) or {c['rubric_id'] for c in checks} != set(rubric_ids)):
        raise ValueError('Visual check must cover each supplied rubric exactly once')
    for check in checks:
        if (check.get('status') not in {'pass', 'fail', 'not_applicable', 'unknown'}
                or not isinstance(check.get('evidence'), str) or not check['evidence'].strip()):
            raise ValueError('Visual check requires status and localized evidence')
