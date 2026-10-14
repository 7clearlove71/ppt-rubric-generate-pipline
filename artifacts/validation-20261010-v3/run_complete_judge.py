"""Bounded task runner using the project's unchanged per-criterion judge path."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path.cwd()))
from ppt_rubric.common import read_json,write_json,load_config,file_digest
from ppt_rubric.data import select_cases,validate_manifest
from ppt_rubric.pipeline import evaluate
from ppt_rubric.operations import inspect_suite,run_report
from ppt_rubric.provider import Client
from ppt_rubric.evaluation import agreement

roots=[Path('artifacts')/f'validation-20261010-v{i}' for i in [1,2,3]]
r=roots[-1];out=r/'judgments'
def count_requests(stage):
    return sum(len(read_json(d/stage/'requests.json')) for d in roots if (d/stage/'requests.json').exists())
used=count_requests('judgments');left=300-used
assert left>0
snapshot=read_json(r/'version-snapshot.json')
assert all(file_digest(p)==h for p,h in snapshot['code_hashes'].items())
assert all(file_digest(r/p)==h for p,h in snapshot['config_hashes'].items())
m=read_json('data/manifest.json');validate_manifest(m)
c=load_config(r/'judge-config.json')
client=Client({**c,'max_api_requests':left},'.cache',journal_dir=out)
write_json(r/'effective-judge-budget.json',{'previous_requests_all_versions':used,'this_process_max_requests':left,'shared_cap':300})
summary={}
cases=sorted(select_cases(m),key=lambda c:(c['id']=='09',c['id']))
print('Complete judge started: same per-item pipeline, remaining total requests',left,flush=True)
for case in cases:
    cid=case['id'];directory=r/'rubrics'/cid
    state=read_json(directory/'status.json')
    if state['status']!='approved':
        summary[cid]={'status':'generation_failed','reason':state['status']}
        write_json(out/'run-summary.json',summary)
        print('Skipped rejected rubric:',cid,flush=True)
        continue
    try:
        suite=read_json(directory/'rubric.json')
        errors=inspect_suite(suite,read_json(directory/'requirements.json'))
        if errors:raise ValueError('; '.join(errors))
        print('Judging case',cid,'criteria',len(suite['items']),'candidates',len(case['candidates']),flush=True)
        reports=evaluate(case,suite,c,client,'.cache',out)
        summary[cid]={aid:report['summary'] for aid,report in reports.items()}
        print('Finished',cid,{aid:(report['summary']['status'],report['summary']['score']) for aid,report in reports.items()},flush=True)
    except Exception as e:
        summary[cid]={'status':'error','error_type':type(e).__name__,'message':str(e)}
        print('Case error',cid,type(e).__name__,flush=True)
    write_json(out/'run-summary.json',summary)
    write_json(out/'report.json',run_report(out))
    # Labels are loaded only after this case's model calls finish; never in model payloads.
    write_json(r/'agreement.json',agreement(m,read_json(r/'human-labels-dev.json'),out,c['tie_margin']))
    assert count_requests('judgments')<=300
write_json(out/'report.json',run_report(out))
write_json(r/'agreement.json',agreement(m,read_json(r/'human-labels-dev.json'),out,c['tie_margin']))
bailian=count_requests('rubrics');maas=count_requests('judgments')
assert bailian<=120 and maas<=300
write_json(roots[0]/'shared-budget.json',{'bailian_limit':120,'bailian_used':bailian,'bailian_remaining':120-bailian,'maas_limit':300,'maas_used':maas,'maas_remaining':300-maas,'versions':[str(d) for d in roots],'status':'model_runs_finished'})
print('All scheduled judging finished; total MaaS requests',maas,'/300',flush=True)
