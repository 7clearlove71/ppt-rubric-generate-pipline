"""Visual search coverage is deterministic; only an image assessor can judge it."""
from pathlib import Path

import pytest

from ppt_rubric.common import file_digest, read_json
from ppt_rubric.contract_regression import expected_visual_answers, run_checks, visual_probe
from ppt_rubric.render import rendered_text_geometry
from ppt_rubric.visual_regions import focus_coverage, focus_views, inspection_regions


def make_pdf(tmp_path, pages):
    import fitz
    document = fitz.open()
    slides = []
    for number, entries in enumerate(pages, 1):
        page = document.new_page(width=400, height=300)
        for x, y, text in entries:
            page.insert_text((x, y), text, fontsize=10)
        image = tmp_path / f'page-{number}.png'
        page.get_pixmap().save(image)
        slides.append({'page': number, 'image': str(image)})
    pdf = tmp_path / 'slides.pdf'
    document.save(pdf)
    document.close()
    return {'pdf': str(pdf), 'slides': slides}


def test_off_page_text_is_retained_but_focus_only_renders_the_visible_boundary(tmp_path):
    deck = make_pdf(tmp_path, [[(20, 60, 'visible title'), (20, 330, 'lost final response level')]])
    geometry = rendered_text_geometry(deck)
    lost = next(span for span in geometry[0]['spans'] if span['text'] == 'lost final response level')
    assert lost['page_intersection'] is None
    assert lost['bbox'][1] > 300
    region = next(r for r in geometry[0]['inspection_candidates']
                  if r['kind'] == 'page_edge' and lost['id'] in r['outside_span_ids'])
    assert region['signal'] == 'bbox_outside_page'
    views = focus_views(deck, geometry, {'max_images_per_request': 2, 'max_visual_focus_images': 8})
    assert len(views) == 1 and views[0]['id'] == region['id']
    assert views[0]['source_rect'][1] > 300
    assert 0 <= views[0]['rect'][1] < views[0]['rect'][3] <= 300
    assert Path(views[0]['image']).is_file()
    assert 'not a confirmed defect' in views[0]['label']
    assert 'answer' not in region


def test_candidate_coverage_includes_blank_edges_table_last_row_and_footnote(tmp_path):
    entries = [(20, 50, 'header')]
    for y, left, right in [(110, 'Item A', '100'), (140, 'Item B', '200'), (170, 'Item C', '300')]:
        entries.extend([(30, y, left), (230, y, right)])
    entries.append((20, 280, 'footnote amount in millions'))
    deck = make_pdf(tmp_path, [entries, []])
    geometry = rendered_text_geometry(deck)
    regions = geometry[0]['inspection_candidates']
    assert {r['side'] for r in regions if r['kind'] == 'page_edge'} == {'left', 'right', 'top', 'bottom'}
    table = next(r for r in regions if r['signal'] == 'aligned_table_final_rows')
    assert 'Item C' in table['texts'] and '300' in table['texts']
    footer = next(r for r in regions if r['signal'] == 'lowest_text_rows')
    assert 'footnote amount in millions' in footer['texts']
    assert any(r['signal'] == 'footer_band_without_text' for r in geometry[1]['inspection_candidates'])


@pytest.mark.parametrize('labels', [('0', '6', '18', '60'), ('Plant A', 'Plant B', 'Plant C', 'Plant D')])
def test_continuous_and_normal_category_axes_are_both_neutral_candidates(tmp_path, labels):
    deck = make_pdf(tmp_path, [[(25 + 90 * i, 220, label) for i, label in enumerate(labels)]])
    regions = rendered_text_geometry(deck)[0]['inspection_candidates']
    axis = next(r for r in regions if r['kind'] == 'axis_encoding')
    assert axis['texts'] == list(labels)
    assert axis['axis_mode'] == 'unresolved'
    assert 'Equal category spacing is valid' in axis['inspection']
    assert not {'answer', 'defect', 'score'} & set(axis)


def test_normal_wrapping_and_separate_units_do_not_create_overlap_verdicts(tmp_path):
    deck = make_pdf(tmp_path, [[(25, 90, 'normal wrapped line one'), (25, 110, 'normal wrapped line two'),
                              (240, 160, '42'), (240, 180, 'million')]])
    geometry = rendered_text_geometry(deck)
    assert geometry[0]['text_overlap_candidates'] == []
    assert not any(r['kind'] == 'overlap' for r in geometry[0]['inspection_candidates'])
    assert any(r['kind'] == 'footer_last_line' for r in geometry[0]['inspection_candidates'])


