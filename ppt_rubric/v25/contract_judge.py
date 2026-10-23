"""Blind document judgments with page-local visual decisions and honest intervals.

Each content call sees one criterion and a complete document, never a competing candidate or a
previous model answer. Each visual call sees one original page, its crops, and all five visual criteria.
One K3 client produces the complete assessment. Raw unknown intervals are kept;
candidate totals share the same subset after cohort-wide unknown exclusions.
"""
from __future__ import annotations

import copy
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..common import digest, read_json, write_json
from ..candidate_mapping import candidate_identity, mapping_rows, write_candidate_mapping
from .comparison_scoring import COMPARISON_POLICY, score_candidates
from .render import rendered_text_geometry
from ..rubric import DIMENSIONS, aggregate
from .source_catalog import source_catalog, resolve_source_ids
from .visual_regions import focus_views
from ..provider import ProviderError
from .judge_batch import compile_batch_prompt, parse_batch_answer
from .recovery import assessment_binding, image_references, request_hash, validate_checkpoint


PROTOCOL = 'contract_v2'
VALIDATOR_VERSION = 10
JUDGMENT_STRATEGY = 'content_single_visual_page_batch'
SCORING_METHOD = 'single_k3'
_RENDER_LOCK = threading.Lock()

from .contract_v2 import VERSION as PIPELINE_VERSION, BASE_VERSION

JUDGE_PROMPT = r"""你是一位评估演示文稿的专家。

一个AI智能体的任务是根据用户任务和提供的背景材料，制作一份完整、专业、逻辑清楚，适合任务所指定受众和使用场景的演示文稿。

你的任务是根据下面给出的要求，评估该智能体生成的幻灯片。生成的幻灯片，以及制作时依据的用户任务和背景材料，都会提供给你。

请先说明生成的幻灯片满足或不满足指定要求的具体依据，尽可能指出具体幻灯片，例如第3页、第5页；然后再回答“yes”或“no”。如果回答“no”，理由须清楚说明为什么不满足。

如果幻灯片介于完全满足与完全不满足之间（即部分满足），你必须判为“no”。只有完全满足指定要求、没有违反该要求的情况，才可以判为“yes”。

请先输出可核查的简短理由，再在回答末尾给出一个明确的最终结论 \boxed{yes} 或 \boxed{no}，并且必须与你的判断一致。只给出一个最终boxed结论；除此之外，不限制回答格式。

仅当提供的材料不足以核验时，回答“unknown”，最终结论写为 \boxed{unknown}。

例如：第3页有不满足本条要求的可见内容，具体原因在此说明。
\boxed{no}

以下是本次检查的要求。
"""

# Content retains the common shell followed by one question. Visual uses the batch contract.
CONTENT_PROMPT = JUDGE_PROMPT
SYSTEM_TEMPLATE = '{common}\n{question}'

CONTENT_INPUT_NOTES = """rendered_text由原始PPTX渲染为PDF后，用PyMuPDF从对应页的文本层提取；不是OCR或模型描述。它仅辅助定位和核对，可能有阅读顺序、换行、字符或遗漏错误，不是额外事实来源。可读性以原始页面图片为依据；提取遗漏不证明原图缺失。
"""

VISUAL_INPUT_NOTES = '局部放大图来自对应原页PDF，图中标注的page为原页码。'


def compile_batch_prompt_template():
    return compile_batch_prompt([{'id': '{rubric_id}', 'judge_prompt': '{question}'}])


def prompt_bundle():
    """The full reusable wrapper, including user-input instructions."""
    return copy.deepcopy({'common': JUDGE_PROMPT, 'content': CONTENT_PROMPT, 'visual': compile_batch_prompt_template(),
                          'system_template': SYSTEM_TEMPLATE,
                          'visual_batch_prompt': compile_batch_prompt_template(),
                          'content_input_notes': CONTENT_INPUT_NOTES, 'visual_input_notes': VISUAL_INPUT_NOTES,
                          'response_mode': 'text', 'boxed_selection': 'first_valid_else_last',
                          'visual_pages_per_request': 1, 'visual_criteria_per_request': 'all',
                          'visual_response_mode': 'strict_json_verdicts', 'judgment_strategy': JUDGMENT_STRATEGY,
                          'scoring_method': SCORING_METHOD})


