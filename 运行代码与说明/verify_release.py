"""Validate release data, adapters and hand-authored BT fixtures.

This is a protocol regression check, NOT a general model semantic scorer.
It compares fixtures with existing references only to detect release mistakes.
"""
from pathlib import Path
from copy import deepcopy
from collections import Counter
from importlib.metadata import version
import hashlib
import json
from protocol import (VERSION, PlanError, OutOfScopeInput, validate, validate_tree, execute_tree,
                      compile_decision, check_result, normalize_reply, evaluate, resolve)
from adapters import (make_bt_input, make_step_input, make_request, accept_step_output, append_completed_event)
from observation_render import observation_summary

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / '提示词与接口配置'
DATA_DIR = ROOT / '数据集及参考计划'


class SemanticOracle:
    """Evaluator-only environment: reveals one requested fact AFTER a legal call."""
    def __init__(self, case, payload, shared):
        self.case, self.shared = case, shared
        self.state = deepcopy(payload['Robot_State'])
        self.observed, self.spoken, self.replies, self.trace = {}, [], 0, []

    def step(self, decision):
        compile_decision(decision, {}, self.shared, self.state)
        before = deepcopy(self.state)
        kind = decision['decision']
        if kind == 'CALL':
            args, skill = decision['args'], decision['skill']
            if skill == 'MOVE_TO':
                self.state['current_location_id'] = args['location_id']
                data = {'arrived_location_id': args['location_id']}
            elif skill == 'FOLLOW':
                self.state['current_location_id'] = args['until_location_id']
                data = {'arrived_location_id': args['until_location_id'], 'followed_target_ref': args['target_ref'],
                        'completion_reason': 'REACHED_UNTIL_LOCATION'}
            elif skill == 'OBSERVE':
                view, field = args['view_id'], args['field']
                scene = self.case['hidden_environment']['scenes_by_view'][view]
                data = {'location_id': self.state['current_location_id'], 'view_id': view, 'field': field,
                        'question': self.shared['Observation_Queries'][view]['questions'][field],
                        'value': deepcopy(scene['facts'][field])}
                data['summary'] = observation_summary(data, self.shared)
                self.observed[(data['location_id'], field)] = deepcopy(data)
            else:
                data = {'spoken_text': args['text']}
                self.spoken.append((self.state['current_location_id'], args['text']))
        elif kind == 'ASK':
            scripts = self.case['hidden_environment']['user_reply_script']
            if self.replies >= len(scripts):
                raise PlanError('UNEXPECTED_QUESTION')
            script = scripts[self.replies]
            spec, expected_spec = decision['reply_spec'], script['reply_spec']
            if spec['kind'] != expected_spec['kind'] or set(spec.get('allowed_location_ids', [])) != set(expected_spec.get('allowed_location_ids', [])):
                raise PlanError('QUESTION_INTENT_OR_OPTIONS_MISMATCH')
            release = script['release_after']
            if self.state['current_location_id'] != release['robot_location_id']:
                raise PlanError('QUESTION_BEFORE_REQUIRED_LOCATION')
            for req in release['required_observations']:
                if (req['location_id'], req['field']) not in self.observed:
                    raise PlanError('QUESTION_BEFORE_REQUIRED_OBSERVATION')
            for req in release.get('required_reports', []):
                summary = self.observed[(req['location_id'], req['field'])]['summary']
                if not any(summary in text for _, text in self.spoken):
                    raise PlanError('QUESTION_BEFORE_REQUIRED_REPORT')
            # No normalized answer or future answer is available until this point.
            raw = script['text']
            data = {'question': decision['question'], 'reply_spec': deepcopy(spec), 'raw_text': raw,
                    'value': normalize_reply(raw, spec, self.shared)}
            self.replies += 1
        else:
            data = {'outcome': decision['outcome']}
        result = {'status': 'SUCCESS', 'data': data, 'robot_state': deepcopy(self.state)}
        check_result(decision, result, before, self.shared)
        self.trace.append({'decision': deepcopy(decision), 'feedback': result})
        return deepcopy(result)


