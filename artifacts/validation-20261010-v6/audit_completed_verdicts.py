"""Read-only lexical diagnostic; never changes verdicts or claims semantic completeness."""
import json,re
from pathlib import Path
root=Path(__file__).resolve().parent
records=[]
for d in [root.parent/'validation-20261010-v3'/'judgments',root/'judgments']:
 for f in sorted(d.glob('*/*.json')):
  if f.name not in {'A.json','B.json'}:continue
  report=json.loads(f.read_text())
  for v in report.get('verdicts',[]):
   evidence=v.get('evidence','')
   explicit_no=re.search(r'(?:本条|最终|因此|故|应)[^。\n]{0,14}(?:判(?:定)?(?:为)?|答案(?:为)?)[ ：:]*[「“\"]?no\b',evidence,re.I)
   if v.get('answer')=='yes' and explicit_no:
    records.append({'file':str(f),'case_id':report['case_id'],'candidate_id':report['candidate_id'],'rubric_id':v['rubric_id'],'answer':v['answer'],'evidence':evidence,'finding':'explicit_no_in_evidence_with_structured_yes'})
output={'scope':'completed reports only','policy':'Diagnostic flags require review; raw scores unchanged. Absence of a flag does not prove semantic consistency.','flags':records}
(root/'verdict-consistency-audit.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
print('Explicit answer/evidence contradiction flags:',len(records))
