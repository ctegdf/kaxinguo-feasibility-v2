"""Input, accepted-event and request adapters. No access to private evaluation data."""
from copy import deepcopy
from pathlib import Path
import json
from protocol import VERSION, PlanError, validate, compile_decision, check_result

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / '提示词与接口配置'


def make_bt_input(case):
    payload = deepcopy(case['payload'])
    validate('bt_input', payload)
    return payload


def make_step_input(case):
    source = make_bt_input(case)
    task = source['Task']
    result = {'protocol_version': VERSION, 'task_id': task['task_id'], 'context_revision': 1,
              'current_goal': {'initial_request': task['instruction'], 'constraints': task['constraints'],
                               'completion_description': task['completion_description']},
              'task_context': {'start_location_id': source['Robot_State']['current_location_id'],
                               'resolved_entities': task['resolved_entities']},
              'robot_state': source['Robot_State'], 'events': [],
              'runtime': {'state': 'DECIDING', 'active_call_id': None}}
    validate('step_input', result)
    return result


def accept_step_output(context, output, shared):
    validate('step_input', context)
    validate('step_output', output)
    if len(context['events']) >= shared['Shared_Protocol']['max_decisions_per_episode']:
        raise PlanError('DECISION_LIMIT_EXCEEDED')
    for key in ('protocol_version', 'task_id', 'context_revision'):
        if output[key] != context[key]:
            raise PlanError('STALE_OR_WRONG_TASK_OUTPUT')
    decision = {k: deepcopy(v) for k, v in output.items()
                if k not in {'protocol_version', 'task_id', 'context_revision'}}
    compile_decision(decision, {}, shared, context['robot_state'])
    pending = deepcopy(context)
    pending['runtime'] = {'state': 'WAITING_USER' if decision['decision'] == 'ASK' else 'EXECUTING',
                          'active_call_id': f"event_{len(context['events']) + 1:03d}"}
    pending['_pending_decision'] = deepcopy(decision)
    # Pending contexts are program-only; they must not be sent for another decision.
    return pending, decision


def append_completed_event(pending, decision, actual_result, shared):
    if pending['runtime']['state'] not in {'EXECUTING', 'WAITING_USER'}:
        raise PlanError('NO_PENDING_CALL')
    if decision != pending.get('_pending_decision'):
        raise PlanError('PENDING_DECISION_MISMATCH')
    check_result(decision, actual_result, pending['robot_state'], shared)
    result = deepcopy(pending)
    result.pop('_pending_decision')
    result['events'].append({'event_id': pending['runtime']['active_call_id'],
                             'context_revision': pending['context_revision'],
                             'decision': deepcopy(decision), 'result': deepcopy(actual_result)})
    result['robot_state'] = deepcopy(actual_result['robot_state'])
    result['context_revision'] += 1
    result['runtime'] = {'state': 'FINISHED' if decision['decision'] == 'FINISH' else 'DECIDING', 'active_call_id': None}
    if result['runtime']['state'] == 'DECIDING':
        validate('step_input', result)
    return result


def make_request(model_name, architecture, dynamic_input, max_tokens=None, transport='sdk'):
    """Return kwargs for an OpenAI-compatible SDK, or a raw vLLM HTTP JSON body.

    This function constructs a request. It performs no network/model call.
    SDK: client.chat.completions.create(**make_request(..., transport='sdk'))
    HTTP: POST returned body to the configured /v1/chat/completions endpoint.
    """
    if architecture not in {'bt', 'step'}:
        raise ValueError('architecture must be bt or step')
    validate(architecture + '_input', dynamic_input)
    prompt = (CONFIG_DIR / 'prompts' / (architecture + '_system.txt')).read_text(encoding='utf-8')
    shared = json.loads((CONFIG_DIR / 'shared_context.json').read_text())
    schema = json.loads((CONFIG_DIR / 'schemas' / (architecture + '_output.schema.json')).read_text())
    # The model also sees field definitions; the backend schema only constrains decoding.
    request = {'model': model_name, 'messages': [
        {'role': 'system', 'content': prompt + '\n\nShared_Context:\n' + json.dumps(shared, ensure_ascii=False)},
        {'role': 'user', 'content': json.dumps(dynamic_input, ensure_ascii=False)}],
        'temperature': 0, 'max_tokens': max_tokens or (8192 if architecture == 'bt' else 512), 'stream': False}
    structured = {'structured_outputs': {'json': schema}}
    if transport == 'sdk':
        request['extra_body'] = structured
    elif transport == 'http':
        request.update(structured)
    else:
        raise ValueError('transport must be sdk or http')
    return request