def check_fixture_trajectory(actual, case):
    """Reference equivalence for THESE fixtures only; model scoring needs the rubric."""
    reference = case['reference_trace']
    assert len(actual) == len(reference), (case['test_id'], 'fixture_length', len(actual), len(reference))
    summaries = []
    for observed, original in zip(actual, reference):
        d, expected = observed['decision'], original['decision']
        assert d['decision'] == expected['decision'], (case['test_id'], 'decision_kind')
        if d.get('skill') == 'OBSERVE':
            summaries.append(observed['feedback']['data']['summary'])
        if d.get('skill') == 'SAY':
            # Public fixed renderings and original faithful reference wording differ.
            assert d['args']['text'] in {expected['args']['text'], ''.join(summaries)}, (case['test_id'], 'fixture_speech', d)
            assert observed['feedback']['robot_state'] == original['feedback']['robot_state']
        elif d['decision'] == 'FINISH':
            assert d['outcome'] == expected['outcome']
        else:
            assert d == expected, (case['test_id'], 'fixture_decision', d, expected)
        loc = observed['feedback']['robot_state']['current_location_id']
        if d.get('skill') == 'MOVE_TO':
            assert loc not in case['expected']['forbidden_move_destinations']
        if d.get('skill') == 'SAY':
            assert loc not in case['expected']['forbidden_speech_locations']
    assert actual[-1]['decision']['decision'] == 'FINISH'
    assert actual[-1]['decision']['outcome'] == case['expected']['terminal_outcome']
    assert actual[-1]['feedback']['robot_state']['current_location_id'] == case['expected']['terminal_location_id']


