"""Deterministic binding and a resumable interpreter for the fixed BT subset.

This module never reads hidden_environment, expected, or reference_trace.
The caller supplies only actual completed results to the generator.
"""
from copy import deepcopy
from pathlib import Path
import json
import re
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / '提示词与接口配置'
VERSION = 'kaxinguo_feasibility_v1'
_VALIDATORS = {}


class PlanError(ValueError):
    """Invalid structure, dependency, argument, branch, or completion control."""


class OutOfScopeInput(ValueError):
    """An input outside the frozen normal-execution/simple-reply contract."""


def validate(name, value):
    if name not in _VALIDATORS:
        schema = json.loads((CONFIG_DIR / 'schemas' / (name + '.schema.json')).read_text())
        Draft202012Validator.check_schema(schema)
        _VALIDATORS[name] = Draft202012Validator(schema)
    errors = list(_VALIDATORS[name].iter_errors(value))
    if errors:
        raise PlanError(f'SCHEMA_{name}: ' + errors[0].message[:400])


def resolve(value, results):
    if isinstance(value, dict) and set(value) == {'ref'}:
        ref = value['ref']
        node_id = ref['node_id']
        if node_id not in results or results[node_id].get('status') != 'SUCCESS':
            raise PlanError('RESULT_NOT_AVAILABLE: ' + node_id)
        result = results[node_id]
        for key in ref['path']:
            if not isinstance(result, dict) or key not in result:
                raise PlanError('RESULT_PATH_NOT_AVAILABLE')
            result = result[key]
        if result is None:
            raise OutOfScopeInput('UNKNOWN_RESULT')
        return deepcopy(result)
    if isinstance(value, dict) and set(value) == {'concat'}:
        parts = [resolve(part, results) for part in value['concat']]
        if not all(type(part) is str for part in parts):
            raise PlanError('TEXT_PART_MUST_BE_STRING')
        return ''.join(parts)
    if isinstance(value, dict):
        return {k: resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, results) for v in value]
    return value


def evaluate(condition, results):
    op = condition['op']
    if op in {'AND', 'OR'}:
        # All referenced evidence must exist, even in a logically short-circuited term.
        values = [evaluate(c, results) for c in condition['conditions']]
        return all(values) if op == 'AND' else any(values)
    if op == 'NOT':
        return not evaluate(condition['condition'], results)
    left, right = resolve(condition['left'], results), resolve(condition['right'], results)
    if type(left) is not type(right):
        raise PlanError('CONDITION_TYPE_MISMATCH')
    if op == 'EQ':
        if type(left) not in (str, int, bool):
            raise PlanError('SCALAR_COMPARISON_REQUIRED')
        return left == right
    if type(left) is not int or op not in {'GT', 'LT'}:
        raise PlanError('INTEGER_COMPARISON_REQUIRED')
    return left > right if op == 'GT' else left < right


def normalize_reply(text, spec, shared):
    """Public lexical rules; they do not select a branch or look up an answer."""
    if not isinstance(text, str):
        raise OutOfScopeInput('TEXT_REPLY_REQUIRED')
    clean = re.sub(r'\s+', '', text).strip('。.!！?？')
    kind = spec['kind']
    if kind == 'CONTINUE_OR_STOP':
        if clean in {'需要', '继续', '需要，继续看看厨房', '继续看看厨房', '是', '是的', '要'}:
            return 'CONTINUE'
        if clean in {'不用', '不用了', '停止', '不用了，就到这里', '就到这里', '不需要', '不要'}:
            return 'STOP'
        raise OutOfScopeInput('REPLY_NEEDS_CLARIFICATION')
    if kind == 'LOCATION':
        allowed = spec['allowed_location_ids']
        found = {loc['location_id'] for loc in shared['Semantic_Map']['locations']
                 if loc['location_id'] in allowed and clean in {loc['location_id'], loc['name'], *loc['aliases']}}
        if len(found) != 1:
            raise OutOfScopeInput('REPLY_NEEDS_CLARIFICATION')
        return found.pop()
    if kind == 'TARGET_REF':
        for prefix in ('跟着', '跟随', '跟'):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
                break
        if not clean:
            raise OutOfScopeInput('EMPTY_TARGET_REFERENCE')
        return clean
    raise PlanError('UNSUPPORTED_REPLY_KIND')


