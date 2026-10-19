"""Hand-authored protocol fixtures from the ten PUBLIC goals, not model answers.

No hidden data is read here. One plan per goal serves every associated scenario.
These fixtures must never be included in the evaluated model's first input.
"""
from pathlib import Path
import json
from protocol import VERSION, validate_tree

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / '数据集及参考计划'
LIVING = 'living_room_bottom_center'
STUDY = 'study_top_right'
KITCHEN = 'kitchen_left_upper'
DINING = 'dining_area_top_center'
BOTTOM = 'balcony_bottom'
LEFT = 'balcony_left'
BEDROOM = 'bedroom_top_left'
VIEWS = {STUDY: 'view_study_top_right_overview', KITCHEN: 'view_kitchen_overview',
         DINING: 'view_dining_area_overview', BOTTOM: 'view_balcony_bottom_overview',
         LEFT: 'view_balcony_left_overview', BEDROOM: 'view_bedroom_top_left_overview'}


def r(node, field='value'):
    return {'ref': {'node_id': node, 'path': ['data', field]}}


def cmp(op, left, right):
    return {'op': op, 'left': left, 'right': right}


def both(*conditions):
    return {'op': 'AND', 'conditions': list(conditions)}


def neg(condition):
    return {'op': 'NOT', 'condition': condition}


class Builder:
    def __init__(self, task):
        self.task, self.nodes = task, []

    def add(self, kind, **kwargs):
        node_id = f'n{len(self.nodes) + 1}'
        self.nodes.append({'id': node_id, 'type': kind, **kwargs})
        return node_id

    def seq(self, *children):
        return self.add('SEQUENCE', children=list(children))

    def choose(self, *branches):
        return self.add('SELECTOR', children=list(branches))

    def branch(self, condition, *children):
        return self.seq(self.add('CONDITION', condition=condition), *children)

    def action(self, skill, **args):
        return self.add('ACTION', skill=skill, args=args)

    def move(self, location):
        return self.action('MOVE_TO', location_id=location)

    def observe(self, location, field, work=False):
        return self.action('OBSERVE', view_id='view_kitchen_work_area' if work else VIEWS[location], field=field)

    def say(self, text):
        return self.action('SAY', text=text)

    def report(self, *nodes):
        values = [r(n, 'summary') for n in nodes]
        return self.say(values[0] if len(values) == 1 else {'concat': values})

    def ask(self, kind, allowed=None):
        spec = {'kind': kind}
        if allowed:
            spec['allowed_location_ids'] = allowed
        question = {'LOCATION': '你希望我在哪一处播报？', 'TARGET_REF': '你希望我跟随谁？',
                    'CONTINUE_OR_STOP': '还需要我查看厨房操作台吗？'}[kind]
        return self.add('ASK', question=question, reply_spec=spec)

    def end(self, outcome='completed'):
        return self.add('END', outcome=outcome, message='已完成当前任务分支。' if outcome == 'completed' else '当前结果没有对应的用户处理要求。')

    def finish(self, root):
        plan = {'protocol_version': VERSION, 'decision': 'PLAN', 'task_id': self.task,
                'behavior_tree': {'task_name': self.task + ' 人工协议校验计划', 'root_id': root, 'nodes': self.nodes}}
        validate_tree(plan)
        return plan


