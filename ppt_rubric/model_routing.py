"""Explicit formal model roles; availability never silently changes a role."""
from urllib.parse import urlparse


POLICY = 'contract-kimi-v1'
ROLE_MODELS = {
    'source_extraction_and_revision': 'kimi-k3',
    'independent_source_audit': 'kimi-k2.6',
    'content_primary_and_evidence': 'kimi-k3',
    'page_visual_primary': 'kimi-k3',
    'independent_content_and_evidence': 'kimi-k2.6',
}


def validate_model_routing(primary, verification, operation):
    """Opt-in binding for new formal profiles; old research profiles still load."""
    policies = {c.get('model_routing') for c in (primary, verification) if c and c.get('model_routing')}
    if not policies:
        return None
    if policies != {POLICY} or primary.get('model_routing') != POLICY:
        raise ValueError('Unknown or inconsistent formal model routing policy')
    role = 'judge' if operation == 'judge' else 'generator'
    if (primary.get(role + '_model') != 'kimi-k3'
            or urlparse(primary['base_url']).hostname != 'dashscope.aliyuncs.com'):
        raise ValueError('Formal extraction/primary/visual roles require Bailian kimi-k3; no automatic fallback')
    if operation != 'export':
        role = 'judge' if operation == 'judge' else 'reviewer'
        if (not verification or verification.get('model_routing') != POLICY
                or verification.get(role + '_model') != 'kimi-k2.6'
                or urlparse(verification['base_url']).hostname != 'api.modelarts-maas.com'):
            raise ValueError('Formal independent audit/verification requires MaaS kimi-k2.6 in a separate profile')
    return {'policy': POLICY, 'roles': dict(ROLE_MODELS), 'automatic_fallback': False}
