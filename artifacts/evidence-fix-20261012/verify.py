"""Bounded real regressions. Does not generate final scores or use human labels."""
import sys, copy, shutil
from pathlib import Path
from functools import partial

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from ppt_rubric.common import read_json,write_json,load_config,digest,file_digest
from ppt_rubric import prompts
from ppt_rubric.provider import Client
from ppt_rubric.render import case_sources,prepare_case,rendered_text_geometry
from ppt_rubric.pipeline import review_and_repair,provenance
from ppt_rubric.rubric import validate_rubric,VALIDATOR_VERSION,validate_requirements
from ppt_rubric.evidence_protocol import (compile_ownership_boundaries,collect_evidence,prepare_evidence_verdict,
    judgment_evidence,arithmetic_check,validate_consistency)

OUT=Path(__file__).resolve().parent
OLD=ROOT/'artifacts/validation-20261012-evidence-k3-v1'
CASES={c['id']:c for c in read_json(ROOT/'data/manifest.json')['cases']}

def client(kind):
    if (OUT/'shared-budget.json').exists() and read_json(OUT/'shared-budget.json')['status']=='closed':
        raise RuntimeError('This pilot is closed; create a separate bounded run instead of reusing its balance')
    cap=12 if kind=='generation' else 18
    cfg=read_json(OUT/f'{kind}-config.json')
    journal=OUT/('audit06' if kind=='generation' else 'probes09')
    used=len(read_json(journal/'requests.json')) if (journal/'requests.json').exists() else 0
    cfg['max_api_requests']=max(0,cap-used)
    if used>=cap: raise RuntimeError('Closed or exhausted bounded pilot budget')
    return cfg,Client(cfg,ROOT/'.cache',journal_dir=journal)

