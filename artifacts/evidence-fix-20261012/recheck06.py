from verify import *
config,c=client('generation')
config['max_revisions']=1
case=CASES['06'];sources=case_sources(case,config);decks=prepare_case(case,config,ROOT/'.cache')
requirements=read_json(OUT/'audit06/06/requirements.json')
draft=read_json(OUT/'audit06/06/draft-2.json')
context={'sources':sources,'requirements':requirements,'dimension_weights':config['dimension_weights']}
write_json(OUT/'recheck06/config.json',{**config,'reviser_role':'reviser','pilot_only':True})
write_json(OUT/'recheck06/06/requirements.json',requirements)
result=review_and_repair(case,config,c,OUT/'recheck06/06',context,draft,
    provenance(case,config,sources,decks),reviser_role='reviser')
write_json(OUT/'recheck06/result.json',{'status':result['status'],'items':len(result.get('items',[])),
    'combined_actual_requests':len(c.attempts),'request_journal':'../audit06/requests.json'})
print('06 bounded recheck:',result['status'],len(c.attempts),'combined requests',flush=True)