def negative_checks(shared, first_case, plans):
    checks = []
    def reject(name, fn):
        try:
            fn()
        except (PlanError, OutOfScopeInput):
            checks.append(name)
        else:
            raise AssertionError('Missing rejection: ' + name)
    location = {'current_location_id': 'study_top_right', 'motion_status': 'stopped'}
    obs = {'decision': 'CALL', 'skill': 'OBSERVE', 'args': {'view_id': 'view_study_top_right_overview', 'field': 'people_count'}}
    bad = deepcopy(obs); bad['args']['field'] = ['people_count', 'cup_visible']
    reject('one_information_item', lambda: compile_decision(bad, {}, shared, location))
    bad = deepcopy(obs); bad['args']['question'] = '有没有杯子？'
    reject('model_question_cannot_override_fixed_query', lambda: compile_decision(bad, {}, shared, location))
    bad = deepcopy(obs); bad['args']['field'] = 'notice_text'
    reject('view_field_pair', lambda: compile_decision(bad, {}, shared, location))
    reject('observation_requires_arrival', lambda: compile_decision(obs, {}, shared, first_case['payload']['Robot_State']))
    ref = {'ref': {'node_id': 'later', 'path': ['data', 'value']}}
    reject('future_reference', lambda: resolve(ref, {}))
    reject('boolean_not_integer', lambda: evaluate({'op': 'EQ', 'left': True, 'right': 1}, {}))
    reject('count_cannot_be_speech', lambda: resolve({'concat': [ref]}, {'later': {'status': 'SUCCESS', 'data': {'value': 2}}}))
    plan = deepcopy(plans['task_001'])
    action = next(n for n in plan['behavior_tree']['nodes'] if n.get('skill') == 'SAY')
    action['args']['text'] = {'ref': {'node_id': 'not_executed', 'path': ['data', 'summary']}}
    reject('tree_dependency_analysis', lambda: validate_tree(plan))
    plan = deepcopy(plans['task_001'])
    plan['behavior_tree']['nodes'].append({'id': 'isolated', 'type': 'END', 'outcome': 'completed', 'message': '完成'})
    reject('isolated_node', lambda: validate_tree(plan))
    plan = deepcopy(plans['task_001'])
    root = next(n for n in plan['behavior_tree']['nodes'] if n['id'] == plan['behavior_tree']['root_id'])
    root['children'].append('late_speech')
    plan['behavior_tree']['nodes'].append({'id': 'late_speech', 'type': 'ACTION', 'skill': 'SAY', 'args': {'text': '不应被跳过'}})
    reject('end_cannot_skip_ancestor_work', lambda: validate_tree(plan))
    plan = deepcopy(plans['task_001'])
    root = next(n for n in plan['behavior_tree']['nodes'] if n['id'] == plan['behavior_tree']['root_id'])
    root['children'].append(root['id'])
    reject('cyclic_tree', lambda: validate_tree(plan))
    plan = deepcopy(plans['task_001'])
    conditions = [n for n in plan['behavior_tree']['nodes'] if n['type'] == 'CONDITION']
    conditions[1]['condition'] = deepcopy(conditions[0]['condition'])
    def overlap():
        runner = execute_tree(plan, first_case['payload']['Robot_State'], shared)
        decision = next(runner)
        result = {'status': 'SUCCESS', 'data': {'arrived_location_id': 'bedroom_top_left'},
                  'robot_state': {'current_location_id': 'bedroom_top_left', 'motion_status': 'stopped'}}
        decision = runner.send(result)
        data = {'location_id': 'bedroom_top_left', 'view_id': decision['args']['view_id'], 'field': 'people_count',
                'value': 1, 'question': shared['Observation_Queries'][decision['args']['view_id']]['questions']['people_count']}
        data['summary'] = observation_summary(data, shared)
        runner.send({'status': 'SUCCESS', 'data': data, 'robot_state': result['robot_state']})
    reject('overlapping_true_branches', overlap)
    context = make_step_input(first_case)
    output = {'protocol_version': VERSION, 'task_id': context['task_id'], 'context_revision': 2,
              'decision': 'CALL', 'skill': 'MOVE_TO', 'args': {'location_id': 'bedroom_top_left'}}
    reject('stale_context_revision', lambda: accept_step_output(context, output, shared))
    output['context_revision'] = 1
    pending, _ = accept_step_output(context, output, shared)
    reject('no_decision_during_pending_action', lambda: accept_step_output(pending, output, shared))
    reject('ambiguous_location_reply', lambda: normalize_reply('那里', {'kind': 'LOCATION', 'allowed_location_ids': ['kitchen_left_upper', 'study_top_right']}, shared))
    reject('unknown_does_not_become_zero', lambda: resolve(ref, {'later': {'status': 'SUCCESS', 'data': {'value': None}}}))
    return checks


