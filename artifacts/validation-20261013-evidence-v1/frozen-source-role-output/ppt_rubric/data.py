from __future__ import annotations

import html
import random
import shutil
from pathlib import Path

from .common import digest, file_digest, read_json, write_json


def validate_manifest(manifest):
    if manifest.get('id') != digest({k:v for k,v in manifest.items() if k!='id'}):
        raise ValueError('Manifest fingerprint mismatch; do not edit a frozen manifest in place')
    cases=manifest.get('cases')
    if not isinstance(cases,list) or not cases:
        raise ValueError('Manifest must contain cases')
    seen=set()
    def safe_id(value):
        return (isinstance(value,str) and bool(value) and value not in {'.','..'}
                and not any(c in value for c in '/\\\r\n\x00'))
    for case in cases:
        cid=case.get('id')
        if not safe_id(cid) or cid in seen:
            raise ValueError('Case ids must be unique safe path components')
        seen.add(cid)
        if not isinstance(case.get('prompt'),str) or not case['prompt'].strip() or case.get('split') not in {'dev','holdout'}:
            raise ValueError('Each case requires a nonempty prompt and a valid split')
        candidates=case.get('candidates',[])
        ids=[c.get('id') for c in candidates]
        if len(ids)<2 or any(not safe_id(i) for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('At least two uniquely identified candidates required')


def ingest(source, output, seed=20260930):
    source, output = Path(source).resolve(), Path(output)
    cases = []
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        candidates = sorted(p for p in folder.iterdir() if (p / "result.pptx").exists())
        if len(candidates) < 2:
            raise ValueError(f"Case {folder.name}: at least two PPTX candidates required")
        shared = folder / 'prompt.txt'
        prompts = [((p / 'prompt.txt') if (p / 'prompt.txt').exists() else shared).read_text(encoding='utf-8').strip()
                   for p in candidates]
        if shared.exists():
            prompts.append(shared.read_text(encoding='utf-8').strip())
        if len(set(prompts)) != 1:
            raise ValueError(f"Case {folder.name}: candidate prompts differ")
        random.Random(f"{seed}:{folder.name}").shuffle(candidates)
        attachments = [{'path':str(p.resolve()),'sha256':file_digest(p)}
                       for p in sorted((folder / 'attachments').rglob('*')) if p.is_file() and p.name != '.DS_Store']
        cases.append({"id": folder.name, "prompt": prompts[0], "attachments": attachments,
                      "candidates": [{"id": chr(65 + i), "pptx": str(p / "result.pptx"),
                                      "sha256": file_digest(p / "result.pptx")}
                                     for i, p in enumerate(candidates)]})
    order = [c["id"] for c in cases]
    random.Random(seed).shuffle(order)
    holdout = set(order[max(1, round(len(order) * .7)):])
    for case in cases:
        case["split"] = "holdout" if case["id"] in holdout else "dev"
    manifest = {"schema_version": 1, "seed": seed, "cases": cases}
    manifest["id"] = digest(manifest)
    validate_manifest(manifest)
    if output.exists() and read_json(output) != manifest:
        raise ValueError("Refusing to overwrite a different frozen manifest")
    write_json(output, manifest)
    return manifest


def select_cases(manifest, split="dev", ids=None, allow_holdout=False):
    cases = manifest["cases"]
    if ids:
        wanted = set(ids.split(","))
        found = {c["id"] for c in cases}
        if wanted - found:
            raise ValueError(f"Unknown case ids: {sorted(wanted - found)}")
        cases = [c for c in cases if c["id"] in wanted]
    else:
        cases = [c for c in cases if split == "all" or c["split"] == split]
    if not allow_holdout and any(c["split"] == "holdout" for c in cases):
        raise ValueError("Holdout model evaluation requires --allow-holdout after protocol freeze")
    return cases


def export_human_review(manifest, prepared, output):
    """Static, local-only annotation page; no predicted scores or model identities."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    items = []
    mapping = {}
    for case in manifest["cases"]:
        dest = output / case["id"]
        dest.mkdir(exist_ok=True)
        (dest / "prompt.txt").write_text(case["prompt"], encoding="utf-8")
        links = []
        for candidate in case["candidates"]:
            cid = candidate["id"]
            p = prepared[(case["id"], cid)]
            shutil.copy2(p["pdf"], dest / f"{cid}.pdf")
            shutil.copy2(candidate["pptx"], dest / f"{cid}.pptx")
            links.append(f'<a href="{case["id"]}/{cid}.pdf" target="_blank">{cid} PDF</a> · <a href="{case["id"]}/{cid}.pptx">{cid} PPTX</a>')
        mapping[case["id"]] = {c["id"]: c["sha256"] for c in case["candidates"]}
        options = ''.join(f'<option>{c["id"]}</option>' for c in case["candidates"])
        items.append(f'''<section data-id="{case['id']}"><h2>用例 {case['id']}</h2>
<pre>{html.escape(case['prompt'])}</pre><p>{' &nbsp; | &nbsp; '.join(links)}</p>
<label>综合判断 <select class="winner"><option value="">未评</option>{options}<option value="tie">基本持平</option></select></label>
<label>主要理由（需求落实、正确性、表达、视觉）<textarea class="reasons" rows="4"></textarea></label>
<label>快速浏览偏好（可选）<select class="impression"><option value="">未填</option>{options}<option value="tie">基本持平</option></select></label></section>''')
    import json
    template = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>PPT 人工盲评</title>
<style>body{max-width:1000px;margin:40px auto;font:16px/1.6 system-ui;background:#f5f6f8;color:#182230}section{background:white;padding:24px;margin:24px 0;border-radius:12px}pre{white-space:pre-wrap;font:inherit}textarea{width:100%;box-sizing:border-box}label{display:block;margin:12px 0}select,button{padding:8px}a{color:#2059c9}header{position:sticky;top:0;background:#f5f6f8;padding:12px 0}</style>
<header><h1>PPT 人工盲评</h1><p>先独立判断。认真核对需求、事实与不确定性，同时考虑表达和视觉。两者都很差也可以持平。不要参考模型预测。</p><button onclick="save()">下载人工评测 JSON</button><span id="status"></span></header>__ITEMS__
<script>const manifestId=__MANIFEST__;const mapping=__MAPPING__;const key='ppt-review-'+manifestId;
function values(){return [...document.querySelectorAll('section')].map(s=>({case_id:s.dataset.id,winner:s.querySelector('.winner').value,reasons:s.querySelector('.reasons').value,impression:s.querySelector('.impression').value,candidate_hashes:mapping[s.dataset.id]}))}
function store(){try{localStorage.setItem(key,JSON.stringify(values()));document.querySelector('#status').textContent=' 已保存到本浏览器';}catch(e){document.querySelector('#status').textContent=' 请下载 JSON 保存';}}
try{for(const r of JSON.parse(localStorage.getItem(key)||'[]')){const s=[...document.querySelectorAll('section')].find(s=>s.dataset.id===r.case_id);if(s){s.querySelector('.winner').value=r.winner;s.querySelector('.reasons').value=r.reasons;s.querySelector('.impression').value=r.impression||'';}}}catch(e){}
document.addEventListener('input',store);document.addEventListener('change',store);
function save(){const obj={manifest_id:manifestId,labels:values()};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(obj,null,2)],{type:'application/json'}));a.download='human_labels.json';a.click();URL.revokeObjectURL(a.href)}</script></html>'''
    page = template.replace('__ITEMS__', '\n'.join(items)).replace('__MANIFEST__', json.dumps(manifest['id'])).replace('__MAPPING__', json.dumps(mapping))
    (output / "index.html").write_text(page, encoding="utf-8")
    return str(output / "index.html")
