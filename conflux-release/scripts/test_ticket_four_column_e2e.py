"""Isolated integration/contract tests; never imports or starts the live hub.

Run: python -B scripts/test_ticket_four_column_e2e.py
All records live in TemporaryDirectory. Red tests are blockers, never skips.
"""
import ast
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import ticket_flow


def hub_tree():
    return ast.parse(Path(__file__).with_name('hub.py').read_text(encoding='utf-8'))


def extract_function(name, namespace):
    node = next(n for n in hub_tree().body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'hub.py', 'exec'), namespace)
    return namespace[name]


def post_route(path, body, namespace):
    """Execute the actual POST route AST with an in-memory request/response."""
    handler = next(n for n in hub_tree().body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
    method = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == 'do_POST')
    branch = next(n for n in method.body if isinstance(n, ast.If) and any(
        isinstance(value, ast.Constant) and value.value == path for value in ast.walk(n.test)))
    wrapper = ast.parse('def isolated_post(self, body):\n    pass').body[0]
    wrapper.body = [branch]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), 'hub-route', 'exec'), namespace)
    response = []
    request = SimpleNamespace(path=path, _json=lambda payload, status=200: response.append((status, payload)))
    namespace['isolated_post'](request, body)
    if len(response) != 1:
        raise AssertionError('Expected exactly one route response, got %r' % response)
    return response[0]


class FourColumnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='four-column-offline-')
        self.addCleanup(self.temp.cleanup)
        self.store = ticket_flow.TicketFlow(self.temp.name, os.path.join(self.temp.name, 'tickets'))
        self.store.set_config({'defaults': {'name': ['mimo'], 'plan': ['codex'], 'delivery': ['codex']}})
        self.ticket = self.store.create('隔离测试，不生成真实项目', '隔离工单', supervisor='codex')
        self.tid = self.ticket['id']

    def reply(self, sender, stage, text):
        return self.store.hook(sender, '[#%s:%s]\n%s' % (self.tid, stage, text))

    def namespace(self):
        return dict(_tickets=self.store, _ticket_flow=ticket_flow, json=json, os=os,
                    re=__import__('re'), time=time, threading=threading,
                    _admin_ok=lambda request: (True, ''),
                    _dispatch_stage=lambda *args, **kwargs: [],
                    _ticket_path=lambda tid: os.path.join(self.temp.name, tid + '.md'),
                    DATA_DIR=self.temp.name)

    def begin_workers(self):
        self.store.expect(self.tid, 'plan', ['codex'])
        self.reply('codex', 'plan', '方案与分工\n>>> mimo\n实现产物 A\n>>> arch\n实现产物 B')
        ticket = self.store.load(self.tid)
        self.assertEqual(set(ticket.get('pending_assignees', [])), {'mimo', 'arch'})
        return ticket

    def worker_reply(self, sender, text='已交付，临时产物验证通过'):
        return self.store.hook(sender, '>>> codex\n' + text)

    def watch_once(self, messages):
        """Run one real watcher iteration, replacing delivery with capture stubs."""
        namespace = self.namespace()
        notifications, dispatches = [], []
        namespace.update(_tickets_events=[], read_msgs=lambda *args, **kwargs: messages,
                         append_msg=lambda *args, **kwargs: notifications.append((args, kwargs)),
                         _dispatch_stage=lambda *args, **kwargs: dispatches.append((args, kwargs)))
        watch = extract_function('_ticket_watch', namespace)
        with patch('time.sleep', side_effect=[None, StopIteration('one iteration complete')]):
            try:
                watch()
            except StopIteration:
                pass
        return notifications, dispatches

    def test_workers_out_of_order_and_duplicates_cannot_bypass_barrier(self):
        self.begin_workers()
        first = self.worker_reply('arch')
        ticket = self.store.load(self.tid)
        self.assertIn('mimo', ticket.get('pending_assignees', []))
        self.assertIn('arch', ticket.get('completed_assignees', []))
        self.assertNotEqual(ticket['current'], 'delivery')
        self.worker_reply('arch', '重复回复不应凑齐人数')
        self.assertIn('mimo', self.store.load(self.tid).get('pending_assignees', []))
        self.worker_reply('mimo')
        ticket = self.store.load(self.tid)
        self.assertFalse(ticket.get('pending_assignees'))
        self.assertEqual(ticket['current'], 'delivery')
        self.assertEqual(ticket['status'], 'running')

    def test_supervisor_cannot_deliver_while_workers_pending(self):
        self.begin_workers()
        self.store.expect(self.tid, 'delivery', ['codex'])
        self.reply('codex', 'delivery', '验收通过，提前交付')
        ticket = self.store.load(self.tid)
        self.assertNotEqual(ticket['status'], 'pending_acceptance')
        self.assertTrue(ticket.get('pending_assignees'))

    def test_concurrent_worker_submissions_do_not_lose_results(self):
        self.begin_workers()
        barrier = threading.Barrier(2)
        def submit(agent):
            barrier.wait(timeout=3)
            return self.worker_reply(agent, agent + ' 的独立成果')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, agent) for agent in ('mimo', 'arch')]
            for future in futures:
                future.result(timeout=5)
        ticket = self.store.load(self.tid)
        self.assertFalse(ticket.get('pending_assignees'))
        self.assertEqual(set(ticket.get('completed_assignees', [])), {'mimo', 'arch'})
        self.assertEqual(ticket['current'], 'delivery')

    def test_watcher_only_dispatches_supervisor_after_every_worker_submits(self):
        self.begin_workers()
        def message(agent):
            return {'sender': agent, 'kind': 'text', 'ts': time.time(), 'text': '>>> codex\n产物已落盘并验证'}
        notifications, first_dispatches = self.watch_once([message('arch')])
        self.assertFalse(first_dispatches, '首人交卷不得触发总管验收')
        notifications, last_dispatches = self.watch_once([message('mimo')])
        self.assertEqual(len(last_dispatches), 1, '收齐全员后仅统一唤醒一次总管')

    def test_unmarked_reply_cannot_be_assigned_to_ambiguous_ticket(self):
        self.begin_workers()
        other = self.store.create('另一张隔离单', '第二张隔离单', supervisor='codex')
        self.store.expect(other['id'], 'plan', ['codex'])
        self.store.hook('codex', '[#%s:plan]\n>>> mimo\n实现另一份产物' % other['id'])
        before = copy.deepcopy(self.store.list())
        self.worker_reply('mimo', '未带工单标记，存在两份待办，不能猜测归属')
        self.assertEqual(self.store.list(), before, '多单同人时必须有明确工单关联，不能按首个匹配收卷')

    def test_partial_rejection_only_reopens_named_worker(self):
        self.begin_workers()
        self.worker_reply('arch', 'B 的结果已完成')
        self.worker_reply('mimo', 'A 的结果已完成')
        self.store.expect(self.tid, 'delivery', ['codex'])
        self.reply('codex', 'delivery', '【验收不通过】A 有问题\n>>> mimo\n仅修复 A')
        ticket = self.store.load(self.tid)
        self.assertEqual(set(ticket.get('pending_assignees', [])), {'mimo'})
        self.assertIn('arch', ticket.get('completed_assignees', []))
        self.assertNotEqual(ticket['status'], 'pending_acceptance')
        self.worker_reply('mimo', 'A 已修复并复测通过')
        self.assertFalse(self.store.load(self.tid).get('pending_assignees'))
        self.assertEqual(self.store.load(self.tid)['current'], 'delivery')

    def test_self_managed_task_goes_to_delivery_without_worker_barrier(self):
        self.store.expect(self.tid, 'plan', ['codex'])
        self.reply('codex', 'plan', '由本人直接实现与验证，不另派人')
        ticket = self.store.load(self.tid)
        self.assertFalse(ticket.get('pending_assignees'))
        self.assertEqual(ticket['current'], 'delivery')
        self.assertNotEqual(ticket['status'], 'done')

    def test_delivery_notifies_user_once_and_waits_for_final_acceptance(self):
        self.store.expect(self.tid, 'plan', ['codex'])
        self.reply('codex', 'plan', '本人执行，产物已验证')
        self.store.expect(self.tid, 'delivery', ['codex'])
        message = {'sender': 'codex', 'kind': 'text', 'ts': time.time(),
                   'text': '[#%s:delivery]\n验收通过；交付清单：临时产物 A；用户可自行确认' % self.tid}
        notifications, dispatches = self.watch_once([message])
        self.assertEqual(self.store.load(self.tid)['status'], 'pending_acceptance')
        user_notifications = [entry for entry in notifications if '验收' in str(entry)]
        self.assertEqual(len(user_notifications), 1, '第4栏写入必须恰好通知一次用户')
        self.assertFalse(self.store.load(self.tid).get('user_accepted'))
        again, _ = self.watch_once([message])
        self.assertFalse([entry for entry in again if '验收' in str(entry)], '重放交付不能重复通知')
        _, payload = post_route('/api/ticketflow/accept', {'id': self.tid, 'passed': True}, self.namespace())
        self.assertTrue(payload.get('ok'), payload)
        self.assertEqual(self.store.load(self.tid)['status'], 'done')

    def test_exactly_four_columns(self):
        self.assertEqual(ticket_flow.STAGE_KEYS, ['name', 'goal', 'plan', 'delivery'])
        self.assertEqual(set(self.ticket['stages']), {'name', 'goal', 'plan', 'delivery'})

    def test_wrong_sender_wrong_stage_and_wrong_ticket_do_not_change_state(self):
        self.store.expect(self.tid, 'plan', ['codex'])
        before = self.store.load(self.tid)
        self.assertIsNone(self.reply('mimo', 'plan', '冒名交卷'))
        self.assertIsNone(self.reply('codex', 'delivery', '越级通过'))
        self.assertIsNone(self.store.hook('codex', '[#ffffffff:plan]\n串单交卷'))
        self.assertEqual(before, self.store.load(self.tid))

    def test_deleted_ticket_late_reply_does_not_recreate_record(self):
        self.store.expect(self.tid, 'plan', ['codex'])
        Path(self.store._path(self.tid)).unlink()
        self.assertIsNone(self.reply('codex', 'plan', '延迟交卷'))
        self.assertFalse(Path(self.store._path(self.tid)).exists())

    def test_dispatch_registers_all_workers_before_any_launch(self):
        workers = ['mimo', 'arch']
        registered = []
        launched = []
        def expect(tid, stage, agents):
            registered.append(list(agents))
        def launch(agent, fn, prompt, **kwargs):
            self.assertEqual(registered, [workers], '必须先一次性登记全员，不能边发边重置')
            launched.append(agent)
        namespace = self.namespace()
        namespace.update(_tickets=SimpleNamespace(
            agents_for=lambda *args: workers, expect=expect, build_prompt=lambda *args, **kwargs: '原始派单'),
            _routing=SimpleNamespace(load_providers=lambda: [], sender_ids=lambda: set(workers)),
            agent_is_enabled=lambda agent: True, agent_enabled_map=lambda: {},
            _agent_asker=lambda agent: lambda text: None, launch_agent=launch)
        dispatch = extract_function('_dispatch_stage', namespace)
        dispatch({'id': self.tid}, 'delivery', workers)
        self.assertEqual(launched, workers)
        self.assertEqual(registered, [workers])

    def test_cannot_accept_before_delivery_completed(self):
        namespace = self.namespace()
        status, payload = post_route('/api/ticketflow/accept', {'id': self.tid, 'passed': True}, namespace)
        self.assertFalse(payload.get('ok'), '未交付不得用户验收通过')
        self.assertNotEqual(self.store.load(self.tid)['status'], 'done')

    def test_legacy_accept_entry_also_blocks_early_accept(self):
        namespace = self.namespace()
        namespace['_ticket_status_of'] = extract_function('_ticket_status_of', namespace)
        namespace['ticket_accept'] = extract_function('ticket_accept', namespace)
        status, payload = post_route('/api/ticket', {'action': 'accept', 'id': self.tid, 'passed': True}, namespace)
        self.assertFalse(payload.get('ok'), '旧入口不能绕过四栏验收闸')
        self.assertNotEqual(self.store.load(self.tid)['status'], 'done')

    def test_user_rejection_retains_id_goal_and_name(self):
        ticket = self.store.load(self.tid)
        ticket['status'] = 'pending_acceptance'
        ticket['current'] = ''
        self.store.save(ticket)
        namespace = self.namespace()
        _, payload = post_route('/api/ticketflow/accept', {
            'id': self.tid, 'passed': False, 'reason': '按钮无响应，请局部修复'}, namespace)
        self.assertTrue(payload.get('ok'), payload)
        after = self.store.load(self.tid)
        self.assertEqual(after['id'], self.tid)
        self.assertEqual(after['title'], ticket['title'])
        self.assertIn(ticket['stages']['goal']['output'], after['stages']['goal']['output'])
        self.assertEqual(after['current'], 'plan', '四栏重做不得回退到已删除的 assign')
        self.assertEqual(after['status'], 'running')
        self.assertIn('按钮无响应', after['acceptance_note'])
        self.assertIn('按钮无响应', after['stages']['goal']['output'], '用户驳回要求必须追加到第2栏')
        self.assertEqual(len(self.store.list()), 1)

    def test_final_acceptance_requires_actual_boolean(self):
        ticket = self.store.load(self.tid)
        ticket['status'] = 'pending_acceptance'
        self.store.save(ticket)
        before = self.store.load(self.tid)
        _, payload = post_route('/api/ticketflow/accept', {'id': self.tid, 'passed': 'false'}, self.namespace())
        self.assertFalse(payload.get('ok'), '字符串 false 不能被 bool() 当作通过')
        self.assertEqual(self.store.load(self.tid), before)



    def test_one_two_one_full_loop_success(self):
        """1-2-1 完整四环节闭环：Argon出方案分工 -> DS/GPT交卷 -> Argon初审通过 -> Gemini终审通过 -> 炫炫最终验收。"""
        tk = self.store.create('1-2-1 闭环任务目标', title='1-2-1 自动化测试项目',
                               roles={'supervisor': 'arch', 'workers': ['dsh', 'codex'], 'auditor': 'ag'})
        tid = tk['id']
        
        # 环节 1：Argon 出方案并指派 dsh 和 codex
        self.store.expect(tid, 'plan', ['arch'])
        res_plan = self.store.hook('arch', f"[#{tid}:plan]\n方案制定完毕，任务分工如下：\n>>> dsh\n>>> codex")
        self.assertTrue(res_plan.get('waiting_workers'), res_plan)
        tk_cur = self.store.load(tid)
        self.assertEqual(set(tk_cur.get('pending_assignees', [])), {'dsh', 'codex'})
        self.assertEqual(tk_cur['status'], 'running')
        
        # 环节 2：dsh 和 codex 协同干活交卷
        self.store.hook('dsh', f"[#{tid}]\nDeepSeek 后端开发交卷")
        tk_cur = self.store.load(tid)
        self.assertEqual(tk_cur['pending_assignees'], ['codex'])
        
        res_codex = self.store.hook('codex', f"[#{tid}]\nGPT 前端界面交卷")
        self.assertTrue(res_codex.get('all_workers_submitted'), res_codex)
        tk_cur = self.store.load(tid)
        self.assertEqual(tk_cur['pending_assignees'], [])
        self.assertEqual(tk_cur['current'], 'delivery')
        self.assertEqual(tk_cur['status'], 'pending_initial_review')
        
        # 环节 3：Argon 初审验收通过
        self.store.expect(tid, 'delivery', ['arch'])
        res_arch_review = self.store.hook('arch', f"[#{tid}:review]\n【初审通过】两名成员交卷均合格，同意交付二审")
        self.assertTrue(res_arch_review.get('to_audit'), res_arch_review)
        self.assertEqual(res_arch_review.get('next'), 'delivery')
        self.assertEqual(res_arch_review.get('agents'), ['ag'])
        
        tk_after_arch = self.store.load(tid)
        self.assertEqual(tk_after_arch['status'], 'pending_final_review')
        self.assertTrue((tk_after_arch.get('reviews', {}).get('initial') or {}).get('passed'))
        self.assertFalse(tk_after_arch.get('user_accepted'))
        self.assertFalse(tk_after_arch.get('user_notified'))
        
        # 环节 4：Gemini 终审质检通过
        self.store.expect(tid, 'delivery', ['ag'])
        msg_ag = {'sender': 'ag', 'kind': 'text', 'ts': time.time(),
                  'text': f"[#{tid}:audit]\n【终审通过】二审质检全部达标，交付用户最终验收"}
        notifications, dispatches = self.watch_once([msg_ag])
        
        tk_final = self.store.load(tid)
        self.assertEqual(tk_final['status'], 'pending_acceptance')
        self.assertTrue((tk_final.get('reviews', {}).get('final') or {}).get('passed'))
        self.assertTrue(tk_final.get('user_notified'))
        user_notifications = [entry for entry in notifications if '验收' in str(entry)]
        self.assertEqual(len(user_notifications), 1, '终审通过后必须恰好通知一次用户')
        
        # 最终决定权：用户点击通过 -> 结单
        _, payload = post_route('/api/ticketflow/accept', {'id': tid, 'passed': True}, self.namespace())
        self.assertTrue(payload.get('ok'), payload)
        self.assertEqual(self.store.load(tid)['status'], 'done')

    def test_one_two_one_supervisor_rejection(self):
        """1-2-1 环节 3：Argon 初审驳回打回 dsh 返工，不影响 codex。"""
        tk = self.store.create('初审驳回测试', title='初审驳回项目', roles={'supervisor': 'arch', 'workers': ['dsh', 'codex'], 'auditor': 'ag'})
        tid = tk['id']
        self.store.expect(tid, 'plan', ['arch'])
        self.store.hook('arch', f"[#{tid}:plan]\n分工\n>>> dsh\n>>> codex")
        self.store.hook('dsh', f"[#{tid}]\n初次后端交卷")
        self.store.hook('codex', f"[#{tid}]\n初次前端交卷")
        
        self.store.expect(tid, 'delivery', ['arch'])
        self.store.hook('arch', f"[#{tid}:delivery]\n【初审驳回】后端接口参数有误，要求返工\n>>> dsh")
        
        tk_cur = self.store.load(tid)
        self.assertEqual(set(tk_cur.get('pending_assignees', [])), {'dsh'})
        self.assertEqual(tk_cur.get('completed_assignees', []), ['codex'])
        self.assertFalse((tk_cur.get('reviews', {}).get('initial') or {}).get('passed'))
        self.assertEqual(tk_cur['current'], 'plan')

    def test_one_two_one_auditor_rejection(self):
        """1-2-1 环节 4：Gemini 终审驳回，初审通过标记强制失效并打回返工。"""
        tk = self.store.create('终审驳回测试', title='终审驳回项目', roles={'supervisor': 'arch', 'workers': ['dsh', 'codex'], 'auditor': 'ag'})
        tid = tk['id']
        self.store.expect(tid, 'plan', ['arch'])
        self.store.hook('arch', f"[#{tid}:plan]\n分工\n>>> dsh\n>>> codex")
        self.store.hook('dsh', f"[#{tid}]\ndsh 完成")
        self.store.hook('codex', f"[#{tid}]\ncodex 完成")
        
        self.store.expect(tid, 'delivery', ['arch'])
        self.store.hook('arch', f"[#{tid}:review]\n【初审通过】初审合格")
        self.assertTrue((self.store.load(tid).get('reviews', {}).get('initial') or {}).get('passed'))
        
        self.store.expect(tid, 'delivery', ['ag'])
        self.store.hook('ag', f"[#{tid}:audit]\n【终审驳回】前端安全校验未通过，打回返工\n>>> codex")
        
        tk_cur = self.store.load(tid)
        self.assertFalse((tk_cur.get('reviews', {}).get('initial') or {}).get('passed'), '终审驳回后旧初审通过标记必须失效')
        self.assertEqual(set(tk_cur.get('pending_assignees', [])), {'codex'})
        self.assertEqual(tk_cur['current'], 'plan')

    def test_one_two_one_approval_permission_isolation(self):
        """1-2-1 审批权限硬隔离：执行人不能审批自己，总管不能终审。"""
        tk = self.store.create('权限隔离测试', title='权限隔离项目', roles={'supervisor': 'arch', 'workers': ['dsh', 'codex'], 'auditor': 'ag'})
        tid = tk['id']
        self.store.expect(tid, 'plan', ['arch'])
        self.store.hook('arch', f"[#{tid}:plan]\n分工\n>>> dsh\n>>> codex")
        self.store.hook('dsh', f"[#{tid}]\ndsh 完成")
        self.store.hook('codex', f"[#{tid}]\ncodex 完成")
        
        # 初审阶段：dsh 试图抢跑初审自己
        self.store.expect(tid, 'delivery', ['arch'])
        res_illegal = self.store.hook('dsh', f"[#{tid}:delivery]\n【验收通过】我自己审过了")
        self.assertIsNone(res_illegal, '执行人不能越权初审自己')
        self.assertFalse((self.store.load(tid).get('reviews', {}).get('initial') or {}).get('passed'))
        
        # 正确初审
        self.store.hook('arch', f"[#{tid}:review]\n【初审通过】通过")
        
        # 终审阶段：arch 试图终审
        self.store.expect(tid, 'delivery', ['ag'])
        res_arch_final = self.store.hook('arch', f"[#{tid}:audit]\n【终审通过】我总管直接终审了")
        self.assertIsNone(res_arch_final, '总管不能代替审计员终审')
        self.assertFalse((self.store.load(tid).get('reviews', {}).get('final') or {}).get('passed'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
