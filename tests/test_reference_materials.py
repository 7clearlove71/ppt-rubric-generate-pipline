"""Synthetic PDF/PPTX materials exercise real adapters and model input wiring offline."""
import copy
import json
import zipfile
from pathlib import Path

import fitz
import pytest

from ppt_rubric.common import digest, file_digest, read_json
from ppt_rubric.data import ingest
from ppt_rubric.v25 import contract_v2 as contract, contract_judge as judge, render
from ppt_rubric.v25.materials import material_images, model_catalog
from ppt_rubric.v25.source_catalog import source_catalog


@pytest.fixture
def material_case(tmp_path):
    path = tmp_path / 'reference.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page(width=300, height=200)
        page.insert_text((20, 30), 'Revenue 2025: 120 million')
        page.draw_rect(fitz.Rect(20, 60, 160, 120), color=(1, 0, 0))
        page = pdf.new_page(width=300, height=200)
        # Image-only scan: intentionally no extractable PDF text.
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pix.clear_with(80)
        page.insert_image(fitz.Rect(20, 20, 160, 120), stream=pix.tobytes('png'))
        pdf.save(path)
    cfg = read_json('configs/contract-v25-k3.json')
    cfg.update(source_cache_root=str(tmp_path / 'cache'), pipeline_mode='light', source_dpi=72)
    case = {'id': 'materials', 'prompt': 'Use reference.pdf to describe revenue and the scanned diagram.',
            'attachments': [{'path': str(path), 'sha256': file_digest(path), 'name': 'reports/reference.pdf'}],
            'candidates': [{'id': cid, 'sha256': digest(cid)} for cid in ('A', 'B')]}
    return case, cfg


class OfflineClient:
    def __init__(self, config):
        self.config = config
        self.calls = []

    def ask(self, role, system, payload, images=(), validate=None):
        self.calls.append((role, system, copy.deepcopy(payload), list(images)))
        if system == contract.EXTRACT:
            material = [s for s in payload['source_catalog'] if 'page_image' in s]
            reply = {'task_summary': 'Present revenue and diagram', 'source_policy': 'Reference facts only',
                     'requirements': [{'id': f'Q{i}', 'text': text, 'kind': ('fact' if i == 1 else 'deliverable'), 'importance': 1,
                                       'source_ids': [page['id']]} for i, (page, text) in enumerate(zip(material,
                         ['Present 2025 revenue of 120 million', 'Present the scanned diagram']), 1)]}
        else:
            q = payload['requirement']
            reply = {'requirement_id': q['id'], 'title': q['text'], 'question': q['text'] + '?'}
        validate(reply)
        return reply

    def ask_text(self, role, system, payload, images=(), validate=None):
        self.calls.append((role, system, copy.deepcopy(payload), list(images)))
        if role == 'reviewer':
            raw = '{"issues": []}'
        elif 'criteria' in payload:
            raw = json.dumps({'verdicts': [{'rubric_id': row['id'], 'answer': 'yes',
                                          'evidence': 'Offline image fixture ' + row['id']}
                                         for row in payload['criteria']]})
        else:
            raw = r'Offline material comparison fixture.\boxed{yes}'
        validate(raw)
        return raw


def test_pdf_diagram_and_scan_keep_every_page_and_stable_citations(material_case):
    case, cfg = material_case
    sources = render.case_sources(case, cfg)
    assert len(sources) == 3
    assert 'Revenue 2025' in sources[1]['text'] and sources[2]['text'] == ''
    catalog = source_catalog(sources)
    assert len(catalog) == 3 and catalog[2]['start'] == catalog[2]['end'] == 0
    assert catalog[2]['source_name'] == 'reports/reference.pdf' and catalog[2]['page'] == 2
    assert len(material_images(catalog)) == 2
    assert render.case_sources(case, cfg) == sources
    assert source_catalog(render.case_sources(case, cfg)) == catalog
    assert 'path' not in model_catalog(catalog)[1]['page_image']


