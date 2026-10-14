"""One bounded validation round. Read labels only after model work for compare."""
import collections, subprocess, sys, fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parent; PROJECT=ROOT.parents[1]
sys.path.insert(0,str(PROJECT))
from ppt_rubric.common import read_json,write_json,file_digest
from ppt_rubric.operations import plan_run,inspect_suite
from ppt_rubric.evaluation import agreement
REGISTRY={'bailian':['rubrics/requests.json'],'maas':['judgments/requests.json']}
CAPS={'bailian':120,'maas':900}

def budget():
    b={'status':read_json(ROOT/'authorization.json')['status']}
    for provider, paths in REGISTRY.items():
        rows=[r for rel in paths for r in (read_json(ROOT/rel) if (ROOT/rel).exists() else [])]
        if len(rows)>CAPS[provider]:raise RuntimeError('Shared budget exceeded')
        b[provider]={'authorized_cap':CAPS[provider],'actual_requests':len(rows),
          'remaining_requests':CAPS[provider]-len(rows),'journals':paths,
          'status_counts':dict(collections.Counter(r['status'] for r in rows)),
          'reported_tokens':sum(r.get('usage',{}).get('total_tokens',0) for r in rows),
          'usage_missing_requests':sum(not r.get('usage') for r in rows),'currency_cost_estimate':None}
    write_json(ROOT/'shared-budget.json',b);return b

def summarize():
    manifest=read_json(PROJECT/'data/manifest.json');auth=read_json(ROOT/'authorization.json')
    config=read_json(ROOT/'judge-config.json')
    cmp=agreement(manifest,read_json(ROOT/'human-labels-dev.json'),ROOT/'judgments',config['tie_margin'],'dev')
    write_json(ROOT/'agreement.json',cmp)
    cases={}
    for cid in auth['scope']:
        directory=ROOT/'rubrics'/cid
        rubric=read_json(directory/'rubric.json') if (directory/'rubric.json').exists() else None
        status=read_json(directory/'status.json') if (directory/'status.json').exists() else {}
        cases[cid]={'rubric_status':rubric['status'] if rubric else status.get('status','not_run'),'candidates':{}}
        for c in 'AB':
            f=ROOT/'judgments'/cid/(c+'.json')
            if f.exists():
                report=read_json(f)
                cases[cid]['candidates'][c]={'summary':report['summary'],'consistency':report['consistency_summary']}
    summary={'status':auth['status'],'prompt_version':read_json(ROOT/'version-snapshot.json')['prompt_version'],
       'cases':cases,'budget':budget(),'agreement':cmp,'holdout_run':False,
       'complete_candidates':sum(v['summary']['status']=='complete' for c in cases.values() for v in c['candidates'].values()),
       'requested_candidates':14}
    write_json(ROOT/'validation-summary.json',summary);return summary

def run(stage):
    auth=read_json(ROOT/'authorization.json')
    if auth['status']!='authorized':raise RuntimeError('Authorization is not active')
    for name,sha in read_json(ROOT/'version-snapshot.json')['files'].items():
        if file_digest(PROJECT/name)!=sha:raise RuntimeError('Frozen source changed: '+name)
    manifest=read_json(PROJECT/'data/manifest.json')
    if stage=='gate':ids=['01'];operation='judge';provider='maas'
    elif stage=='recover-transport':
        ids=[];operation='judge';provider='maas'
        for cid in ['06','09','10']:
            closed=ROOT/('case'+cid+'-final-qa.json')
            if not (closed.exists() and read_json(closed).get('additional_semantic_repair_this_round') is False):ids.append(cid)
    elif stage=='generate':ids=['02','03','05','09','10'];operation='generate';provider='bailian'
    elif stage=='recover-generation':
        ids=[];operation='generate';provider='bailian'
        for cid in auth['scope']:
            path=ROOT/'rubrics'/cid/'status.json'
            status=read_json(path) if path.exists() else {}
            if status.get('status')=='error' and status.get('message','').startswith('Output token limit reached'):
                ids.append(cid)
    elif stage=='judge':
        ids=[];operation='judge';provider='maas'
        for cid in auth['scope']:
            if cid=='01':continue
            closed=ROOT/('case'+cid+'-final-qa.json')
            if closed.exists() and read_json(closed).get('additional_semantic_repair_this_round') is False:continue
            reports=[ROOT/'judgments'/cid/(candidate+'.json') for candidate in 'AB']
            if all(path.exists() and read_json(path).get('summary',{}).get('status')=='complete'
                   for path in reports):continue
            d=ROOT/'rubrics'/cid
            if (d/'rubric.json').exists() and not inspect_suite(read_json(d/'rubric.json'),read_json(d/'requirements.json')):ids.append(cid)
    else:raise ValueError('Unknown stage')
    if not ids:return
    config_path=ROOT/('judge-config.json' if operation=='judge' else 'generation-config.json')
    if stage=='recover-generation':config_path=ROOT/'generation-recovery-config.json'
    config=read_json(config_path)
    plan=plan_run([c for c in manifest['cases'] if c['id'] in ids],config,operation,ROOT/'rubrics')
    write_json(ROOT/(stage+'-plan.json'),plan)
    if plan['ready_cases']!=len(ids):raise RuntimeError('Plan has input errors')
    remaining=budget()[provider]['remaining_requests']
    if remaining<1:raise RuntimeError('Shared provider budget exhausted')
    out=ROOT/('judgments' if operation=='judge' else 'rubrics')
    command=[sys.executable,'-m','ppt_rubric',operation,'--cases',','.join(ids),'--config',str(config_path),
       '--out',str(out),'--workers','1','--max-api-requests',str(remaining)]
    if operation=='judge':command+=['--rubrics',str(ROOT/'rubrics')]
    else:command+=['--confirm-bailian-bulk']
    write_json(ROOT/(stage+'-launch.json'),{'stage':stage,'cases':ids,'remaining_provider_requests':remaining,
       'command':command,'authorization':'authorization.json','plan':stage+'-plan.json'})
    print('Launching',stage,ids,'remaining',remaining,flush=True)
    result=subprocess.run(command,cwd=PROJECT)
    write_json(ROOT/(stage+'-exit.json'),{'returncode':result.returncode})
    summary=summarize()
    print('Stage finished',stage,'code',result.returncode,'complete candidates',summary['complete_candidates'],flush=True)

if __name__=='__main__':
    if sys.argv[1]=='status':
        b=budget();print({p:b[p] for p in CAPS})
    elif sys.argv[1]=='summarize':print(summarize())
    else:
        with (ROOT/'runner.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            run(sys.argv[1])
