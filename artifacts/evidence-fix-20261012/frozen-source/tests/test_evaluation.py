import pytest

from ppt_rubric.common import write_json
from ppt_rubric.evaluation import agreement


def make_pair(root, case, a, b, status='complete'):
    for cid, score in [('A',a),('B',b)]:
        write_json(root/case/f'{cid}.json', {'candidate_hash':cid+'hash', 'rubric_hash':'shared',
                                           'summary':{'status':status,'score':score}})


def test_pairwise_ties_and_partial_reports(tmp_path):
    cases=[{'id':i,'split':'dev','candidates':[{'id':c,'sha256':c+'hash'} for c in 'AB']} for i in ['01','02','03']]
    manifest={'id':'frozen','cases':cases}
    labels={'manifest_id':'frozen','labels':[{'case_id':i,'winner':w,'reasons':'Independent reasons',
                                           'candidate_hashes':{'A':'Ahash','B':'Bhash'}}
                                          for i,w in [('01','A'),('02','tie'),('03','B')]]}
    make_pair(tmp_path,'01',80,60)
    make_pair(tmp_path,'02',71,69)
    make_pair(tmp_path,'03',None,None,'incomplete')
    result=agreement(manifest,labels,tmp_path,tie_margin=3)
    assert result['n']==2
    assert result['agreement']==1
    assert result['predicted_ties']==1
    assert result['evaluation_coverage']==2/3
    assert result['matched_fraction_of_decided_labels']==2/3
    assert result['all_decided_cases_evaluated'] is False
    assert result['skipped']==[{'case_id':'03','reason':'incomplete automated evaluation'}]
    assert 0 < result['wilson_95_interval'][0] < 1
    labels['labels'][0]['candidate_hashes']['A']='wrong'
    with pytest.raises(ValueError):agreement(manifest,labels,tmp_path)


def test_human_undecided_is_neither_tie_nor_model_failure(tmp_path):
    manifest={'id':'frozen','cases':[{'id':'01','split':'dev','candidates':
                                     [{'id':c,'sha256':c+'hash'} for c in 'AB']}]}
    labels={'manifest_id':'frozen','labels':[{'case_id':'01','winner':None,'label_status':'undecided',
            'reasons':'Cannot choose','candidate_hashes':{'A':'Ahash','B':'Bhash'}}]}
    make_pair(tmp_path,'01',90,50)
    result=agreement(manifest,labels,tmp_path)
    assert result['n']==0 and result['agreement'] is None
    assert result['decided_labels']==0 and result['human_ties']==0
    assert result['all_cases_comparable'] is False
    assert result['skipped']==[{'case_id':'01','reason':'human undecided'}]
    with pytest.raises(ValueError):
        agreement(manifest,labels,tmp_path,tie_margin=float('nan'))


@pytest.mark.parametrize('field', ['judge_validator_version', 'judge_prompt_fingerprint'])
def test_comparison_rejects_mixed_judge_versions(tmp_path, field):
    from ppt_rubric.common import read_json
    manifest = {'id': 'frozen', 'cases': [{'id': '01', 'split': 'dev', 'candidates':
                [{'id': c, 'sha256': c+'hash'} for c in 'AB']}]}
    labels = {'manifest_id': 'frozen', 'labels': [{'case_id': '01', 'winner': 'A',
              'reasons': 'Independent review', 'candidate_hashes': {'A': 'Ahash', 'B': 'Bhash'}}]}
    make_pair(tmp_path, '01', 90, 70)
    report = read_json(tmp_path/'01/A.json')
    report[field] = 3 if field == 'judge_validator_version' else 'new-version'
    write_json(tmp_path/'01/A.json', report)
    with pytest.raises(ValueError, match='different judge versions'):
        agreement(manifest, labels, tmp_path)