def _evaluation_context(scope):
    content = scope == 'document'
    return {'evaluation_scope': scope,
            'input_notes': CONTENT_INPUT_NOTES if content else VISUAL_INPUT_NOTES}


def compile_judge_prompt(item):
    return SYSTEM_TEMPLATE.format(common=JUDGE_PROMPT.rstrip(), question=item['judge_prompt'])


def document_id(deck):
    """Identity is content-derived and independent of candidate labels/order."""
    sha = deck.get('identity', {}).get('sha256')
    if not isinstance(sha, str) or not sha:
        raise ValueError('A document needs a content hash')
    return 'doc_' + digest({'sha256': sha})[:24]


def page_catalog(deck):
    doc = document_id(deck)
    pages = []
    seen = set()
    for slide in deck['slides']:
        page = slide['page']
        if type(page) is not int or page < 1 or page in seen:
            raise ValueError('Document pages must have unique positive numbers')
        seen.add(page)
        if not isinstance(slide.get('rendered_text'), str):
            raise ValueError('rendered_text must come from the rendered PDF; re-render this deck before judging')
        pages.append({'page': page, 'page_id': f'{doc}/page/{page}',
                      'rendered_text': slide['rendered_text']})
    if not pages:
        raise ValueError('A document needs at least one original page')
    return pages


def validate_inputs(case, rubric, config, decks, sources, catalog):
    """Reject altered contracts or candidate bindings before any model call."""
    from .contract_v2 import compile_item, verify_suite, VALIDATOR_VERSION as CONTRACT_VALIDATOR_VERSION

    verify_suite(case, rubric, config)

    def safe_component(value):
        return isinstance(value, str) and bool(value) and value not in {'.', '..'} and Path(value).name == value

    if not isinstance(case, dict) or not safe_component(case.get('id')):
        raise ValueError('Case needs a safe nonempty identifier')
    candidates = case.get('candidates')
    if (not isinstance(candidates, list) or not candidates
            or any(not isinstance(c, dict) or not safe_component(c.get('id')) for c in candidates)
            or len({c['id'] for c in candidates}) != len(candidates)):
        raise ValueError('Case needs unique candidate identifiers')
    if not isinstance(decks, dict) or set(decks) != {c['id'] for c in candidates}:
        raise ValueError('Prepared decks must cover exactly the case candidates')
    for candidate in candidates:
        sha = candidate.get('sha256')
        deck = decks[candidate['id']]
        if (not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha)
                or not isinstance(deck, dict) or deck.get('identity', {}).get('sha256') != sha):
            raise ValueError('Prepared candidate hash differs from the frozen case manifest')
        page_catalog(deck)
        if any(not isinstance(s.get('image'), str) or not s['image'] for s in deck['slides']):
            raise ValueError('Every original page needs an image reference')
    if not isinstance(rubric, dict) or rubric.get('status') not in {'approved', 'ready'} or rubric.get('evaluation_protocol') != PROTOCOL:
        raise ValueError('Only a published, ready contract_v2 rubric may be evaluated')
    if rubric.get('rubric_hash') != digest({k: v for k, v in rubric.items() if k != 'rubric_hash'}):
        raise ValueError('Rubric changed after approval')
    provenance = rubric.get('provenance')
    if (not isinstance(provenance, dict) or rubric.get('case_id') != case['id']
            or provenance.get('case_id') != case['id']):
        raise ValueError('Rubric case binding differs from the supplied case')
    if provenance.get('source_hash') != digest(sources):
        raise ValueError('Rubric source hash does not match supplied source materials')
    if rubric.get('source_catalog_hash') != digest(catalog):
        raise ValueError('Rubric source catalog changed after approval')
    if rubric.get('signature') != digest(provenance):
        raise ValueError('Rubric provenance signature is invalid')
    if provenance.get('validator_version') != CONTRACT_VALIDATOR_VERSION:
        raise ValueError('Rubric requires the current contract audit')
    items = rubric.get('items')
    if (not isinstance(items, list) or not items
            or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] for item in items)
            or len({item['id'] for item in items}) != len(items)):
        raise ValueError('Rubric needs a complete list of unique criteria')
    weights = rubric.get('dimension_weights')
    if (not isinstance(weights, dict) or set(weights) != DIMENSIONS
            or any(type(value) not in {int, float} or not math.isfinite(value) or value < 0 for value in weights.values())
            or not math.isclose(sum(weights.values()), 100, abs_tol=1e-8)):
        raise ValueError('Rubric dimension budgets must be finite and sum to 100')
    if 'dimension_weights' in config and config['dimension_weights'] != weights:
        raise ValueError('Evaluation dimension budgets differ from the approved rubric')
    for item in items:
        if (item.get('dimension') not in DIMENSIONS or type(item.get('score')) not in {int, float}
                or not math.isfinite(item['score']) or item['score'] <= 0):
            raise ValueError('Every criterion needs a valid dimension and positive finite score')
        if any(not isinstance(item.get(key), str) or not item[key].strip()
               for key in ('objective', 'scope', 'applicability', 'pass_condition', 'exclusions')):
            raise ValueError('Every criterion needs all canonical contract fields')
        if item.get('judge_prompt') != compile_item(item):
            raise ValueError('Judge prompt differs from the canonical rubric contract')
        if item.get('evidence_mode') not in {'text', 'both', 'visual'} or item.get('scope_type') not in {'deck', 'document', 'page'}:
            raise ValueError('Criterion evidence mode or scope is invalid')
        resolve_source_ids(catalog, item.get('source_ids'))
    for dimension, weight in weights.items():
        if not math.isclose(sum(i['score'] for i in items if i['dimension'] == dimension), weight, abs_tol=1e-8):
            raise ValueError('Criterion scores do not preserve the approved dimension budgets')


