"""Generate self-contained JSON Schema 2020-12 documents from the fixed map."""
from pathlib import Path
from copy import deepcopy
import json

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / '提示词与接口配置'
VERSION = 'kaxinguo_feasibility_v1'


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def arr(items, minimum=0, **kwargs):
    return {'type': 'array', 'items': items, 'minItems': minimum, **kwargs}


def const(value):
    return {'const': value}


def union(*schemas):
    return {'anyOf': list(schemas)}


def ref(name):
    return {'$ref': '#/$defs/' + name}


def definitions(shared):
    locations = [p['location_id'] for p in shared['Semantic_Map']['locations']]
    loc = {'type': 'string', 'enum': locations}
    text = {'type': 'string', 'minLength': 1}
    node_id = {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_]*$', 'maxLength': 64}
    result_ref = obj({'ref': obj({'node_id': node_id, 'path': {'enum': [
        ['data', 'value'], ['data', 'summary'], ['data', 'arrived_location_id'],
        ['data', 'spoken_text'], ['data', 'raw_text']]}})})
    text_expr = union(text, ref('result_ref'), obj({'concat': arr(union(text, ref('result_ref')), 1)}))
    reply_spec = union(obj({'kind': const('LOCATION'), 'allowed_location_ids': arr(loc, 2, uniqueItems=True)}),
                       obj({'kind': const('CONTINUE_OR_STOP')}), obj({'kind': const('TARGET_REF')}))
    observe_args = union(*(obj({'view_id': const(view), 'field': {'enum': list(rule['questions'])}})
                           for view, rule in shared['Observation_Queries'].items()))
    scalar = union({'type': 'integer'}, {'type': 'boolean'}, {'type': 'string'}, ref('result_ref'))
    predicate = union(obj({'op': {'enum': ['EQ', 'GT', 'LT']}, 'left': scalar, 'right': scalar}),
                      obj({'op': {'enum': ['AND', 'OR']}, 'conditions': arr(ref('predicate'), 2)}),
                      obj({'op': const('NOT'), 'condition': ref('predicate')}))
    state = obj({'current_location_id': loc, 'motion_status': const('stopped')})
    entities = obj({'locations': arr(obj({'mention': text, 'location_id': loc})),
                    'return_location_id': union(loc, {'type': 'null'}),
                    'follow_target_ref': union(text, {'type': 'null'})})
    defs = {'location_id': loc, 'text': text, 'node_id': node_id, 'result_ref': result_ref,
            'text_expr': text_expr, 'reply_spec': reply_spec, 'observe_args': observe_args,
            'predicate': predicate, 'robot_state': state, 'resolved_entities': entities}

    def call_variants(planned=False, base=None):
        base = base or {}
        target = union(loc, ref('result_ref')) if planned else loc
        spoken = ref('text_expr') if planned else text
        target_ref = union(text, ref('result_ref')) if planned else text
        args_by_skill = {'MOVE_TO': obj({'location_id': target}),
                         'FOLLOW': obj({'target_ref': target_ref, 'until_location_id': target}),
                         'OBSERVE': ref('observe_args'), 'SAY': obj({'text': spoken})}
        return [obj({**base, 'skill': const(skill), 'args': args}) for skill, args in args_by_skill.items()]

    def decisions(meta=None):
        meta = meta or {}
        return union(*call_variants(base={**meta, 'decision': const('CALL')}),
                     obj({**meta, 'decision': const('ASK'), 'question': text, 'reply_spec': ref('reply_spec')}),
                     obj({**meta, 'decision': const('FINISH'), 'outcome': {'enum': ['completed', 'blocked']}, 'message': text}))

    defs['decision'] = decisions()
    task_id = {'type': 'string', 'pattern': '^task_[A-Za-z0-9_]+$'}
    meta = {'protocol_version': const(VERSION), 'task_id': task_id,
            'context_revision': {'type': 'integer', 'minimum': 1}}
    defs['step_output'] = decisions(meta)
    defs['node'] = union(
        obj({'id': node_id, 'type': {'enum': ['SEQUENCE', 'SELECTOR']}, 'children': arr(node_id, 1, uniqueItems=True)}),
        obj({'id': node_id, 'type': const('CONDITION'), 'condition': ref('predicate')}),
        *call_variants(True, {'id': node_id, 'type': const('ACTION')}),
        obj({'id': node_id, 'type': const('ASK'), 'question': text, 'reply_spec': ref('reply_spec')}),
        obj({'id': node_id, 'type': const('END'), 'outcome': {'enum': ['completed', 'unresolved']}, 'message': text}))
    defs['tree'] = obj({'task_name': text, 'root_id': node_id, 'nodes': arr(ref('node'), 2, maxItems=256)})
    defs['bt_output'] = union(
        obj({'protocol_version': const(VERSION), 'decision': const('PLAN'), 'task_id': task_id, 'behavior_tree': ref('tree')}),
        obj({'protocol_version': const(VERSION), 'decision': const('NEEDS_RESOLUTION'), 'task_id': task_id,
             'message': text, 'missing_information': arr(text, 1)}),
        obj({'protocol_version': const(VERSION), 'decision': const('UNSUPPORTED'), 'task_id': task_id, 'message': text}))
    defs['bt_input'] = obj({'protocol_version': const(VERSION),
        'Task': obj({'task_id': task_id, 'mode': const('NEW'), 'instruction': text,
                     'resolved_entities': ref('resolved_entities'), 'constraints': arr(text),
                     'completion_description': union(text, {'type': 'null'})}),
        'Robot_State': ref('robot_state'), 'Execution_Feedback': {'type': 'null'}})
    value_schemas = {'people_count': {'type': 'integer', 'minimum': 0},
                     'people_descriptions': arr(text), 'cup_visible': {'type': 'boolean'},
                     'tableware_visible': {'type': 'boolean'}, 'notice_text': {'type': 'string'},
                     'quiet_sign_visible': {'type': 'boolean'}, 'visible_items': arr(text)}
    data_variants = []
    for view, rule in shared['Observation_Queries'].items():
        for field, question in rule['questions'].items():
            data_variants.append(obj({'location_id': const(rule['location_id']), 'view_id': const(view),
                'field': const(field), 'question': const(question), 'value': value_schemas[field], 'summary': text}))
    data_variants += [obj({'arrived_location_id': loc}),
                     obj({'arrived_location_id': loc, 'followed_target_ref': text, 'completion_reason': const('REACHED_UNTIL_LOCATION')}),
                     obj({'spoken_text': text}), obj({'outcome': {'enum': ['completed', 'blocked']}})]
    for spec, value in [
        (obj({'kind': const('LOCATION'), 'allowed_location_ids': arr(loc, 2, uniqueItems=True)}), loc),
        (obj({'kind': const('CONTINUE_OR_STOP')}), {'enum': ['CONTINUE', 'STOP']}),
        (obj({'kind': const('TARGET_REF')}), text)]:
        data_variants.append(obj({'question': text, 'reply_spec': spec, 'raw_text': text, 'value': value}))
    defs['result'] = obj({'status': const('SUCCESS'), 'data': union(*data_variants), 'robot_state': ref('robot_state')})
    defs['event'] = obj({'event_id': {'type': 'string', 'pattern': '^event_[0-9]+$'},
                        'context_revision': {'type': 'integer', 'minimum': 1},
                        'decision': ref('decision'), 'result': ref('result')})
    defs['step_input'] = obj({'protocol_version': const(VERSION), 'task_id': task_id,
        'context_revision': {'type': 'integer', 'minimum': 1},
        'current_goal': obj({'initial_request': text, 'constraints': arr(text), 'completion_description': union(text, {'type': 'null'})}),
        'task_context': obj({'start_location_id': loc, 'resolved_entities': ref('resolved_entities')}),
        'robot_state': ref('robot_state'), 'events': arr(ref('event'), maxItems=40),
        'runtime': obj({'state': const('DECIDING'), 'active_call_id': {'type': 'null'}})})
    return defs


def document(defs, name):
    # Keep only reachable definitions, each schema can be sent without external URLs.
    wanted, pending = set(), [name]
    def refs(x):
        if isinstance(x, dict):
            if '$ref' in x:
                yield x['$ref'].split('/')[-1]
            for v in x.values():
                yield from refs(v)
        elif isinstance(x, list):
            for v in x:
                yield from refs(v)
    while pending:
        key = pending.pop()
        if key in wanted:
            continue
        wanted.add(key)
        pending.extend(refs(defs[key]))
    result = {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': VERSION + ':' + name,
              **deepcopy(defs[name]), '$defs': {key: deepcopy(defs[key]) for key in sorted(wanted) if key != name}}
    return result


def main():
    shared = json.loads((CONFIG_DIR / 'shared_context.json').read_text())
    defs = definitions(shared)
    folder = CONFIG_DIR / 'schemas'
    folder.mkdir(exist_ok=True)
    for name in ['bt_input', 'step_input', 'bt_output', 'step_output', 'decision', 'result']:
        (folder / (name + '.schema.json')).write_text(json.dumps(document(defs, name), ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
