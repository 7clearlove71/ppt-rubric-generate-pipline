import copy
import json
import zipfile

import pytest

from ppt_rubric.common import read_json, write_json
from ppt_rubric.data import ingest, select_cases
from ppt_rubric.provider import parse_json
from ppt_rubric.render import extract_pptx, case_sources
from ppt_rubric.rubric import aggregate, assign_scores, validate_requirements, validate_rubric, normalize_single_source_duplicates
from ppt_rubric.pipeline import audit_review


@pytest.fixture
def rubric():
    dims = ['fulfillment', 'correctness', 'integrity', 'narrative', 'visual']
    return {'groups': [{'id':f'G{i}', 'dimension':d, 'importance':1, 'rationale':'task importance'} for i,d in enumerate(dims)],
            'items': [{'id':f'R{i}', 'group_id':f'G{i}', 'importance':1, 'source_refs':['Q1'] if i == 0 else [],
                       'basis':'explicit' if i == 0 else 'quality', 'scope':'whole deck', 'evidence_mode':'both',
                       'judge_prompt':f'Observable criterion with specific pass conditions {i}'} for i in range(5)]}


def test_group_budget_survives_splitting(rubric):
    weights = dict(fulfillment=30,correctness=25,integrity=15,narrative=15,visual=15)
    one = assign_scores(copy.deepcopy(rubric), weights)
    extra = copy.deepcopy(rubric['items'][0]);extra['id']='R5';extra['judge_prompt']+=' additional atomic condition'
    rubric['items'].append(extra)
    two = assign_scores(rubric, weights)
    assert sum(x['score'] for x in two['items']) == pytest.approx(100)
    assert sum(x['score'] for x in two['items'] if x['group_id']=='G0') == one['items'][0]['score']


def test_unresolved_is_not_zero_or_dropped(rubric):
    assign_scores(rubric,dict.fromkeys(['fulfillment','correctness','integrity','narrative','visual'],20))
    score = aggregate(rubric,[{'rubric_id':'R0','answer':'yes'},{'rubric_id':'R1','answer':'no'}])
    assert score['score'] is None
    assert score['status']=='incomplete'
    assert (score['score_lower'],score['score_upper'])==(20,80)
    complete = aggregate(rubric,[{'rubric_id':f'R{i}','answer':'yes'} for i in range(5)])
    assert complete['score']==100


def test_rubric_rejects_missing_coverage_and_duplicates(rubric):
    req={'requirements':[{'id':'Q1'},{'id':'Q2'}]}
    rubric['items'][1]['judge_prompt']=rubric['items'][0]['judge_prompt']
    errors=validate_rubric(rubric,req)
    assert any('Uncovered' in e for e in errors)
    assert any('duplicate judge' in e for e in errors)


def test_explicit_fact_cannot_be_scored_in_both_content_dimensions(rubric):
    rubric['items'][1]['source_refs']=['Q1']
    rubric['items'][1]['basis']='explicit'
    errors=validate_rubric(rubric,{'requirements':[{'id':'Q1'}]})
    assert any('duplicate explicit scoring' in e for e in errors)


def test_normalization_preserves_fact_coverage_and_correctness_criterion(rubric):
    rubric['items'][1]['source_refs']=['Q1']
    rubric['items'][1]['basis']='explicit'
    original=copy.deepcopy(rubric['items'][1])
    normalized, changes=normalize_single_source_duplicates(rubric)
    assert len(changes)==1 and changes[0]['removed_item']=='R0'
    assert next(i for i in normalized['items'] if i['id']=='R1')==original
    assert {r for i in normalized['items'] for r in i['source_refs']}=={'Q1'}
    # Repeated normalization is stable and never edits the retained judge text.
    assert normalize_single_source_duplicates(normalized)==(normalized,[])


def test_verbatim_grounding():
    q={'requirements':[{'id':'Q1','text':'Keep uncertainty','importance':3,'source_id':'prompt','quote':'待考'}]}
    validate_requirements(q,[{'id':'prompt','text':'这个信息待考。'}])
    q['requirements'][0]['quote']='确定无疑'
    with pytest.raises(ValueError):validate_requirements(q,[{'id':'prompt','text':'这个信息待考。'}])


