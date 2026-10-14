"""Necessary judge-output checks; these do not prove evidence is correct."""
import re

JUDGE_VALIDATOR_VERSION = 2


def validate_judge_verdict(result, page_count):
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
