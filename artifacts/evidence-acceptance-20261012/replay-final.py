from run import *
from ppt_rubric.visual_regions import image_region_evidence
config,sources,decks=setup()
dest=OUT/'v10-replay';dest.mkdir(exist_ok=True)
payload=read_json(OUT/'v9/A-R14-input.json')
raw_path=OUT/'invalid-responses/cbdc0f54117245c994fe877fa04e83fa-11.json'
raw=read_json(raw_path)
regions=image_region_evidence(decks['A'],payload['rendered_text_geometry'],config)
ledger=payload['ledger']+regions
rubric=read_json(ROOT/'artifacts/validation-20261012-evidence-k3-v1/rubrics/09/rubric.json')
modes={m['id']:m for m in rubric['failure_modes']};item=next(i for i in rubric['items'] if i['id']=='R14')
inspections=read_json(OUT/'v8/A-R14-inspections.json')
result=prepare_evidence_verdict(raw,len(decks['A']['slides']),item,ledger,modes,sources,inspections)
write_json(dest/'A-R14-result.json',result)
write_json(dest/'provenance.json',{'validation_type':'offline_revalidation_of_unchanged_real_model_response',
 'response_path':str(raw_path.relative_to(ROOT)),'response_sha256':file_digest(raw_path),
 'model_prompt_version':'2026-10-13.9','current_prompt_version':prompts.VERSION,
 'model_answer_before':raw['answer'],'model_answer_after':result['answer'],
 'additional_api_calls':0,'changes':'Register actual supplied crop IDs and compile explicit inspection defect confirmations. No semantic verdict edited.',
 'limitation':'Current prompt wording .10 was not called online; parser replay used actual .9 output.'})
shutil.copytree(ROOT/'ppt_rubric',dest/'frozen-source/ppt_rubric',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
write_json(dest/'hashes.json',{str(p.relative_to(ROOT)):file_digest(p) for p in (ROOT/'ppt_rubric').glob('*.py')})
write_json(ROOT/'fixtures/evidence-regressions/case09-region-citations.json',{'raw':raw,'inspections':[{k:v for k,v in i.items() if k!='image'} for i in inspections],'regions':regions})
print(result['answer'],result['failure_ids'])