def test_standalone_judge_cannot_reference_other_rubric_items(rubric):
    rubric['items'][1]['judge_prompt'] = '此项只检查图表，数值准确性由R0负责，不重复处罚。'
    assert any('references other rubric ids' in e for e in validate_rubric(rubric, {'requirements':[{'id':'Q1'}]}))


def test_internal_requirement_reference_is_not_available_to_standalone_judge(rubric):
    rubric['items'][1]['judge_prompt']='关键数据应与Q1所述内容完全一致，不出现矛盾数字。'
    assert any('internal requirement ids' in e for e in validate_rubric(rubric,{'requirements':[{'id':'Q1','quote':'increase to 92%'}]}))
    rubric['items'][1]['judge_prompt']='For the Q1 fiscal quarter, show the required sales number clearly.'
    assert not any('internal requirement ids' in e for e in validate_rubric(rubric,{'requirements':[{'id':'Q1','quote':'Q1 fiscal quarter'}]}))


def test_exact_count_threshold_requires_original_source_support(rubric):
    rubric['items'][0]['judge_prompt']='下一步计划应包括研发方向、时间节点或里程碑中的至少两项，否则不通过。'
    errors=validate_rubric(rubric,{'requirements':[{'id':'Q1','quote':'20分钟，含技术路线图与下一步计划'}]})
    assert any('exact count thresholds' in e for e in errors)
    rubric['items'][0]['judge_prompt']='报告必须至少包含20页，内容清晰且完整。'
    assert not any('exact count thresholds' in e for e in validate_rubric(rubric,{'requirements':[{'id':'Q1','quote':'报告至少二十页'}]}))


def test_normalization_requires_separate_context_coverage(rubric):
    rubric['items'][0]['source_refs']=['Q1','Q2']
    rubric['items'][1].update(source_refs=['Q1'], basis='explicit')
    # A second, uncovered requirement must never disappear with the duplicated fact.
    assert normalize_single_source_duplicates(rubric)==(rubric,[])
    extra=copy.deepcopy(rubric['items'][0])
    extra.update(id='R5',source_refs=['Q2'],judge_prompt='Separately assess the report context requirement')
    rubric['items'].append(extra)
    normalized,changes=normalize_single_source_duplicates(rubric)
    assert changes[0]['removed_item']=='R0'
    assert changes[0]['separately_covered_context_refs']==['Q2']
    assert {ref for i in normalized['items'] for ref in i['source_refs']}=={'Q1','Q2'}


def test_manifest_blinding_is_reproducible_and_frozen(tmp_path):
    for i in range(10):
        for model in ['glm','qwen']:
            d=tmp_path/'source'/f'{i+1:02d}'/model;d.mkdir(parents=True)
            (d/'prompt.txt').write_text(f'task {i}')
            (d/'result.pptx').write_bytes(f'{model}:{i}'.encode())
    path=tmp_path/'manifest.json'
    a=ingest(tmp_path/'source',path)
    b=ingest(tmp_path/'source',path)
    assert a==b
    assert sum(c['split']=='holdout' for c in a['cases'])==3
    assert len(select_cases(a))==7
    with pytest.raises(ValueError):select_cases(a,split='holdout')
    (tmp_path/'source'/'01'/'glm'/'result.pptx').write_bytes(b'changed')
    with pytest.raises(ValueError):ingest(tmp_path/'source',path)


def test_manifest_rejects_tampering_and_unsafe_case_ids():
    from ppt_rubric.common import digest
    from ppt_rubric.data import validate_manifest
    m={'cases':[{'id':'../escape','prompt':'task','split':'dev','candidates':[{'id':'A'},{'id':'B'}]}]}
    m['id']=digest(m)
    with pytest.raises(ValueError,match='safe path'):validate_manifest(m)
    m['cases'][0]['id']='01'
    with pytest.raises(ValueError,match='fingerprint'):validate_manifest(m)


def test_slide_relationship_order_and_notes_exclusion(tmp_path):
    path=tmp_path/'test.pptx'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('ppt/presentation.xml','''<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="rB"/><p:sldId r:id="rA"/></p:sldIdLst></p:presentation>''')
        z.writestr('ppt/_rels/presentation.xml.rels','''<Relationships><Relationship Id="rA" Target="slides/slide1.xml"/><Relationship Id="rB" Target="slides/slide9.xml"/></Relationships>''')
        for n,text in [(1,'Second'),(9,'First')]:
            z.writestr(f'ppt/slides/slide{n}.xml',f'''<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>''')
        z.writestr('ppt/notesSlides/notesSlide1.xml','secret model identity')
    assert [s['text'] for s in extract_pptx(path)]==['First','Second']


