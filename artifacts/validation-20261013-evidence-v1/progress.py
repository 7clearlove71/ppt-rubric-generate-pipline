from pathlib import Path
import sys,collections
ROOT=Path(__file__).resolve().parent;PROJECT=ROOT.parents[1];sys.path.insert(0,str(PROJECT))
from ppt_rubric.common import read_json
from control import budget
budget()
for provider,folder in [('bailian','rubrics'),('maas','judgments')]:
    f=ROOT/folder/'requests.json'
    rows=read_json(f) if f.exists() else []
    events=[]
    for row in rows[-4:]:
        event={k:row[k] for k in ('request_id','role','status')}
        cache=PROJECT/'.cache/api'/(row['cache_key']+'.json')
        if row['status']=='success' and cache.exists():
            value=read_json(cache)['parsed']
            event.update({k:value[k] for k in ('page','complete','answer','approved','consistent','overlapping') if k in value})
        if row.get('error_type'):event['error']=row.get('validation_error',row['error_type'])
        events.append(event)
    print(provider,{'actual_requests':len(rows),'statuses':dict(collections.Counter(r['status'] for r in rows)),'recent':events})
for f in sorted((ROOT/'judgments').glob('??/*-initial.json')):
    x=read_json(f);print(str(f.relative_to(ROOT)),dict(collections.Counter(v['answer'] for v in x.values())))
for f in sorted((ROOT/'judgments').glob('??/consistency.json')):
    x=read_json(f);print(str(f.relative_to(ROOT)),'criteria reviewed',len(x))
for f in ROOT.glob('*-exit.json'):print(f.name,read_json(f))

for f in sorted((ROOT/'rubrics').glob('??/status.json')):
    x=read_json(f);print(str(f.relative_to(ROOT)),x)
