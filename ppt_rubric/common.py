from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".writing-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.writing-',suffix='.jsonl')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_config(path):
    config = read_json(path)
    if config.get('pipeline_stage') not in {None, 'rubric', 'judge'}:
        raise ValueError('pipeline_stage must be rubric or judge when specified')
    if type(config.get('max_visual_focus_images', 12)) is not int or config.get('max_visual_focus_images', 12) < 0:
        raise ValueError('max_visual_focus_images must be a nonnegative integer')
    if type(config.get('max_visual_inspections', 2)) is not int or config.get('max_visual_inspections', 2) < 0:
        raise ValueError('max_visual_inspections must be a nonnegative integer')
    timeouts = config.get('role_timeout_seconds', {})
    if (not isinstance(timeouts, dict) or set(timeouts) - {'generator', 'vision', 'reviewer', 'judge', 'reviser'}
            or any(type(v) not in {int, float} or not math.isfinite(v) or v <= 0 for v in timeouts.values())):
        raise ValueError('role_timeout_seconds must map model roles to positive finite timeouts')
    output_limits = config.get('role_max_output_tokens', {})
    if (not isinstance(output_limits, dict)
            or set(output_limits) - {'generator', 'vision', 'reviewer', 'judge', 'reviser'}
            or any(type(v) is not int or v < 1 for v in output_limits.values())):
        raise ValueError('role_max_output_tokens must map model roles to positive integer limits')
    if config.get('evaluation_protocol', 'legacy') not in {'legacy', 'evidence_v1'}:
        raise ValueError('Unsupported evaluation_protocol')
    weights = config["dimension_weights"]
    if set(weights) != {"fulfillment", "correctness", "integrity", "narrative", "visual"}:
        raise ValueError("Exactly five configured dimensions are required")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in weights.values()) or abs(sum(weights.values()) - 100) > 1e-6:
        raise ValueError("Dimension weights must be positive and sum to 100")
    for key in ['api_concurrency','max_retries','max_output_tokens','max_images_per_request','max_case_text_chars','dpi']:
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError(f'{key} must be a positive integer')
    if type(config.get('max_revisions')) is not int or config['max_revisions'] < 0:
        raise ValueError('max_revisions must be a nonnegative integer')
    refinement = config.get('max_source_refinements', 0)
    if type(refinement) is not int or refinement not in {0,1}:
        raise ValueError('max_source_refinements must be 0 or 1')
    revision_role = config.get('source_refinement_reviser_role', 'generator')
    if revision_role not in {'generator','reviser'} or (revision_role == 'reviser' and not config.get('reviser_model')):
        raise ValueError('Source refinement reviser must be generator, or reviser with a configured model')
    if set(config.get('request_options',{})) & {'model','messages','stream','max_tokens'}:
        raise ValueError('request_options cannot override model/messages/stream/max_tokens')
    return config


def validate_operation_profile(config, operation):
    stage = config.get('pipeline_stage')
    expected = {'generate': 'rubric', 'audit': 'rubric', 'judge': 'judge'}.get(operation)
    if stage is not None and expected is not None and stage != expected:
        raise ValueError(f'{operation} requires a {expected} profile; configured pipeline_stage is {stage}')