def test_attachments_never_silently_omitted(tmp_path):
    bad=tmp_path/'source.xlsx';bad.write_bytes(b'not supported')
    with pytest.raises(ValueError):case_sources({'prompt':'hello','attachments':[str(bad)]},{'max_case_text_chars':1000})


def test_ingest_shared_prompt_and_attachments_are_frozen(tmp_path):
    folder=tmp_path/'source'/'01'
    for name in ['one','two','three']:
        d=folder/name;d.mkdir(parents=True)
        (d/'result.pptx').write_bytes(name.encode())
    (folder/'prompt.txt').write_text('Use the attached facts')
    (folder/'attachments').mkdir()
    attachment=folder/'attachments'/'facts.txt'
    attachment.write_text('Project budget: 100')
    manifest=ingest(tmp_path/'source',tmp_path/'manifest.json')
    case=manifest['cases'][0]
    assert len(case['candidates'])==3 and len(case['attachments'])==1
    assert case_sources(case,{'max_case_text_chars':1000})[1]['text']=='Project budget: 100'
    attachment.write_text('Project budget: 200')
    with pytest.raises(ValueError,match='changed since manifest freeze'):
        case_sources(case,{'max_case_text_chars':1000})


def test_observer_must_cite_real_pages_and_requirements():
    from ppt_rubric.pipeline import validate_observations
    result={'observations':[{'pages':[4],'finding':'overlap','suggested_general_criterion':'No overlapping text',
                            'basis':'quality','requirement_refs':[]}],'render_concerns':[]}
    with pytest.raises(ValueError,match='pages'):validate_observations(result,pages=3,refs={'Q1'})
    result['observations'][0].update(pages=[1],requirement_refs=['Q9'])
    with pytest.raises(ValueError,match='requirements'):validate_observations(result,pages=3,refs={'Q1'})


def test_pdf_attachment_with_text_and_graphic_is_not_silently_reduced_to_text(tmp_path):
    import fitz
    path=tmp_path/'visual-evidence.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((50,50),'The diagram below contains additional evidence.')
        page.draw_rect(fitz.Rect(50,100,200,200),color=(1,0,0))
        pdf.save(path)
    with pytest.raises(ValueError,match='visual elements'):
        case_sources({'prompt':'Use this source','attachments':[str(path)]},{'max_case_text_chars':1000})


def test_review_cannot_approve_errors(rubric):
    with pytest.raises(ValueError):
        audit_review({'approved':True,'issues':[{'severity':'error','reason':'Ungrounded'}]}, {'requirements':[]}, rubric)


def test_json_parser_does_not_accept_prose_or_arrays():
    assert parse_json('```json\n{"ok":true}\n```')=={'ok':True}
    with pytest.raises(ValueError):parse_json('[]')
    with pytest.raises(ValueError):parse_json('Here is my answer {"ok":true}')


@pytest.mark.parametrize('text,expected', [
    ('如120÷6.6写成18.2这类精度错误也算不通过', True),
    ('如100/6算成16.7这种计算错误应不通过', True),
    ('如0.1+0.2写成0.3这类算术错误', True),
    ('如2.5×4写成10这类计算错误', True),
    ('如5−2记为3属于算术错误', True),
    ('如120÷6.6写成18.3这类精度错误', False),
    ('120÷6.6写成18.2不是错误', False),
    ('精确结果120÷6.6写成18.2这类精度错误', False),
    ('保留两位小数；120÷6.6写成18.2这类精度错误', False),
    ('如1/0写成0这类算术错误', False),
])
def test_numerical_negative_examples(text, expected):
    from ppt_rubric.rubric import false_arithmetic_examples
    assert bool(false_arithmetic_examples(text)) is expected
    assert not false_arithmetic_examples(text, '结果须保留两位小数')


def test_false_rounding_example_blocks_rubric_approval(rubric):
    rubric['items'][1]['judge_prompt'] = '每步计算正确，如100÷6写成16.7这类精度错误也算不通过。'
    assert any('valid arithmetic/rounding' in e for e in validate_rubric(rubric, {'requirements': [{'id': 'Q1'}]}))
