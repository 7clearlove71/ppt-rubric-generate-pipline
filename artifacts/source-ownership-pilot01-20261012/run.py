"""One bounded K3 repair of a real source-ownership failure; no candidate labels."""
from pathlib import Path
import sys,shutil
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from ppt_rubric.common import read_json,write_json,load_config,digest,file_digest
from ppt_rubric.render import case_sources,prepare_case
from ppt_rubric.pipeline import review_and_repair,provenance
from ppt_rubric.provider import Client
from ppt_rubric.rubric import validate_requirements,validate_rubric
from ppt_rubric.evidence_protocol import compile_ownership_boundaries
from ppt_rubric import prompts
OUT=Path(__file__).resolve().parent
if (OUT/'shared-budget.json').exists() and read_json(OUT/'shared-budget.json').get('status')=='closed':
    raise RuntimeError('Pilot closed; unused capacity cannot be reused')
case=next(c for c in read_json(ROOT/'data/manifest.json')['cases'] if c['id']=='01')
config=load_config(ROOT/'configs/evidence-v1-k3.json')
config['max_retries']=1  # One bounded recovery after two recorded 180s timeouts.
sources=case_sources(case,config)
requirements=read_json(ROOT/'artifacts/validation-20261012-evidence-k3-v1/rubrics/01/requirements.json')
draft=read_json(ROOT/'artifacts/validation-20261012-evidence-k3-v1/rubrics/01/draft-2.json')
validate_requirements(requirements,sources)
write_json(OUT/'recovery-plan.json',{'operation':'single_failed_draft_review_and_repair','case_ids':['01'],
    'provider':'Bailian K3','reviser_role':'reviser','max_revisions':2,'shared_http_cap_including_retries':12,
    'current_errors':validate_rubric(compile_ownership_boundaries(draft),requirements),
    'human_labels_in_input':False,'holdout':False,'fee_estimate':None,
    'authorization_basis':'Existing small-scale single-case repair allowance; not a split bulk run; no old balance reused.'})
write_json(OUT/'config.json',{**config,'audit_reviser_role':'reviser'})
shutil.copytree(ROOT/'ppt_rubric',OUT/'recovery-source/ppt_rubric',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
write_json(OUT/'recovery-hashes.json',{str(p.relative_to(ROOT)):file_digest(p) for p in (ROOT/'ppt_rubric').glob('*.py')})
used=len(read_json(OUT/'requests.json')) if (OUT/'requests.json').exists() else 0
if used>=12:raise RuntimeError('Budget exhausted')
config['max_api_requests']=12-used
client=Client(config,ROOT/'.cache',journal_dir=OUT)
decks=prepare_case(case,config,ROOT/'.cache')
write_json(OUT/'01/requirements.json',requirements)
context={'sources':sources,'requirements':requirements,'dimension_weights':config['dimension_weights']}
result=review_and_repair(case,config,client,OUT/'01',context,draft,
    provenance(case,config,sources,decks),reviser_role='reviser')
write_json(OUT/'result.json',{'status':result['status'],'items':len(result.get('items',[])),
    'actual_requests':len(client.attempts),'prompt_version':prompts.VERSION})
print(result['status'],len(client.attempts),'actual requests',flush=True)