def compile_decision(decision, results, shared, state):
    concrete = resolve(decision, results)
    validate('decision', concrete)
    if concrete['decision'] == 'CALL' and concrete['skill'] == 'OBSERVE':
        args = concrete['args']
        rule = shared['Observation_Queries'][args['view_id']]
        if rule['location_id'] != state['current_location_id']:
            raise PlanError('VIEW_LOCATION_MISMATCH')
    return concrete


def check_result(decision, result, previous_state, shared):
    """Validate the contract and attribution; this is not a perception truth check."""
    try:
        validate('result', result)
    except PlanError as exc:
        raise OutOfScopeInput('INVALID_ACTUAL_RESULT_CONTRACT') from exc
    data, state = result['data'], result['robot_state']
    kind = decision['decision']
    expected_location = previous_state['current_location_id']
    if kind == 'CALL':
        args, skill = decision['args'], decision['skill']
        if skill == 'MOVE_TO':
            expected_location = args['location_id']
            if data != {'arrived_location_id': expected_location}:
                raise OutOfScopeInput('MOVE_RESULT_MISMATCH')
        elif skill == 'FOLLOW':
            expected_location = args['until_location_id']
            if data != {'arrived_location_id': expected_location, 'followed_target_ref': args['target_ref'],
                         'completion_reason': 'REACHED_UNTIL_LOCATION'}:
                raise OutOfScopeInput('FOLLOW_RESULT_MISMATCH')
        elif skill == 'SAY':
            if data != {'spoken_text': args['text']}:
                raise OutOfScopeInput('SAY_RESULT_MISMATCH')
        else:
            from observation_render import observation_summary
            rule = shared['Observation_Queries'][args['view_id']]
            if (data.get('view_id'), data.get('field'), data.get('location_id'), data.get('question')) != (
                    args['view_id'], args['field'], expected_location, rule['questions'][args['field']]):
                raise OutOfScopeInput('OBSERVATION_ATTRIBUTION_MISMATCH')
            if data.get('summary') != observation_summary(data, shared):
                raise OutOfScopeInput('SUMMARY_IS_NOT_SAME_FACT')
    elif kind == 'ASK':
        if data.get('question') != decision['question'] or data.get('reply_spec') != decision['reply_spec']:
            raise OutOfScopeInput('ASK_RESULT_MISMATCH')
        if data.get('value') != normalize_reply(data.get('raw_text'), decision['reply_spec'], shared):
            raise OutOfScopeInput('REPLY_BINDING_MISMATCH')
    elif data != {'outcome': decision['outcome']}:
        raise OutOfScopeInput('FINISH_RESULT_MISMATCH')
    if state['current_location_id'] != expected_location:
        raise OutOfScopeInput('ACTUAL_LOCATION_MISMATCH')


def reference_ids(value):
    if isinstance(value, dict):
        if set(value) == {'ref'}:
            yield value['ref']['node_id']
        else:
            for child in value.values():
                yield from reference_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from reference_ids(child)