@pytest.mark.parametrize('mode', ['light', 'full'])
def test_materials_flow_through_extraction_cited_wording_audit_and_judge(material_case, tmp_path, monkeypatch, mode):
    case, cfg = material_case
    cfg['pipeline_mode'] = mode
    builder = OfflineClient(cfg)
    suite = contract.generate_contract(case, cfg, builder, tmp_path / 'rubrics')
    contract.verify_suite(case, suite, cfg)
    extraction = builder.calls[0]
    assert len(extraction[3]) == 2 and 'material_notes' in extraction[2]
    wordings = [c for c in builder.calls if c[1] == contract.WRITE_CRITERION]
    assert len(wordings) == 2
    for i, (_, _, payload, images) in enumerate(wordings, 1):
        assert [s['source_id'] for s in payload['source_catalog']] == ['prompt', f'attachment_1_page_{i}']
        assert [label for label, _ in images] == [f'material/attachment_1_page_{i}']
    audits = [c for c in builder.calls if c[0] == 'reviewer']
    assert len(audits) == (1 if mode == 'full' else 0)
    if audits:
        assert len(audits[0][3]) == 2 and 'reports/reference.pdf, page 2' in audits[0][2]
    sources = render.case_sources(case, cfg)
    image = sources[1]['page_image']['path']
    decks = {c['id']: {'identity': {'sha256': c['sha256']}, 'slides': [
        {'page': 1, 'image': image, 'rendered_text': 'Candidate content'}]} for c in case['candidates']}
    monkeypatch.setattr(judge, 'rendered_text_geometry', lambda *args: [])
    monkeypatch.setattr(judge, 'focus_views', lambda *args: [])
    assessor = OfflineClient(cfg)
    reports = judge.evaluate_contract(case, suite, cfg, assessor, decks, sources, tmp_path / 'judgments')
    assert all(r['execution']['status'] == 'complete' for r in reports.values())
    assert len(assessor.calls) == 2 * (len(suite['items']) - 5 + 1)
    for _, _, payload, images in assessor.calls:
        assert case['prompt'] in json.dumps(payload, ensure_ascii=False)
        assert [label for label, _ in images[:2]] == ['material/attachment_1_page_1', 'material/attachment_1_page_2']
        assert images[2][0].startswith('doc_')
        assert len(images) == 3
        assert payload['source_catalog'][2]['text'] == ''
    # Saved successful judgments remain reusable with source images in their bindings.
    resumed = OfflineClient(cfg)
    judge.evaluate_contract(case, suite, cfg, resumed, decks, sources, tmp_path / 'judgments', resume_current=True)
    assert resumed.calls == []


@pytest.mark.parametrize('mutation', ['original', 'image'])
def test_changed_material_is_rejected_before_model_work(material_case, tmp_path, mutation):
    case, cfg = material_case
    sources = render.case_sources(case, cfg)
    target = (Path(case['attachments'][0]['path']) if mutation == 'original'
              else Path(sources[2]['page_image']['path']))
    target.write_bytes(b'changed')
    client = OfflineClient(cfg)
    with pytest.raises(ValueError, match='changed'):
        contract.generate_contract(case, cfg, client, tmp_path / 'rubrics')
    assert client.calls == []


@pytest.mark.parametrize('option', ['max_source_pages', 'max_images_per_request', 'max_case_text_chars'])
def test_source_limits_fail_without_sending_partial_materials(material_case, tmp_path, option):
    case, cfg = material_case
    cfg[option] = 1
    client = OfflineClient(cfg)
    with pytest.raises(ValueError, match='exceed'):
        contract.generate_contract(case, cfg, client, tmp_path / 'rubrics')
    assert client.calls == []