def test_crop_budget_balances_pages_and_domains_and_records_omissions(tmp_path):
    rows = [(20, 80, 'one'), (23, 82, 'two'), (30, 220, '0'), (160, 220, '12'), (290, 220, '60')]
    deck = make_pdf(tmp_path, [rows, rows])
    geometry = rendered_text_geometry(deck)
    config = {'max_images_per_request': 8, 'max_visual_focus_images': 6}
    views = focus_views(deck, geometry, config)
    assert len(views) == 6
    assert {v['page'] for v in views[:2]} == {1, 2}
    assert {v['kind'] for v in views} >= {'overlap', 'axis_encoding', 'footer_last_line'}
    assert focus_views(deck, geometry, config) == views
    coverage = focus_coverage(deck, geometry, config, views)
    assert coverage['full_page_numbers'] == [1, 2]
    assert coverage['automatic_verdict'] is False
    assert coverage['model_inspection_status'] == 'not_established_by_image_supply'
    assert len(coverage['selected_crop_ids']) == 6
    assert len(coverage['uncropped_candidate_ids']) + 6 == coverage['available_candidates']
    assert focus_views(deck, geometry, {'max_images_per_request': 2}) == []
    no_crops = focus_coverage(deck, geometry, {'max_images_per_request': 2}, [])
    assert no_crops['uncropped_candidate_ids'] and no_crops['full_page_numbers'] == [1, 2]


def test_unknown_visual_label_is_not_silently_treated_as_a_passing_control():
    with pytest.raises(ValueError, match='explicit expected_verdicts'):
        expected_visual_answers({'ground_truth': 'time_axis_distortion'})
    assert expected_visual_answers({'expected_verdicts': {'V_LAYOUT': 'no', 'V_READ': 'yes'}}) == {
        'V_LAYOUT': 'no', 'V_READ': 'yes'}
    assert expected_visual_answers({'ground_truth': 'no_overlap'}) == {'V_READ': 'yes'}


def test_axis_probe_keeps_labels_offline_and_compares_the_correct_criterion(tmp_path):
    image = tmp_path / 'image.png'
    image.write_bytes(b'Only API isolation is tested here; no visual evidence is inferred.')
    sample = {'id': 'axis', 'ground_truth': 'time_axis_distortion',
              'diagnostic_category': 'axis_encoding',
              'expected_verdicts': {'V_READ': 'yes', 'V_LAYOUT': 'no'},
              'expected_objects': ['SECRET RATIONALE'], 'candidate_id': 'secret candidate',
              'image_path': str(image), 'image': {'sha256': file_digest(image)},
              'original_page': {'path': str(image), 'sha256': file_digest(image)}}

    class Client:
        def ask(self, role, system, payload, images, validate):
            assert not any(word in repr(payload) for word in ('SECRET', 'secret candidate', 'time_axis_distortion', 'expected_verdicts'))
            return {'pages': [{'page': 1, 'verdicts': [
                {'rubric_id': i['id'], 'answer': 'yes' if i['id'] == 'V_READ' else 'no',
                 'pages': [1], 'evidence': 'Visible labels and plotted positions checked.',
                 'defect': None if i['id'] == 'V_READ' else {'key': 'doc/page/1/axis', 'description': 'A positional mismatch'}}
                for i in payload['criteria']]}]}

    visual_probe(sample, Client())
    records = run_checks({'samples': [sample]}, Client(), None, tmp_path / 'out')
    result = records[0]['assessors']['primary']
    assert result['criterion_matches_expected'] == {'V_READ': True, 'V_LAYOUT': True}
    assert result['rationale_review_status'] == 'pending_object_region_and_ownership_review'
    assert read_json(tmp_path / 'out/results.json')['records'][0]['original_page_sha256'] == file_digest(image)


def test_formal_visual_probe_enforces_the_same_diagnostics_as_document_scoring(tmp_path):
    image = tmp_path / 'page.png'
    image.write_bytes(b'API boundary fixture')
    sample = {'image_path': str(image), 'image': {'sha256': file_digest(image)},
              'original_page': {'path': str(image), 'sha256': file_digest(image)}}

    class Client:
        config = {'visual_diagnostics': True}

        def ask(self, role, system, payload, images, validate):
            assert payload['require_diagnostics'] is True
            return {'pages': [{'page': 1, 'verdicts': [
                {'rubric_id': i['id'], 'answer': 'yes', 'pages': [1],
                 'evidence': 'Visible page checked.', 'defect': None}
                for i in payload['criteria']]}]}

    with pytest.raises(ValueError, match='Visual diagnostics need'):
        visual_probe(sample, Client())