def verify():
    test = json.loads((DATA_DIR / 'test_inputs.json').read_text())
    val = json.loads((DATA_DIR / 'validation_data.json').read_text())
    shared = json.loads((CONFIG_DIR / 'shared_context.json').read_text())
    plans = {p['task_id']: p for p in json.loads((DATA_DIR / 'reference_plans.json').read_text())['plans']}
    assert test['shared_context'] == shared
    assert test['protocol_version'] == val['protocol_version'] == VERSION
    inputs = {c['test_id']: c for c in test['cases']}
    assert len(inputs) == len(test['cases']) == len(val['cases']) == 48
    assert set(inputs) == {c['test_id'] for c in val['cases']}
    grouped = {}
    for case in test['cases']:
        grouped.setdefault(case['task_id'], set()).add(json.dumps(case['payload'], sort_keys=True, ensure_ascii=False))
    assert len(grouped) == 10 and all(len(v) == 1 for v in grouped.values())
    private = {'expected', 'reference_trace', 'reference_plans', 'hidden_environment', 'scenario_label', 'branch_specification', 'user_reply_script', 'test_id'}
    def public_only(value):
        if isinstance(value, dict):
            assert not set(value) & private
            for v in value.values(): public_only(v)
        elif isinstance(value, list):
            for v in value: public_only(v)
    public_only(shared)
    for plan in plans.values(): validate_tree(plan)
    counters, case_reports = Counter(), []
    requests_checked = 0
    for case in val['cases']:
        public = inputs[case['test_id']]
        assert public['task_id'] == case['task_id']
        bt_input, step_input = make_bt_input(public), make_step_input(public)
        public_only(bt_input); public_only(step_input)
        # Verify each reference result against a fresh environment and both schemas.
        oracle = SemanticOracle(case, public['payload'], shared)
        for index, item in enumerate(case['reference_trace'], 1):
            assert item['step'] == index and item['event_id'] == f'event_{index:03d}'
            validate('decision', item['decision']); validate('result', item['feedback'])
            produced = oracle.step(item['decision'])
            assert produced == item['feedback'], (case['test_id'], index, 'reference_feedback')
            counters[item['decision'].get('skill', item['decision']['decision'])] += 1
            out = {'protocol_version': VERSION, 'task_id': step_input['task_id'],
                   'context_revision': step_input['context_revision'], **item['decision']}
            validate('step_input', step_input)
            pending, accepted = accept_step_output(step_input, out, shared)
            step_input = append_completed_event(pending, accepted, produced, shared)
        assert step_input['runtime']['state'] == 'FINISHED'
        assert oracle.replies == case['expected']['expected_user_question_count']
        assert len(oracle.trace) == case['expected']['reference_total_decisions']
        assert sum(t['decision']['decision'] == 'CALL' for t in oracle.trace) == case['expected']['reference_skill_calls']
        check_fixture_trajectory(oracle.trace, case)
        # Same one public-goal plan is replayed in every hidden scenario of that goal.
        bt_oracle = SemanticOracle(case, public['payload'], shared)
        runner = execute_tree(plans[case['task_id']], bt_input['Robot_State'], shared)
        decision = next(runner)
        while True:
            result = bt_oracle.step(decision)
            try:
                decision = runner.send(result)
            except StopIteration:
                break
        check_fixture_trajectory(bt_oracle.trace, case)
        for architecture, dynamic in [('bt', bt_input), ('step', make_step_input(public))]:
            sdk = make_request('MODEL_NAME_TO_SET', architecture, dynamic, transport='sdk')
            http = make_request('MODEL_NAME_TO_SET', architecture, dynamic, transport='http')
            assert sdk['extra_body']['structured_outputs'] == http['structured_outputs']
            assert 'extra_body' not in http
            assert json.loads(sdk['messages'][1]['content']) == dynamic
            requests_checked += 1
        case_reports.append({'test_id': case['test_id'], 'task_id': case['task_id'],
                             'reference_data': 'PASS', 'step_adapter_replay': 'PASS', 'hand_authored_bt_replay': 'PASS'})
    for expectation in val['task_expectations']:
        assert set(expectation['test_ids']) == {c['test_id'] for c in val['cases'] if c['task_id'] == expectation['task_id']}
    negatives = negative_checks(shared, test['cases'][0], plans)
    report = {'protocol_version': VERSION, 'scope': 'FROZEN_PROTOCOL_AND_HAND_AUTHORED_FIXTURE_REGRESSION',
              'unique_goals': len(grouped), 'scenarios': len(case_reports), 'json_schema_validator': 'jsonschema ' + version('jsonschema'),
              'reference_data_cases_passed': len(case_reports), 'step_adapter_replay_cases_passed': len(case_reports),
              'hand_authored_bt_count': len(plans), 'hand_authored_bt_replay_cases_passed': len(case_reports),
              'single_information_observations': counters['OBSERVE'], 'simple_replies': counters['ASK'],
              'reference_calls_by_type': dict(counters), 'request_pairs_checked': requests_checked,
              'negative_protocol_checks_passed': len(negatives), 'negative_protocol_checks': negatives,
              'model_called': False, 'model_planning_accuracy': None,
              'vllm_backend_schema_acceptance_tested': False, 'real_robot_or_vision_tested': False,
              'general_free_text_semantic_scorer_implemented': False,
              'case_results': case_reports}
    (ROOT / '运行代码与说明' / 'verification_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    report = verify()
    print(json.dumps({k: v for k, v in report.items() if k not in {'case_results', 'negative_protocol_checks'}}, ensure_ascii=False, indent=2))