def plan():
    if (OUT/'plan.json').exists():
        raise RuntimeError('Preserve the existing plan and source snapshot; use a new run directory')
    cfg=load_config(ROOT/'configs/evidence-v1-k3.json')
    judge=load_config(ROOT/'configs/evidence-v1-judge.json');judge['max_api_requests']=18
    write_json(OUT/'generation-config.json',cfg);write_json(OUT/'judge-config.json',judge)
    entries=[]
    for cid in ('01','02','03','05','06','09','10'):
        paths=sorted((OLD/'rubrics'/cid).glob('draft-*.json'))
        latest=read_json(paths[-1]); requirements=read_json(OLD/'rubrics'/cid/'requirements.json')
        sources=case_sources(CASES[cid],cfg);validate_requirements(requirements,sources)
        entries.append({'case_id':cid,'input':str(paths[-1].relative_to(ROOT)),
                        'before':validate_rubric(latest,requirements),
                        'after_compilation':validate_rubric(compile_ownership_boundaries(latest),requirements),
                        'semantic_review_rerun':False,'approval_changed':False})
    write_json(OUT/'offline-replay.json',entries)
    write_json(OUT/'plan.json',{'operation':'bounded_component_regression','human_labels_in_inputs':False,
        'bailian':{'case_ids':['06'],'purpose':'Review last failing draft using production review_and_repair',
                   'max_http_attempts_including_retries_and_resume':12,'unconstrained_logical_upper':8},
        'maas':{'case_ids':['09'],'purpose':'Two real pages, two full-deck single-criterion judgments and one real consistency replay',
                'logical_calls':5,'max_http_attempts_including_retries_and_resume':18},
        'full_case_scores':False,'holdout':False,'price':'unverified; no fee estimate',
        'status':'planned','authorization_basis':'Existing small-scale single-case test allowance; fresh bounded run, no historical balance reused'})
    freeze=OUT/'frozen-source';freeze.mkdir(exist_ok=True)
    for directory in ('ppt_rubric','configs','tests'):
        shutil.copytree(ROOT/directory,freeze/directory,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    paths=list((ROOT/'ppt_rubric').glob('*.py'))+list((ROOT/'configs').glob('*.json'))
    write_json(OUT/'source-hashes.json',{str(p.relative_to(ROOT)):file_digest(p) for p in paths})
    print('Offline plan and source snapshot saved',flush=True)

def audit():
    cfg,c=client('generation');case=CASES['06'];output=OUT/'audit06/06'
    sources=case_sources(case,cfg);decks=prepare_case(case,cfg,ROOT/'.cache')
    requirements=read_json(OLD/'rubrics/06/requirements.json')
    draft=read_json(OLD/'rubrics/06/draft-2.json')
    write_json(output/'requirements.json',requirements)
    context={'sources':sources,'requirements':requirements,'dimension_weights':cfg['dimension_weights']}
    result=review_and_repair(case,cfg,c,output,context,draft,provenance(case,cfg,sources,decks))
    write_json(OUT/'audit06/result.json',{'status':result['status'],'items':len(result.get('items',[])),'actual_requests':len(c.attempts)})
    print('06 review:',result['status'],len(c.attempts),'requests',flush=True)

def probes():
    cfg,c=client('judge');case=CASES['09'];output=OUT/'probes09'
    sources=case_sources(case,cfg);decks=prepare_case(case,cfg,ROOT/'.cache')
    rubric=compile_ownership_boundaries(read_json(OLD/'rubrics/09/rubric.json'))
    modes={m['id']:m for m in rubric['failure_modes']}
    # These are component tests against fixed historical criteria, not newly approved total scores.
    for cid,page,rid in [('A',1,'R14'),('B',4,'R09')]:
        deck=decks[cid]
        single={**deck,'slides':[s for s in deck['slides'] if s['page']==page]}
        evidence=collect_evidence(single,rubric,sources,c)
        write_json(output/f'{cid}-page{page}-evidence.json',evidence)
        print(cid,'page',page,'complete',evidence['complete'],flush=True)
        if not evidence['complete']:continue
        old=read_json(OLD/f'judgments/09/{cid}-evidence.json')
        ledger=[copy.deepcopy(row) for row in old['ledger'] if row['page']!=page]+evidence['ledger']
        for row in ledger:
            row['arithmetic_checks']=[arithmetic_check(x,row['quote']+'\n'+'\n'.join(s['text'] for s in sources)) for x in row['calculations']]
        item=next(i for i in rubric['items'] if i['id']==rid)
        check=partial(prepare_evidence_verdict,page_count=len(deck['slides']),item=item,ledger=ledger,modes=modes,sources=sources)
        payload={'sources':sources,'ledger':[judgment_evidence(r) for r in ledger],'criterion':item['judge_prompt'],'scope':item['scope'],
                 'owned_failure_modes':[m for m in rubric['failure_modes'] if m['owner']==rid],
                 'required_resolution_ids':[r['id'] for r in ledger if r['failure_mode'] is not None and modes[r['failure_mode']]['owner']==rid]}
        if rid=='R14': payload['rendered_text_geometry']=rendered_text_geometry(deck)
        write_json(output/f'{cid}-{rid}-input.json',payload)
        try:
            raw=c.ask('judge',prompts.EVIDENCE_JUDGE,payload,[(f'Slide {s["page"]}',s['image']) for s in deck['slides']],validate=check)
            result=check(raw)
        except Exception as e: result={'status':'error','error_type':type(e).__name__,'message':str(e)}
        write_json(output/f'{cid}-{rid}-verdict.json',result)
        print(cid,rid,result.get('answer',result.get('status')),flush=True)
    history=read_json(OLD/'judgments/09/consistency.json')['R04']
    verdicts={x['candidate']:x['rejudgment'] for x in history if 'rejudgment' in x}
    item=next(i for i in rubric['items'] if i['id']=='R04')
    candidates={}
    for cid,v in verdicts.items():
        selected=set(v['evidence_ids']);ledger=read_json(OLD/f'judgments/09/{cid}-evidence.json')['ledger']
        selected.update(r['id'] for r in ledger if r['failure_mode'] and modes[r['failure_mode']]['owner']=='R04')
        evidence=[]
        for row in ledger:
            if row['id'] not in selected: continue
            row['arithmetic_checks']=[arithmetic_check(x,row['quote']+'\n'+'\n'.join(s['text'] for s in sources)) for x in row['calculations']]
            evidence.append(judgment_evidence(row))
        candidates[cid]={'verdict':{k:v[k] for k in ('answer','pages','evidence','evidence_ids','failure_ids','resolutions')},'evidence':evidence}
    check=partial(validate_consistency,candidate_evidence={cid:{r['id'] for r in d['evidence']} for cid,d in candidates.items()},candidates=candidates,criterion=item['judge_prompt'])
    payload={'sources':sources,'criterion':item['judge_prompt'],'scope':item['scope'],
             'owned_failure_modes':[m for m in rubric['failure_modes'] if m['owner']=='R04'],'candidates':candidates}
    write_json(output/'R04-consistency-input.json',payload)
    try:
        result=c.ask('reviewer',prompts.CONSISTENCY_REVIEW,payload,validate=check);check(result)
    except Exception as e:result={'status':'error','error_type':type(e).__name__,'message':str(e)}
    write_json(output/'R04-consistency.json',result)
    print('R04 consistency:',result.get('consistent',result.get('status')),len(c.attempts),'requests',flush=True)

if __name__=='__main__':
    {'plan':plan,'audit':audit,'probes':probes}[sys.argv[1]]()
