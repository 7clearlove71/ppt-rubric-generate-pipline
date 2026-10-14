from run import *
from ppt_rubric.evidence_protocol import criterion_ledger
from ppt_rubric.visual_regions import validate_visual_inspection
if (OUT/'shared-budget.json').exists() and read_json(OUT/'shared-budget.json').get('status')=='closed':
    raise RuntimeError('Pilot closed')
config,sources,decks=setup()
used=len(read_json(OUT/'requests.json'))
if used>=12:raise RuntimeError('Budget exhausted')
config['max_api_requests']=12-used
config['max_retries']=1  # Final bounded probe and one non-overlap control; no extra recovery.
client=Client(config,ROOT/'.cache',journal_dir=OUT)
dest=OUT/'v9';dest.mkdir(exist_ok=True)
shutil.copytree(ROOT/'ppt_rubric',dest/'frozen-source/ppt_rubric',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
write_json(dest/'config.json',config)
payload=read_json(OUT/'v8/A-R14-input.json')
rubric=read_json(ROOT/'artifacts/validation-20261012-evidence-k3-v1/rubrics/09/rubric.json')
modes={m['id']:m for m in rubric['failure_modes']};item=next(i for i in rubric['items'] if i['id']=='R14')
payload['ledger']=criterion_ledger(payload['ledger'],item,modes)
payload['required_resolution_ids']=[r['id'] for r in payload['ledger'] if r['failure_mode'] is not None and modes[r['failure_mode']]['owner']==item['id']]
inspections=read_json(OUT/'v8/A-R14-inspections.json')
check=partial(prepare_evidence_verdict,page_count=len(decks['A']['slides']),item=item,
    ledger=payload['ledger'],modes=modes,sources=sources,visual_inspections=inspections)
images=read_json(OUT/'v8/A-R14-images.json')
write_json(dest/'A-R14-input.json',payload);write_json(dest/'A-R14-images.json',images)
write_json(dest/'plan.json',{'scope':'case09 A/R14 and image-only case09 B/page4 negative control',
    'logical_calls':2,'max_http_attempts':2,'shared_request_cap':12,'used_before':used,
    'negative_control_expected':{'overlapping':False},'expectation_in_model_input':False,
    'criterion_and_human_labels_unchanged':True})
try:
    raw=client.ask('judge',prompts.EVIDENCE_JUDGE,payload,images,validate=check)
    result=check(raw)
except Exception as e:result={'status':'error','error_type':type(e).__name__,'message':str(e)}
write_json(dest/'A-R14-result.json',result);print('final visual',result.get('answer',result.get('status')),len(client.attempts),flush=True)
control_image=decks['B']['slides'][3]['image']
try:
    control=client.ask('judge',prompts.VISUAL_INSPECTION,{},[('Image',control_image)],validate=validate_visual_inspection)
except Exception as e:control={'status':'error','error_type':type(e).__name__}
write_json(dest/'negative-control.json',{'image':control_image,'result':control})
print('negative control',control.get('overlapping',control.get('status')),len(client.attempts),flush=True)
