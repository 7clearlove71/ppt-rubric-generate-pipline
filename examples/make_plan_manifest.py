"""Create a synthetic, source-only manifest for offline plan demonstration."""
from pathlib import Path
from ppt_rubric.common import digest, write_json

manifest = {
    'schema_version': 1,
    'seed': 20260930,
    'cases': [{
        'id': 'demo', 'split': 'dev',
        'prompt': '制作项目周报PPT：本周完成12项任务，尚有3项未完成。说明下一步计划，未确认的风险须标为待核实。',
        'attachments': [],
        # Plan reads sources only. These are explicit placeholders, not PPTX files.
        'candidates': [
            {'id': name, 'pptx': f'data/demo-placeholder/{name}/result.pptx', 'sha256': '0' * 64}
            for name in ('A', 'B')
        ],
    }],
}
manifest['id'] = digest(manifest)
output = Path('artifacts/demo/manifest.json')
if output.exists():
    raise SystemExit(f'{output} already exists; choose a clean demo directory')
write_json(output, manifest)
print(output)