def test_combined_material_and_candidate_capacity_checked_before_any_judge(material_case, tmp_path, monkeypatch):
    case, cfg = material_case
    suite = contract.generate_contract(case, cfg, OfflineClient(cfg), tmp_path / 'rubrics')
    sources = render.case_sources(case, cfg)
    image = sources[1]['page_image']['path']
    decks = {c['id']: {'identity': {'sha256': c['sha256']}, 'slides': [
        {'page': n, 'image': image, 'rendered_text': 'Candidate'}
        for n in range(1, (1 if c['id'] == 'A' else 2) + 1)]} for c in case['candidates']}
    cfg['max_images_per_request'] = 3  # A fits, B does not; neither may be judged.
    client = OfflineClient(cfg)
    with pytest.raises(ValueError, match=r'material \+ candidate images exceed'):
        judge.evaluate_contract(case, suite, cfg, client, decks, sources, tmp_path / 'judgments')
    assert client.calls == []


def make_source_pptx(path, hidden=False):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('ppt/presentation.xml', '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="rB"/><p:sldId r:id="rA"/></p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels', '<Relationships><Relationship Id="rA" Target="slides/slide1.xml"/><Relationship Id="rB" Target="slides/slide9.xml"/></Relationships>')
        for n in (1, 9):
            show = ' show="0"' if hidden and n == 9 else ''
            z.writestr(f'ppt/slides/slide{n}.xml', f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"{show}><a:p><a:r><a:t>XML sentinel {n}</a:t></a:r></a:p></p:sld>')


def test_pptx_material_uses_rendered_pdf_text_and_pages(material_case, tmp_path, monkeypatch):
    case, cfg = material_case
    path = tmp_path / 'reference.pptx'
    make_source_pptx(path)
    pdf_path = case['attachments'][0]['path']
    case['attachments'] = [{'path': str(path), 'sha256': file_digest(path)}]
    calls = []
    def convert(pptx, config, cache):
        calls.append((pptx, config, cache))
        assert [s['text'] for s in render.extract_pptx(pptx)] == ['XML sentinel 9', 'XML sentinel 1']
        return {'identity': {'sha256': file_digest(pptx), 'renderer': 'offline converter fixture'}, 'pdf': pdf_path}
    monkeypatch.setattr(render, 'prepare_deck', convert)
    sources = render.case_sources(case, cfg)
    assert len(calls) == 1 and calls[0][1]['dpi'] == cfg['source_dpi']
    assert len(sources) == 3 and 'Revenue 2025' in sources[1]['text']
    assert sources[2]['text'] == ''
    assert 'XML sentinel' not in json.dumps(sources)
    assert len(material_images(source_catalog(sources))) == 2


def test_hidden_source_pptx_fails_before_conversion(material_case, tmp_path, monkeypatch):
    case, cfg = material_case
    path = tmp_path / 'hidden.pptx'
    make_source_pptx(path, hidden=True)
    case['attachments'] = [{'path': str(path), 'sha256': file_digest(path)}]
    def unexpected(*args):
        raise AssertionError('Hidden slides must be rejected before conversion')
    monkeypatch.setattr(render, 'prepare_deck', unexpected)
    with pytest.raises(ValueError, match='hidden/empty'):
        render.case_sources(case, cfg)


def test_reference_named_result_is_never_a_candidate(tmp_path):
    root = tmp_path / 'inputs/task'
    for folder in ('A', 'B', 'attachments'):
        (root / folder).mkdir(parents=True)
        (root / folder / 'result.pptx').write_bytes(folder.encode())
    (root / 'prompt.txt').write_text('Use attached slides as reference')
    case = ingest(tmp_path / 'inputs', tmp_path / 'manifest.json', split='dev')['cases'][0]
    assert len(case['candidates']) == 2
    assert len(case['attachments']) == 1
    assert case['attachments'][0]['name'] == 'result.pptx'


def test_identical_pdf_paths_share_a_self_contained_material_cache(material_case, tmp_path):
    case, cfg = material_case
    sources = render.case_sources(case, cfg)
    original = Path(case['attachments'][0]['path'])
    duplicate = tmp_path / 'duplicate.pdf'
    duplicate.write_bytes(original.read_bytes())
    original.unlink()
    case['attachments'][0]['path'] = str(duplicate)
    assert render.case_sources(case, cfg) == sources


def test_multiple_attachments_respect_total_page_limit(material_case, tmp_path):
    case, cfg = material_case
    case['attachments'].append(copy.deepcopy(case['attachments'][0]))
    cfg['max_source_pages'] = 3
    client = OfflineClient(cfg)
    with pytest.raises(ValueError, match='across attachments'):
        contract.generate_contract(case, cfg, client, tmp_path / 'rubrics')
    assert not client.calls


def test_encrypted_pdf_fails_before_model_work(material_case, tmp_path):
    case, cfg = material_case
    path = tmp_path / 'encrypted.pdf'
    with fitz.open(case['attachments'][0]['path']) as pdf:
        pdf.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader')
    case['attachments'] = [{'path': str(path), 'sha256': file_digest(path)}]
    client = OfflineClient(cfg)
    with pytest.raises(ValueError, match='Encrypted'):
        contract.generate_contract(case, cfg, client, tmp_path / 'rubrics')
    assert not client.calls


def test_scan_only_count_constraint_defers_text_grounding_to_visual_source(material_case):
    case, cfg = material_case
    catalog = source_catalog(render.case_sources(case, cfg))
    q = {'id': 'Q1', 'kind': 'deliverable', 'importance': 1, 'text': '至少3页',
         'source_ids': [catalog[2]['id']]}
    requirements = {'task_summary': 'Scan constraint', 'source_policy': 'Read image', 'requirements': [q]}
    wordings = [{'requirement_id': 'Q1', 'title': '页数', 'question': '是否至少3页？'}]
    design = contract.compile_requirements(requirements, wordings)
    contract.validate_design(design, requirements, catalog, wordings)
    # An unrelated scanned page cannot excuse an ungrounded text-only criterion.
    q['source_ids'] = [catalog[0]['id']]
    design = contract.compile_requirements(requirements, wordings)
    with pytest.raises(ValueError, match='unsupported exact count threshold'):
        contract.validate_design(design, requirements, catalog, wordings)


@pytest.mark.parametrize('option,value', [('source_dpi', 0), ('source_dpi', True), ('max_source_pages', -1)])
def test_invalid_material_limits_rejected_by_current_config(material_case, option, value):
    _, cfg = material_case
    cfg[option] = value
    with pytest.raises(ValueError, match=option):
        contract.validate_config(cfg, 'generate')


def test_offline_plan_with_materials_reports_page_count_and_policy(material_case, tmp_path, monkeypatch):
    from ppt_rubric.common import write_json
    case, cfg = material_case
    case['split'] = 'dev'
    manifest = {'schema_version': 1, 'cases': [case]}
    manifest['id'] = digest(manifest)
    manifest_path, config_path = tmp_path / 'manifest.json', tmp_path / 'config.json'
    write_json(manifest_path, manifest)
    write_json(config_path, cfg)
    def no_client(*args, **kwargs):
        raise AssertionError('Plan must not construct a provider Client')
    monkeypatch.setattr(contract, 'Client', no_client)
    contract.main(['plan', '--manifest', str(manifest_path), '--config', str(config_path),
                   '--cache', cfg['source_cache_root'], '--out', str(tmp_path / 'plan.json')])
    plan = read_json(tmp_path / 'plan.json')
    assert plan['offline'] is True and plan['pipeline_version'] == contract.VERSION
    assert plan['cases'][0]['material_pages'] == 2
    assert plan['material_input_policy']['content_image_formula'] == 'A + P'
    assert 'cited material pages' in plan['material_input_policy']['write_criterion']


@pytest.fixture
def text_material_case(material_case):
    case, cfg = material_case
    path = case['attachments'][0]['path']
    with fitz.open(path) as pdf:
        pdf[1].insert_text((20, 30), 'Diagram description: training then delivery')
        pdf.saveIncr()
    case['attachments'][0]['sha256'] = file_digest(path)
    return case, cfg


class StageClient(OfflineClient):
    def ask(self, role, system, payload, images=(), validate=None):
        if system != contract.EXTRACT:
            return super().ask(role, system, payload, images, validate)
        self.calls.append((role, system, copy.deepcopy(payload), list(images)))
        material = [s for s in payload['source_catalog'] if s['source_id'].startswith('attachment_')]
        result = {'task_summary': 'Revenue and delivery', 'source_policy': 'Use supplied evidence only',
                  'requirements': [{'id': f'Q{i}', 'kind': 'fact' if i == 1 else 'deliverable',
                     'importance': 1, 'text': text, 'source_ids': [page['id']]}
                     for i, (page, text) in enumerate(zip(material,
                         ['Present revenue of 120 million', 'Describe delivery plan']), 1)]}
        validate(result)
        return result


def candidate_decks(case, sources):
    return {c['id']: {'identity': {'sha256': c['sha256']}, 'slides': [
        {'page': 1, 'image': sources[1]['page_image']['path'],
         'rendered_text': 'Revenue 120 million. Training followed by delivery.'}]}
        for c in case['candidates']}


@pytest.mark.parametrize('generator,reviewer,judge_images', [
    (False, False, False), (False, True, True), (True, False, False), (True, True, False)])
def test_per_stage_capabilities_control_inputs_and_scoring(text_material_case, tmp_path, monkeypatch,
                                                         generator, reviewer, judge_images):
    from ppt_rubric.v25.comparison_scoring import validate_comparison
    from ppt_rubric.candidate_mapping import mapping_rows
    from ppt_rubric.contract_report import summarize
    case, cfg = text_material_case
    cfg.update(pipeline_mode='full', role_supports_images={'generator': generator,
               'reviewer': reviewer, 'judge': judge_images})
    if not judge_images:
        cfg['judge_model'] = 'offline-text-model'
        cfg.pop('vision_model')
    build = StageClient(cfg)
    contract.validate_config(cfg, 'generate')
    suite = contract.generate_contract(case, cfg, build, tmp_path / 'rubrics')
    assert suite['provenance']['generation_supports_images'] is generator
    assert suite['provenance']['review_supports_images'] is reviewer
    for role, _, payload, images in build.calls:
        assert bool(images) is (reviewer if role == 'reviewer' else generator)
        if role == 'generator' and not generator:
            assert all('page_image' not in row for row in payload['source_catalog'])
            assert '仅提供材料的PDF文本层' in payload['material_notes']
        if role == 'reviewer' and not reviewer:
            assert '仅提供材料的PDF文本层' in payload
    sources = render.case_sources(case, cfg)
    decks = candidate_decks(case, sources)
    monkeypatch.setattr(judge, 'rendered_text_geometry', lambda *a: [])
    monkeypatch.setattr(judge, 'focus_views', lambda *a: [])
    config = {**cfg, 'pipeline_stage': 'judge'}
    contract.validate_config(config, 'judge')
    assessor = StageClient(config)
    reports = judge.evaluate_contract(case, suite, config, assessor, decks, sources, tmp_path / 'judgments')
    comparison = validate_comparison(case, suite, reports)
    content_count = len(suite['items']) - 5
    assert len(assessor.calls) == 2 * (content_count + (1 if judge_images else 0))
    for _, _, payload, images in assessor.calls:
        assert bool(images) is judge_images
        if not judge_images:
            assert 'criteria' not in payload
            assert all('page_image' not in row for row in payload['source_catalog'])
            assert '仅内容评测' in payload['input_notes']
    for report in reports.values():
        assert report['summary']['score'] == pytest.approx(100 if judge_images else 80)
        assert report['execution']['status'] == 'complete'
        assert report['assessment']['visual_evaluated'] is judge_images
        assert report['evaluation_coverage']['mode'] == ('full' if judge_images else 'content_only')
        assert report['scoring_method'] == ('single_k3' if judge_images else 'single_text')
        if not judge_images:
            assert len(report['verdicts']) == content_count
            assert report['assessment']['visual_batches'] == []
            assert len(report['evaluation_coverage']['skipped_rubric_ids']) == 5
            assert comparison['unassessed_weight'] == pytest.approx(20)
            assert comparison['original_possible'] == pytest.approx(100)
            assert comparison['scope_possible'] == pytest.approx(80)
            assert comparison['excluded_rubric_ids'] == []
    rows = mapping_rows([case], {case['id']: reports}, tmp_path / 'judgments')
    assert all(r['evaluation_mode'] == ('full' if judge_images else 'content_only') for r in rows)
    case['split'] = 'dev'
    manifest = {'schema_version': 1, 'cases': [case]}
    manifest['id'] = digest(manifest)
    summary = summarize(manifest, tmp_path)
    assert summary['cases'][0]['candidates']['A']['summary']['score'] == pytest.approx(100 if judge_images else 80)
    if not judge_images:
        assert summary['scoring_method'] == 'single_text'
        assert summary['cases'][0]['weight_sensitivity']['status'] == 'not_applicable'
        assert 'Content-only' in summary['interpretation'][0]
    resumed = StageClient(config)
    judge.evaluate_contract(case, suite, config, resumed, decks, sources, tmp_path / 'judgments', resume_current=True)
    assert resumed.calls == []
    if not judge_images:
        changed = {**config, 'judge_model': 'kimi-k3', 'vision_model': 'kimi-k3',
                   'role_supports_images': {**config['role_supports_images'], 'judge': True}}
        no_work = StageClient(changed)
        with pytest.raises(ValueError, match='binding'):
            judge.evaluate_contract(case, suite, changed, no_work, decks, sources,
                                    tmp_path / 'judgments', resume_current=True)
        assert not no_work.calls


@pytest.mark.parametrize('role', ['generator', 'reviewer'])
def test_text_stage_scan_rejected_before_any_generation(material_case, tmp_path, role):
    case, cfg = material_case
    cfg.update(pipeline_mode='full', role_supports_images={role: False})
    client = StageClient(cfg)
    with pytest.raises(ValueError, match='without text'):
        contract.generate_contract(case, cfg, client, tmp_path / 'rubrics')
    assert not client.calls


def test_text_judge_empty_candidate_fails_before_model_work(text_material_case, tmp_path):
    case, cfg = text_material_case
    suite = contract.generate_contract(case, cfg, StageClient(cfg), tmp_path / 'rubrics')
    sources = render.case_sources(case, cfg)
    decks = candidate_decks(case, sources)
    config = {**cfg, 'judge_model': 'offline-text-model', 'role_supports_images': {'judge': False}}
    for deck in decks.values():
        deck['slides'][0]['rendered_text'] = ''
    client = StageClient(config)
    with pytest.raises(ValueError, match='no readable text'):
        judge.evaluate_contract(case, suite, config, client, decks, sources, tmp_path / 'judgments')
    assert not client.calls


@pytest.mark.parametrize('value', [False, {'generator': 'false'}, {'judge': 0}, {'vision': False}])
def test_invalid_image_capabilities_are_rejected(text_material_case, value):
    _, cfg = text_material_case
    cfg['role_supports_images'] = value
    with pytest.raises(ValueError, match='role_supports_images'):
        contract.validate_config(cfg, 'generate')


def test_text_judge_rejects_image_only_material_before_judge_work(material_case, tmp_path):
    case, cfg = material_case
    suite = contract.generate_contract(case, cfg, StageClient(cfg), tmp_path / 'rubrics')
    sources = render.case_sources(case, cfg)
    client = StageClient({**cfg, 'judge_model': 'offline-text-model', 'role_supports_images': {'judge': False}})
    with pytest.raises(ValueError, match='without text'):
        judge.evaluate_contract(case, suite, client.config, client, candidate_decks(case, sources), sources,
                                tmp_path / 'judgments')
    assert not client.calls


@pytest.mark.parametrize('all_unknown', [False, True])
def test_text_judge_unknown_exclusion_stays_within_content_scope(text_material_case, tmp_path, all_unknown):
    from ppt_rubric.v25.comparison_scoring import validate_comparison
    case, cfg = text_material_case
    suite = contract.generate_contract(case, cfg, StageClient(cfg), tmp_path / 'rubrics')
    sources = render.case_sources(case, cfg)
    class UnknownText(StageClient):
        def ask_text(self, role, system, payload, images=(), validate=None):
            self.calls.append((role, system, copy.deepcopy(payload), list(images)))
            answer = 'unknown' if all_unknown or payload['criterion']['id'] == 'C_CALC' else 'yes'
            raw = 'Only supplied text considered.\\boxed{' + answer + '}'
            validate(raw)
            return raw
    config = {**cfg, 'judge_model': 'offline-text-model', 'role_supports_images': {'judge': False}}
    reports = judge.evaluate_contract(case, suite, config, UnknownText(config), candidate_decks(case, sources),
                                      sources, tmp_path / 'judgments')
    comparison = validate_comparison(case, suite, reports)
    assert not any(rid.startswith('V_') for rid in comparison['excluded_rubric_ids'])
    if all_unknown:
        assert comparison['status'] == 'not_comparable' and comparison['score_possible'] == 0
        assert all(r['summary']['score'] is None for r in reports.values())
    else:
        weight = next(i['score'] for i in suite['items'] if i['id'] == 'C_CALC')
        assert comparison['score_possible'] == pytest.approx(80 - weight)
        assert comparison['excluded_rubric_ids'] == ['C_CALC']
        assert comparison['unassessed_weight'] == pytest.approx(20)


def test_text_plan_and_export_report_zero_image_visual_skips(text_material_case, tmp_path):
    from ppt_rubric.common import write_json
    case, cfg = text_material_case
    cfg['role_supports_images'] = {'generator': False, 'judge': False}
    suite = contract.generate_contract(case, cfg, StageClient(cfg), tmp_path / 'rubrics')
    exported = contract.export_record(case, suite, cfg)
    assert exported['scoring_method'] == 'single_text'
    assert exported['visual_batch_system_prompt'] is None
    assert [r['id'] for r in exported['rubrics'] if not r['evaluated']] == exported['evaluation_coverage']['skipped_rubric_ids']
    case['split'] = 'dev'
    manifest = {'cases': [case]}
    manifest['id'] = digest(manifest)
    write_json(tmp_path / 'manifest.json', manifest)
    write_json(tmp_path / 'config.json', cfg)
    contract.main(['plan', '--manifest', str(tmp_path / 'manifest.json'), '--config', str(tmp_path / 'config.json'),
                   '--cache', cfg['source_cache_root'], '--out', str(tmp_path / 'plan.json')])
    plan = read_json(tmp_path / 'plan.json')
    assert plan['scoring_method'] == 'single_text'
    assert plan['judge_logical_calls_per_document_formula'] == 'C'
    assert plan['cases'][0]['generator_image_count'] == 0
    assert plan['cases'][0]['material_pages'] == 2
    assert plan['material_input_policy']['content_image_formula'] == '0'


def test_text_judge_combined_character_budget_rejects_before_requests(text_material_case, tmp_path):
    case, cfg = text_material_case
    suite = contract.generate_contract(case, cfg, StageClient(cfg), tmp_path / 'rubrics')
    sources = render.case_sources(case, cfg)
    config = {**cfg, 'judge_model': 'offline-text-model', 'role_supports_images': {'judge': False},
              'max_case_text_chars': sum(len(s['text']) for s in sources) + 5}
    client = StageClient(config)
    with pytest.raises(ValueError, match=r'material \+ candidate text exceeds'):
        judge.evaluate_contract(case, suite, config, client, candidate_decks(case, sources), sources,
                                tmp_path / 'judgments')
    assert not client.calls