def visual_item(item):
    return item['dimension'] == 'visual' or (
        item.get('scope_type') == 'page' and item.get('evidence_mode') == 'visual')


def _criterion(item):
    # The one question is already in system. Only a neutral request ID remains.
    return {'id': item['id']}


# PresentBench's boxed token normalization and first-box/last-box selection:
# https://github.com/PresentBench/PresentBench/blob/main/judge.py
_LATEX_WRAPPER_RE = re.compile(r'\\[a-zA-Z]+\s*')


def parse_boxed_answer(text):
    """Read the PB conclusion from free text; retain the project's unknown token."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Expected nonempty visible response text')
    matches = re.findall(r'\\boxed\{([^}]+)\}', text)
    if not matches:
        raise ValueError('Missing final \\boxed{yes}, \\boxed{no} or \\boxed{unknown}')
    tokens = []
    for match in matches:
        token = _LATEX_WRAPPER_RE.sub('', match.strip())
        token = token.replace('{', '').replace('}', '').strip().strip('.').strip().lower()
        tokens.append(token)
    answer = tokens[0] if tokens[0] in {'yes', 'no', 'unknown'} else tokens[-1]
    if answer not in {'yes', 'no', 'unknown'}:
        raise ValueError('Boxed conclusion must be yes, no or unknown')
    return answer


def _unknown(rid, reason, pages=(), error=None):
    return {'rubric_id': rid, 'answer': 'unknown', 'pages': list(pages),
            'evidence': reason, 'defect': None, 'unknown_origin': 'execution_error',
            'error': copy.deepcopy(error or {})}


def _request(client, system, payload, images, previous=None, rubric_ids=None):
    fingerprint = request_hash(getattr(client, 'config', {}), system, payload, images)
    if previous is not None and previous.get('input_hash') != fingerprint:
        raise ValueError('Recovery request changed; refusing to reuse or submit it')
    if previous is not None and previous['status'] == 'success':
        return copy.deepcopy(previous)
    raw = None
    try:
        parser = (lambda text: parse_batch_answer(text, rubric_ids)) if rubric_ids else parse_boxed_answer
        raw = client.ask_text('judge', system, payload, images, validate=parser)
        parsed = parser(raw)  # Alternate clients follow the same parser.
        unknown = any(row['answer'] == 'unknown' for row in parsed) if rubric_ids else parsed == 'unknown'
        record = {'status': 'success', 'raw': raw,
                  **({'verdicts': parsed} if rubric_ids else {'answer': parsed}),
                  'outcome_kind': 'model_unknown' if unknown else 'judgment'}
    except Exception as error:
        record = {'status': 'unavailable', 'error_type': type(error).__name__,
                  'outcome_kind': 'execution_error'}
        if isinstance(error, ProviderError):
            record['error'] = copy.deepcopy(error.details)
        if raw is not None:
            record['raw'] = copy.deepcopy(raw)
    record['input_hash'] = fingerprint
    if previous is not None:
        record['recovery_history'] = copy.deepcopy(previous.get('recovery_history', [])) + [
            {k: copy.deepcopy(v) for k, v in previous.items() if k != 'recovery_history'}]
    return record


def _unavailable_reason(record):
    error = record.get('error', {})
    status = error.get('http_status')
    if status:
        return 'unknown：HTTP' + str(status) + ('；共享熔断，本项未发送请求' if error.get('circuit_open') else '')
    return 'Judgment unavailable: ' + error.get('error_category', record['error_type'])


def _verdict(record, rubric_id, pages=()):
    # IDs and inspected visual pages are known request metadata. Preserve the
    # complete visible response; do not invent structured content citations.
    result = {'rubric_id': rubric_id, 'answer': record['answer'], 'pages': list(pages),
              'evidence': record['raw'], 'defect': None}
    if record['answer'] == 'unknown':
        result['unknown_origin'] = 'model_insufficient_evidence'
    return result


def aggregate_visual(items, page_records):
    """An observed local failure survives any unknown or passing other page."""
    result = []
    for item in items:
        rows = [next(v for v in page['verdicts'] if v['rubric_id'] == item['id']) for page in page_records]
        failures = [row for row in rows if row['answer'] == 'no']
        pending = [row for row in rows if row['answer'] == 'unknown']
        answer = 'no' if failures else ('unknown' if pending or not rows else 'yes')
        decisive = failures or pending or rows
        pages = sorted({p for row in decisive for p in row['pages']})
        result.append({'rubric_id': item['id'], 'answer': answer, 'pages': pages,
                       'evidence': '\n'.join(f"Page {','.join(map(str, row['pages']))}: {row['evidence']}"
                                             for row in decisive) or 'No page observations available',
                       'defect': None, 'defects': [copy.deepcopy(row['defect']) for row in failures if row.get('defect')],
                       'page_verdicts': copy.deepcopy(rows), 'aggregation': 'any_no_else_unknown_else_all_yes'})
    return result


def defect_conflicts(verdicts):
    """Exact shared keys flag ambiguity; free-text keys cannot prove semantic uniqueness."""
    owners = {}
    for row in verdicts:
        if row['answer'] != 'no':
            continue
        defects = row.get('defects', []) or ([row['defect']] if row.get('defect') else [])
        for defect in defects:
            key = defect['key'].strip()
            owners.setdefault(key, set()).add(row['rubric_id'])
    return [{'defect_key': key, 'rubric_ids': sorted(ids)} for key, ids in sorted(owners.items()) if len(ids) > 1]


def _model_name(client, config):
    return getattr(client, 'config', config).get('judge_model', config.get('judge_model', 'unavailable'))


def _completed_tasks(function, values, workers):
    if workers == 1:
        for value in values:
            yield function(value)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(function, value) for value in values]):
                yield future.result()


def _assess_document(deck, rubric, config, client, catalog, checkpoint=None, previous=None):
    items = rubric['items']
    content_items = [item for item in items if not visual_item(item)]
    visual_items = [item for item in items if visual_item(item)]
    doc = document_id(deck)
    pages = page_catalog(deck)
    by_page = {page['page']: page for page in pages}
    client_config = getattr(client, 'config', config)
    result = {'model': _model_name(client, config), 'document_id': doc,
              'recovery_binding': assessment_binding(deck, rubric, catalog, client_config, prompt_bundle()),
              'scoring_method': SCORING_METHOD, 'judge_validator_version': VALIDATOR_VERSION,
              'content': {'strategy': 'one_criterion_per_request',
                          'records': copy.deepcopy((previous or {}).get('content', {}).get('records', []))},
              'visual_batches': copy.deepcopy((previous or {}).get('visual_batches', [])),
              'visual_pages': copy.deepcopy((previous or {}).get('visual_pages', [])),
              'auxiliary_errors': [], 'verdicts': []}
    previous_content = {row['rubric_id']: row for row in (previous or {}).get('content', {}).get('records', [])}
    previous_visual = {row['pages'][0]: row for row in (previous or {}).get('visual_batches', [])}
    result_lock = threading.RLock()
    workers = client_config.get('assessment_workers', 30)
    if type(workers) is not int or workers < 1:
        raise ValueError('assessment_workers must be a positive integer')
    item_order = {item['id']: index for index, item in enumerate(items)}

    def save():
        result['checkpoint_hash'] = digest({k: v for k, v in result.items() if k != 'checkpoint_hash'})
        if checkpoint is not None:
            write_json(checkpoint, result)
    save()

    def assess_content(item):
        payload = {**_evaluation_context('document'), 'document_id': doc, 'source_catalog': catalog,
                   'pages': pages, 'criterion': _criterion(item)}
        images = [(by_page[s['page']]['page_id'], s['image']) for s in deck['slides']]
        record = _request(client, compile_judge_prompt(item), payload, images, previous_content.get(item['id']))
        return item, record

    for item, record in _completed_tasks(assess_content, content_items, workers):
        result['content']['records'] = [row for row in result['content']['records'] if row['rubric_id'] != item['id']]
        result['content']['records'].append({'rubric_id': item['id'], **record})
        result['content']['records'].sort(key=lambda row: item_order[row['rubric_id']])
        if record['status'] == 'success':
            result['verdicts'].append(_verdict(record, item['id']))
        else:
            result['verdicts'].append(_unknown(item['id'], _unavailable_reason(record), error=record.get('error')))
        save()

    if visual_items:
        try:
            with _RENDER_LOCK:
                geometry = rendered_text_geometry(deck)
        except Exception as error:
            geometry = []
            result['auxiliary_errors'].append({'stage': 'geometry', 'error_type': type(error).__name__})
        # Use the actually executing provider's capacity, not another model's config.
        max_images = client_config.get('max_images_per_request', config.get('max_images_per_request', 60))
        if max_images < 1:
            raise ValueError('The visual evaluator requires capacity for at least one page image')
        def assess_page(slide):
            page = slide['page']
            errors, batches = [], []
            images = [(by_page[page]['page_id'], slide['image'])]
            remaining_images = max(0, max_images - len(images))
            crop_limit = min(3, config.get('max_visual_focus_images', 3), remaining_images)
            focus_config = {**config, 'max_images_per_request': max_images,
                            'max_visual_focus_images': crop_limit}
            prior = previous_visual.get(page)
            saved_images = prior.get('image_refs') if prior else None
            try:
                with _RENDER_LOCK:
                    views = [] if saved_images else focus_views({**deck, 'slides': [slide]},
                                        [p for p in geometry if p['page'] == page], focus_config)
            except Exception as error:
                views = []
                errors.append({'stage': 'focus', 'pages': [page],
                               'error_type': type(error).__name__})
            views = [view for view in views if view['page'] == page][:crop_limit]
            # Prefix crop references with the document id; do not forward old
            # ledger observations, source text, or another candidate's geometry.
            images.extend((f"{doc}/page/{view['page']}/crop/{view['id']}; original PDF rectangle {view['rect']}",
                           view['image']) for view in views)
            if saved_images:
                images = [(image['label'], image['path']) for image in saved_images]
            ids = [item['id'] for item in visual_items]
            payload = {**_evaluation_context('page'), 'document_id': doc, 'source_catalog': catalog,
                       'pages': [{'page': page, 'page_id': by_page[page]['page_id']}],
                       'criteria': [_criterion(item) for item in visual_items]}
            record = _request(client, compile_batch_prompt(visual_items), payload, images, prior, rubric_ids=ids)
            batch = {'rubric_ids': ids, 'pages': [page],
                     'crop_ids': prior['crop_ids'] if prior else [f"{doc}/{v['id']}" for v in views],
                     **record, 'image_refs': image_references(images)}
            batches.append(batch)
            page_record = {'page': page, 'verdicts': []}
            if record['status'] == 'success':
                for row in record['verdicts']:
                    verdict = {'rubric_id': row['rubric_id'], 'answer': row['answer'], 'pages': [page],
                               'evidence': row['evidence'], 'defect': None}
                    if row['answer'] == 'unknown':
                        verdict['unknown_origin'] = 'model_insufficient_evidence'
                    page_record['verdicts'].append(verdict)
            else:
                page_record['verdicts'] = [_unknown(rid, _unavailable_reason(record), [page], record.get('error'))
                                           for rid in ids]
            # The complete page is one atomic request/checkpoint. Invalid batches
            # never publish or reuse partial per-criterion answers.
            with result_lock:
                result['visual_batches'] = [row for row in result['visual_batches'] if row['pages'] != [page]]
                result['visual_batches'].append(copy.deepcopy(batch))
                result['visual_batches'].sort(key=lambda row: row['pages'][0])
                result['visual_pages'] = [row for row in result['visual_pages'] if row['page'] != page]
                result['visual_pages'].append(copy.deepcopy(page_record))
                result['visual_pages'].sort(key=lambda row: row['page'])
                save()
            return page_record, batches, errors

        for page_record, batches, errors in _completed_tasks(assess_page, deck['slides'], workers):
            with result_lock:
                result['auxiliary_errors'].extend(errors)
                save()
        result['visual_pages'].sort(key=lambda page: page['page'])
        result['verdicts'].extend(aggregate_visual(visual_items, result['visual_pages']))
    result['verdicts'] = sorted(result['verdicts'], key=lambda row: next(
        index for index, item in enumerate(items) if item['id'] == row['rubric_id']))
    result['summary'] = aggregate(rubric, result['verdicts'])
    result['ownership_conflicts'] = defect_conflicts(result['verdicts'])
    result['visual_evaluated'] = bool(visual_items)
    records = result['content']['records'] + result['visual_batches']
    unavailable = [r for r in records if r['status'] != 'success']
    result['execution'] = {'status': ('failed' if any(r.get('error', {}).get('fatal') for r in unavailable)
                                      else 'incomplete' if unavailable else 'complete'),
                           'unavailable_requests': len(unavailable),
                           'model_unknown_requests': sum(r.get('outcome_kind') == 'model_unknown' for r in records)}
    save()
    return result



def evaluate_contract(case, rubric, config, client, decks, sources, output, verification_client=None, resume_from=None,
                      resume_current=False):
    """Assess once with K3, then score all candidates on a shared rubric subset.

    The retired verification argument is rejected before provider work so callers
    cannot silently run or reuse the former independent-review workflow. API
    retry and accounting remain the single client's responsibility.
    """
    if verification_client is not None:
        raise ValueError('contract-v2.5 uses one K3 assessor; verification_client is no longer supported')
    client_config = getattr(client, 'config', config)
    if (client_config.get('judge_model') != 'kimi-k3'
            or client_config.get('vision_model') != 'kimi-k3'):
        raise ValueError('contract-v2.5 requires kimi-k3 for content and visual judgments')
    if config.get('scoring_method', SCORING_METHOD) != SCORING_METHOD:
        raise ValueError('contract-v2.5 requires scoring_method=single_k3')
    catalog = source_catalog(sources)
    validate_inputs(case, rubric, config, decks, sources, catalog)
    workers = config.get('candidate_workers', 1)
    if type(workers) is not int or workers < 1:
        raise ValueError('candidate_workers must be a positive integer')
    output = Path(output) / case['id']
    previous = {}
    if resume_current and resume_from is not None:
        raise ValueError('Select either batch checkpoint reuse or recovery into a new directory')
    if resume_current:
        # The batch runner holds an exclusive run lock, verifies frozen inputs,
        # and retains prior receipts in SQLite. Never-started candidates need
        # no checkpoint; existing checkpoints remain strictly bound to inputs.
        for cid, deck in decks.items():
            checkpoint = output / f'{cid}-assessment.json'
            if checkpoint.exists():
                binding = assessment_binding(deck, rubric, catalog, client_config, prompt_bundle(), strict=True)
                previous[cid] = validate_checkpoint(checkpoint, binding, parse_boxed_answer)
                archived = output / 'checkpoint-history' / f'{cid}-{previous[cid]["checkpoint_hash"]}.json'
                if not archived.exists():
                    write_json(archived, previous[cid])
    if resume_from is not None:
        if Path(output).parent.resolve().is_relative_to(Path(resume_from).resolve()):
            raise ValueError('Recovery output must differ from the original run')
        if output.exists() and any(output.iterdir()):
            raise ValueError('Recovery requires a new, empty candidate output directory')
        for cid, deck in decks.items():
            binding = assessment_binding(deck, rubric, catalog, client_config, prompt_bundle(), strict=True)
            previous[cid] = validate_checkpoint(Path(resume_from) / case['id'] / f'{cid}-assessment.json',
                                                binding, parse_boxed_answer)
    output.mkdir(parents=True, exist_ok=True)
    identities = {c['id']: candidate_identity(case, c, decks[c['id']]) for c in case['candidates']}
    write_candidate_mapping(output, mapping_rows([case], output=output.parent))
    settings = {'scoring_method': SCORING_METHOD, 'assessment': client_config, 'config': config,
                'comparison_policy': COMPARISON_POLICY}
    reports = {}
    # Clear stale complete reports before any provider work can fail. Existing
    # artifacts retain their schema and are never relabeled as single-K3 results.
    for cid in decks:
        existing = output / f'{cid}.json'
        if existing.exists():
            existing.rename(output / f'{cid}-superseded-{digest(read_json(existing))[:16]}.json')

    def assess_candidate(cid, deck):
        assessment = _assess_document(deck, rubric, config, client, catalog,
                                      checkpoint=output / f'{cid}-assessment.json', previous=previous.get(cid))
        report = {'case_id': case['id'], 'candidate_id': cid,
                  'candidate_identity': copy.deepcopy(identities[cid]),
                  'candidate_hash': deck['identity']['sha256'], 'document_id': document_id(deck),
                  'rubric_hash': rubric['rubric_hash'], 'source_hash': digest(sources),
                  'config_hash': digest(settings), 'judge_model': _model_name(client, config),
                  'protocol': PROTOCOL, 'evaluation_protocol': PROTOCOL,
                  'pipeline_version': PIPELINE_VERSION, 'base_pipeline_version': BASE_VERSION,
                  'judge_validator_version': VALIDATOR_VERSION,
                  'scoring_method': SCORING_METHOD,
                  'judgment_strategy': JUDGMENT_STRATEGY,
                  'judge_prompt_fingerprint': digest([PIPELINE_VERSION, prompt_bundle()]),
                  'pipeline_mode': rubric.get('pipeline_mode'),
                  'source_audit': copy.deepcopy(rubric.get('source_audit')),
                  'assessment': assessment,
                  'execution': copy.deepcopy(assessment['execution']),
                  'summary': copy.deepcopy(assessment['summary']),
                  'verdicts': copy.deepcopy(assessment['verdicts']),
                  'ownership_conflicts': copy.deepcopy(assessment['ownership_conflicts']),
                  'limitations': ['Single-model judgments are not independently verified.',
                                 'Independent criterion reasons do not establish unique semantic defect ownership.']}
        return report

    # Candidate tasks share the original provider Client: semaphores, pacing,
    # journals, budgets and circuit breakers remain global to the provider.
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(decks)))) as pool:
        pending = {cid: pool.submit(assess_candidate, cid, deck) for cid, deck in decks.items()}
        # Keep report order deterministic even when B completes before A.
        for cid, future in pending.items():
            reports[cid] = future.result()
    comparison, summaries = score_candidates(
        rubric, {cid: report['verdicts'] for cid, report in reports.items()},
        [c['id'] for c in case['candidates']])
    # Publish totals only after every candidate has a final assessment, so a
    # faster candidate cannot be ranked using a different set of criteria.
    for cid, report in reports.items():
        report['comparison'] = copy.deepcopy(comparison)
        report['summary'] = summaries[cid]
        write_json(output / f'{cid}.json', report)
    write_candidate_mapping(output, mapping_rows([case], {case['id']: reports}, output.parent))
    return reports
