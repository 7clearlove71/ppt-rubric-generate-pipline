"""Real acceptance probes for the final previous repair; no labels or total scores."""
import copy, sys, shutil
from pathlib import Path
from functools import partial
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from ppt_rubric.common import read_json,write_json,load_config,digest,file_digest
from ppt_rubric.provider import Client
from ppt_rubric.render import prepare_case,case_sources,rendered_text_geometry
from ppt_rubric.evidence_protocol import prepare_evidence_verdict
from ppt_rubric import prompts
from ppt_rubric.visual_regions import focus_images, collect_visual_inspections
OUT=Path(__file__).resolve().parent
OLD=ROOT/'artifacts/evidence-fix-20261012/probes09'
CASE=next(c for c in read_json(ROOT/'data/manifest.json')['cases'] if c['id']=='09')

def setup():
    config=load_config(ROOT/'configs/evidence-v1-judge.json')
    sources=case_sources(CASE,config)
    decks=prepare_case(CASE,config,ROOT/'.cache')
    return config,sources,decks

def plan():
    config,sources,decks=setup()
    write_json(OUT/'config.json',config)
    write_json(OUT/'plan.json',{'operation':'two_full_deck_single_criterion_acceptance_probes',
       'case_id':'09','targets':[['A','R14'],['B','R09']], 'logical_calls':2,'first_pass_http_upper':6,
       'shared_http_cap_including_bounded_repairs':12,'provider':'MaaS Kimi-K2.6','bailian_calls':0,
       'candidate_pages':{k:len(v['slides']) for k,v in decks.items()},'source_hash':digest(sources),
       'full_scores':False,'human_labels_in_inputs':False,'holdout':False,
       'authorization_basis':'User requests continued repair; existing MaaS credential permission. Fresh cap; no previous balance reused.',
       'price':None,'prompt_version':prompts.VERSION})
    shutil.copytree(ROOT/'ppt_rubric',OUT/'source-v6/ppt_rubric',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    write_json(OUT/'source-v6/hashes.json',{str(p.relative_to(ROOT)):file_digest(p) for p in (ROOT/'ppt_rubric').glob('*.py')})
    print('Offline acceptance plan: two criteria, 16 image presentations, MaaS cap 12, Bailian 0.',flush=True)

def run():
    config,sources,decks=setup()
    if (OUT/'shared-budget.json').exists() and read_json(OUT/'shared-budget.json').get('status')=='closed':
        raise RuntimeError('Pilot closed; unused capacity cannot be reused')
    used=len(read_json(OUT/'requests.json')) if (OUT/'requests.json').exists() else 0
    if used>=12: raise RuntimeError('Pilot budget exhausted')
    config['max_api_requests']=12-used
    client=Client(config,ROOT/'.cache',journal_dir=OUT)
    dest=OUT/('v'+prompts.VERSION.rsplit('.',1)[1]);dest.mkdir(exist_ok=True)
    shutil.copytree(ROOT/'ppt_rubric',dest/'frozen-source/ppt_rubric',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    write_json(dest/'config.json',config)
    for cid,rid in [('A','R14'),('B','R09')]:
        payload=read_json(OLD/f'{cid}-{rid}-input.json')
        assert payload['sources']==sources
        if rid=='R14': payload['rendered_text_geometry']=rendered_text_geometry(decks[cid])
        rubric=read_json(ROOT/'artifacts/validation-20261012-evidence-k3-v1/rubrics/09/rubric.json')
        modes={m['id']:m for m in rubric['failure_modes']}
        item=next(i for i in rubric['items'] if i['id']==rid)
        inspections=collect_visual_inspections(decks[cid],payload['rendered_text_geometry'],client,config) if rid=='R14' else []
        if inspections: payload['visual_inspections']=[{k:v for k,v in row.items() if k!='image'} for row in inspections]
        write_json(dest/f'{cid}-{rid}-inspections.json',inspections)
        check=partial(prepare_evidence_verdict,page_count=len(decks[cid]['slides']),item=item,
                      ledger=payload['ledger'],modes=modes,sources=sources,visual_inspections=inspections)
        images=[(f'Slide {s["page"]}',s['image']) for s in decks[cid]['slides']]
        if rid=='R14': images+=focus_images(decks[cid],payload['rendered_text_geometry'],config)
        write_json(dest/f'{cid}-{rid}-images.json',images)
        write_json(dest/f'{cid}-{rid}-input.json',payload)
        try:
            raw=client.ask('judge',prompts.EVIDENCE_JUDGE,payload,
               images,validate=check)
            result=check(raw)
        except Exception as e:result={'status':'error','error_type':type(e).__name__,'message':str(e)}
        write_json(dest/f'{cid}-{rid}-result.json',result)
        print(cid,rid,result.get('answer',result.get('status')),len(client.attempts),'cumulative requests',flush=True)
    write_json(OUT/'run-status.json',{'phase':'initial_probes_complete','actual_requests':len(client.attempts),'prompt_version':prompts.VERSION})

if __name__=='__main__': {'plan':plan,'run':run}[sys.argv[1]]()
