"""Small explicit source-ledger fixtures; never used to migrate real artifacts."""
import copy


def add_ledger(requirements, catalog):
    requirements = copy.deepcopy(requirements)
    targets = []
    for number, requirement in enumerate(requirements['requirements'], 1):
        tid = f'T{number}'
        requirement.update(target_ids=[tid], reference_target_ids=[])
        targets.append({'id': tid, 'kind': requirement['kind'], 'object': requirement['id'],
                        'attribute': '交付内容', 'value': requirement['text'],
                        'unit': 'not_applicable', 'time': 'not_applicable', 'state': 'not_applicable',
                        'mandatory': True, 'condition': {'mode': 'always', 'text': '', 'source_ids': []},
                        'relation': {'type': 'none', 'target_ids': []},
                        'source_ids': list(requirement['source_ids']), 'scoring_owner': requirement['id']})
    requirements['source_targets'] = targets
    requirements['source_coverage'] = []
    for source in catalog:
        ids = [target['id'] for target in targets if source['id'] in target['source_ids']]
        requirements['source_coverage'].append({'source_id': source['id'], 'role': 'required' if ids else 'context',
                                                'target_ids': ids, 'rationale': '明确交付目标' if ids else '背景上下文'})
    return requirements


def approval(requirements):
    targets = requirements['source_targets']
    checks = []
    for target in targets:
        operations = ['change_owned_property']
        if target['condition']['mode'] != 'always':
            operations.append('condition_absent')
        if target['relation']['type'] != 'none':
            operations.append('reverse_relation')
        for operation in operations:
            failed = [] if operation == 'condition_absent' else ['R_' + target['scoring_owner']]
            checks.append({'target_id': target['id'], 'operation': operation,
                           'candidate': '其他独立属性均满足，只改变本目标属性；条件存在时使其成立',
                           'expected_failed_item_ids': failed, 'actual_failed_item_ids': list(failed),
                           'repair': '恢复本目标的原始属性', 'status': 'pass'})
    return {'approved': True, 'issues': [], 'summary': '逐目标审核通过',
            'coverage_checks': [{'source_id': row['source_id'], 'target_ids': list(row['target_ids']),
                                 'status': 'context' if row['role'] == 'context' else 'covered',
                                 'source_goals': ([{'goal': '独立交付目标' + tid,
                                                   'disposition': 'scored', 'target_ids': [tid],
                                                   'rationale': row['rationale']} for tid in row['target_ids']]
                                                  or [{'goal': '背景上下文', 'disposition': 'context',
                                                       'target_ids': [], 'rationale': row['rationale']}]),
                                 'rationale': '原文目标及背景均已核对'} for row in requirements['source_coverage']],
            'target_checks': [{'target_id': target['id'], 'source_ids': list(target['source_ids']),
                               'owner_item_id': 'R_' + target['scoring_owner'], 'status': 'grounded_unique',
                               'rationale': '对象、属性、状态及条件有原文依据，独立责任唯一'} for target in targets],
            'counterexample_checks': checks}
