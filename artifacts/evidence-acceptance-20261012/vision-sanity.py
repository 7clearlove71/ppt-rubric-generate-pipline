from run import *
if (OUT/'shared-budget.json').exists() and read_json(OUT/'shared-budget.json').get('status')=='closed':
    raise RuntimeError('Pilot closed; unused capacity cannot be reused')
config=load_config(ROOT/'configs/evidence-v1-judge.json')
used=len(read_json(OUT/'requests.json'));config['max_api_requests']=12-used
client=Client(config,ROOT/'.cache',journal_dir=OUT)
images=read_json(OUT/'focus-diagnostic.json')['images']
# Neutral image-only prompt. Expected characters, answer and rubric are absent.
system='客观读取图片，仅输出JSON：{"text_seen":["逐字转录实际能看到的文字"],"overlapping":true或false,"overlap_objects":["若有实际字形相交，列出对象"],"description":"描述实际可见的空间关系与颜色；不猜图外内容"}。文字框可能相交不等于笔画相交，以实际图像为准。'
result=client.ask('judge',system,{},[('Image',images[0][1])])
write_json(OUT/'vision-sanity.json',{'image':images[0][1],'prompt':system,'result':result,'cumulative_requests':len(client.attempts)})
print(result,flush=True)