def validate_tree(plan):
    validate('bt_output', plan)
    if plan['decision'] != 'PLAN':
        return
    tree = plan['behavior_tree']
    nodes = {n['id']: n for n in tree['nodes']}
    if len(nodes) != len(tree['nodes']):
        raise PlanError('DUPLICATE_NODE_ID')
    root = tree['root_id']
    if root not in nodes or nodes[root]['type'] != 'SEQUENCE':
        raise PlanError('ROOT_SEQUENCE_REQUIRED')
    parents, visited, stack = {}, set(), set()

    def walk(node_id):
        if node_id not in nodes:
            raise PlanError('DANGLING_CHILD')
        if node_id in stack:
            raise PlanError('CYCLE')
        if node_id in visited:
            raise PlanError('MULTIPLE_PARENTS')
        stack.add(node_id)
        node = nodes[node_id]
        children = node.get('children', [])
        if node['type'] == 'SELECTOR':
            for child in children:
                branch = nodes.get(child, {})
                if branch.get('type') != 'SEQUENCE' or not branch.get('children'):
                    raise PlanError('GUARDED_SEQUENCE_REQUIRED')
                if nodes.get(branch['children'][0], {}).get('type') != 'CONDITION':
                    raise PlanError('BRANCH_FIRST_CONDITION_REQUIRED')
        for index, child in enumerate(children):
            if child in parents:
                raise PlanError('MULTIPLE_PARENTS')
            parents[child] = (node_id, index)
            walk(child)
        stack.remove(node_id)
        visited.add(node_id)

    walk(root)
    if visited != set(nodes):
        raise PlanError('ISOLATED_NODE')
    for node_id, node in nodes.items():
        if node['type'] == 'CONDITION':
            parent, index = parents.get(node_id, (None, None))
            grandparent = parents.get(parent, (None, None))[0]
            if index != 0 or grandparent is None or nodes[grandparent]['type'] != 'SELECTOR':
                raise PlanError('CONDITION_ONLY_AS_BRANCH_GUARD')
        if node['type'] == 'END':
            cursor = node_id
            while cursor != root:
                parent, index = parents[cursor]
                if nodes[parent]['type'] == 'SEQUENCE' and index != len(nodes[parent]['children']) - 1:
                    raise PlanError('END_BEFORE_REMAINING_SEQUENCE_WORK')
                cursor = parent

    def dependencies(node_id, available):
        node = nodes[node_id]
        if not set(reference_ids(node)).issubset(available):
            raise PlanError('REFERENCE_NOT_GUARANTEED_ON_PATH: ' + node_id)
        kind = node['type']
        if kind in {'ACTION', 'ASK'}:
            return available | {node_id}, False
        if kind == 'END':
            return available, True
        if kind == 'SEQUENCE':
            ended = False
            for child in node['children']:
                if ended:
                    raise PlanError('WORK_AFTER_END')
                available, ended = dependencies(child, available)
            return available, ended
        if kind == 'SELECTOR':
            branches = [dependencies(child, set(available)) for child in node['children']]
            end_flags = {ended for _, ended in branches}
            if len(end_flags) != 1:
                raise PlanError('MIXED_TERMINAL_AND_CONTINUING_BRANCHES')
            return set.intersection(*(done for done, _ in branches)), end_flags.pop()
        return available, False

    _, ended = dependencies(root, set())
    if not ended:
        raise PlanError('TERMINAL_END_REQUIRED_ON_ALL_STRUCTURAL_PATHS')


def execute_tree(plan, initial_state, shared):
    """Yield CALL/ASK/FINISH; .send(actual_result) resumes after exactly that event.

    SELECTOR is a deliberately restricted exclusive branch selector. Exactly one
    guard must be true. Legal skills never fail, so there is no fallback/retry tick.
    """
    validate_tree(plan)
    if plan['decision'] != 'PLAN':
        raise PlanError('PLAN_REQUIRED_FOR_EXECUTION')
    nodes = {n['id']: n for n in plan['behavior_tree']['nodes']}
    results, state = {}, deepcopy(initial_state)
    count = 0

    def run(node_id):
        nonlocal state, count
        node, kind = nodes[node_id], nodes[node_id]['type']
        if kind == 'SEQUENCE':
            for child in node['children']:
                yield from run(child)
            return
        if kind == 'SELECTOR':
            matches = [child for child in node['children']
                       if evaluate(nodes[nodes[child]['children'][0]]['condition'], results)]
            if len(matches) != 1:
                raise PlanError('BRANCH_COVERAGE_OR_OVERLAP')
            yield from run(matches[0])
            return
        if kind == 'CONDITION':
            if not evaluate(node['condition'], results):
                raise PlanError('SELECTED_BRANCH_GUARD_FALSE')
            return
        if kind == 'ACTION':
            decision = {'decision': 'CALL', 'skill': node['skill'], 'args': node['args']}
        elif kind == 'ASK':
            decision = {'decision': 'ASK', 'question': node['question'], 'reply_spec': node['reply_spec']}
        else:
            decision = {'decision': 'FINISH', 'outcome': 'completed' if node['outcome'] == 'completed' else 'blocked',
                        'message': node['message']}
        decision = compile_decision(decision, results, shared, state)
        count += 1
        if count > shared['Shared_Protocol']['max_decisions_per_episode']:
            raise PlanError('DECISION_LIMIT_EXCEEDED')
        actual_result = yield deepcopy(decision)
        check_result(decision, actual_result, state, shared)
        state = deepcopy(actual_result['robot_state'])
        results[node_id] = deepcopy(actual_result)

    yield from run(plan['behavior_tree']['root_id'])