def make_plan(task):
    b = Builder(task)
    if task == 'task_001':
        move, obs = b.move(BEDROOM), b.observe(BEDROOM, 'people_count')
        root = b.seq(move, obs, b.choose(
            b.branch(cmp('GT', r(obs), 0), b.say('请到餐区吃饭'), b.end()),
            b.branch(cmp('EQ', r(obs), 0), b.move(LIVING), b.report(obs), b.end())))
    elif task == 'task_002':
        def search(locations, earlier):
            location = locations[0]
            move, obs = b.move(location), b.observe(location, 'people_count')
            none = search(locations[1:], earlier + [obs]) if len(locations) > 1 else b.seq(b.move(LIVING), b.report(*(earlier + [obs])), b.end())
            return b.seq(move, obs, b.choose(
                b.branch(cmp('GT', r(obs), 0), b.say('会议开始了'), b.end()),
                b.branch(cmp('EQ', r(obs), 0), none)))
        root = search([KITCHEN, STUDY, LEFT], [])
    elif task == 'task_003':
        move, people = b.move(STUDY), b.observe(STUDY, 'people_count')
        cup = b.observe(STUDY, 'cup_visible')
        kmove, kcup = b.move(KITCHEN), b.observe(KITCHEN, 'cup_visible', work=True)
        has_people = b.seq(cup, b.choose(
            b.branch(cmp('EQ', r(cup), True), b.say('记得喝水'), b.end()),
            b.branch(cmp('EQ', r(cup), False), kmove, kcup, b.report(kcup), b.end())))
        root = b.seq(move, people, b.choose(
            b.branch(cmp('EQ', r(people), 0), b.move(LIVING), b.report(people), b.end()),
            b.branch(cmp('GT', r(people), 0), has_people)))
    elif task == 'task_004':
        move, people, tableware = b.move(DINING), b.observe(DINING, 'people_count'), b.observe(DINING, 'tableware_visible')
        guard = both(cmp('GT', r(people), 0), cmp('EQ', r(tableware), True))
        root = b.seq(move, people, tableware, b.choose(
            b.branch(guard, b.move(BEDROOM), b.say('可以来餐区吃饭了'), b.end()),
            b.branch(neg(guard), b.move(LIVING), b.report(people, tableware), b.end())))
    elif task == 'task_005':
        move, obs = b.move(STUDY), b.observe(STUDY, 'people_count')
        ask = b.ask('TARGET_REF')
        root = b.seq(move, obs, b.choose(
            b.branch(cmp('EQ', r(obs), 0), b.move(LIVING), b.report(obs), b.end()),
            b.branch(cmp('EQ', r(obs), 1), b.action('FOLLOW', target_ref='书房里唯一的人', until_location_id=LIVING), b.say('已经跟随到客厅了'), b.end()),
            b.branch(cmp('GT', r(obs), 1), ask, b.action('FOLLOW', target_ref=r(ask), until_location_id=LIVING), b.say('已经跟随到客厅了'), b.end())))
    elif task == 'task_006':
        kmove, ko, smove, so = b.move(KITCHEN), b.observe(KITCHEN, 'people_count'), b.move(STUDY), b.observe(STUDY, 'people_count')
        ask = b.ask('LOCATION', [KITCHEN, STUDY])
        selected = b.seq(ask, b.choose(
            b.branch(cmp('EQ', r(ask), KITCHEN), b.move(r(ask)), b.say('会议开始了'), b.end()),
            b.branch(cmp('EQ', r(ask), STUDY), b.say('会议开始了'), b.end())))
        root = b.seq(kmove, ko, smove, so, b.choose(
            b.branch(both(cmp('GT', r(ko), 0), cmp('EQ', r(so), 0)), b.move(KITCHEN), b.say('会议开始了'), b.end()),
            b.branch(both(cmp('EQ', r(ko), 0), cmp('GT', r(so), 0)), b.say('会议开始了'), b.end()),
            b.branch(both(cmp('GT', r(ko), 0), cmp('GT', r(so), 0)), selected),
            b.branch(both(cmp('EQ', r(ko), 0), cmp('EQ', r(so), 0)), b.move(LIVING), b.report(ko, so), b.end())))
    elif task == 'task_007':
        move, obs = b.move(KITCHEN), b.observe(KITCHEN, 'notice_text', work=True)
        meanings = [('会议开始', STUDY, '会议开始了'), ('会议取消', LIVING, '会议取消了'), ('改在餐区', DINING, '请到餐区开会')]
        branches = [b.branch(cmp('EQ', r(obs), content), b.move(loc), b.say(text), b.end()) for content, loc, text in meanings]
        unknown = neg({'op': 'OR', 'conditions': [cmp('EQ', r(obs), content) for content, _, _ in meanings]})
        branches.append(b.branch(unknown, b.end('unresolved')))
        root = b.seq(move, obs, b.choose(*branches))
    elif task == 'task_008':
        dm, d, bm, k = b.move(DINING), b.observe(DINING, 'people_count'), b.move(BOTTOM), b.observe(BOTTOM, 'people_count')
        ask = b.ask('LOCATION', [DINING, BOTTOM])
        selected = b.seq(ask, b.choose(
            b.branch(cmp('EQ', r(ask), DINING), b.move(r(ask)), b.say('请到客厅集合'), b.end()),
            b.branch(cmp('EQ', r(ask), BOTTOM), b.say('请到客厅集合'), b.end())))
        root = b.seq(dm, d, bm, k, b.choose(
            b.branch(cmp('GT', r(d), r(k)), b.move(DINING), b.say('请到客厅集合'), b.end()),
            b.branch(cmp('LT', r(d), r(k)), b.say('请到客厅集合'), b.end()),
            b.branch(both(cmp('EQ', r(d), r(k)), cmp('GT', r(d), 0)), selected),
            b.branch(both(cmp('EQ', r(d), 0), cmp('EQ', r(k), 0)), b.move(LIVING), b.report(d, k), b.end())))
    elif task == 'task_009':
        move, people, quiet = b.move(STUDY), b.observe(STUDY, 'people_count'), b.observe(STUDY, 'quiet_sign_visible')
        root = b.seq(move, people, quiet, b.choose(
            b.branch(cmp('EQ', r(quiet), True), b.move(LIVING), b.end()),
            b.branch(both(cmp('EQ', r(quiet), False), cmp('GT', r(people), 0)), b.say('会议开始了'), b.end()),
            b.branch(both(cmp('EQ', r(quiet), False), cmp('EQ', r(people), 0)), b.move(KITCHEN), b.say('会议开始了'), b.end())))
    elif task == 'task_010':
        move, obs = b.move(STUDY), b.observe(STUDY, 'visible_items')
        report, ask = b.report(obs), b.ask('CONTINUE_OR_STOP')
        kmove, ko = b.move(KITCHEN), b.observe(KITCHEN, 'visible_items', work=True)
        root = b.seq(move, obs, report, ask, b.choose(
            b.branch(cmp('EQ', r(ask), 'CONTINUE'), kmove, ko, b.report(ko), b.end()),
            b.branch(cmp('EQ', r(ask), 'STOP'), b.end())))
    else:
        raise ValueError(task)
    return b.finish(root)


def main():
    data = json.loads((DATA_DIR / 'test_inputs.json').read_text())
    plans = [make_plan(task) for task in sorted({case['task_id'] for case in data['cases']})]
    (DATA_DIR / 'reference_plans.json').write_text(json.dumps({'data_visibility': 'EVALUATOR_ONLY',
        'source': 'HAND_AUTHORED_PUBLIC_GOAL_PROTOCOL_FIXTURES_NOT_MODEL_OUTPUT', 'plans': plans}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
