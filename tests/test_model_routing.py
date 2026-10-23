import copy

import pytest

from ppt_rubric.common import read_json
from ppt_rubric.model_routing import validate_model_routing


@pytest.mark.parametrize('operation,main,other', [
    ('plan', 'k3', 'auditor'), ('generate', 'k3', 'auditor'), ('judge', 'judge', 'verifier')])
def test_formal_roles_are_bound_to_original_models(operation, main, other):
    primary = read_json(f'configs/contract-v24-{main}.json')
    verification = read_json(f'configs/contract-v24-{other}.json')
    result = validate_model_routing(primary, verification, operation)
    assert result['automatic_fallback'] is False
    assert result['roles']['page_visual_primary'] == 'kimi-k3'
    assert result['roles']['independent_content_and_evidence'] == 'kimi-k2.6'
    role = 'judge' if operation == 'judge' else 'generator'
    altered = copy.deepcopy(primary); altered[role + '_model'] = 'glm-5.2'
    with pytest.raises(ValueError, match='kimi-k3'):
        validate_model_routing(altered, verification, operation)
    altered = copy.deepcopy(verification)
    altered[('judge' if operation == 'judge' else 'reviewer') + '_model'] = 'qwen3-vl-plus'
    with pytest.raises(ValueError, match='kimi-k2.6'):
        validate_model_routing(primary, altered, operation)
    with pytest.raises(ValueError, match='separate profile'):
        validate_model_routing(primary, None, operation)


def test_legacy_profiles_not_relabelled_as_formal_model_experiments():
    primary = read_json('configs/contract-v2-k3.json')
    assert validate_model_routing(primary, None, 'generate') is None


def test_export_is_offline_and_does_not_require_a_verification_client():
    primary = read_json('configs/contract-v24-k3.json')
    assert validate_model_routing(primary, None, 'export')['automatic_fallback'] is False
