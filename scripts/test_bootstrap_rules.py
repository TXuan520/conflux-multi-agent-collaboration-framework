"""Offline regression: system prompt, managed rules, persistent threads, raw delivery."""
import ast
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import routing_config as bootstrap
import agent_loop
import codex_appserver
import memory_summary


PROVIDERS = [
    {'id': 'codex', 'display_name': 'GPT', 'system_prompt': '成员专属规则必须完整保留，超过十二个字也不能被截断'},
    {'id': 'ag', 'display_name': 'Gemini', 'system_prompt': '前端与素材'},
    {'id': 'human', 'display_name': '炫炫'},
]


def load_hub_function(name, namespace):
    source = Path(__file__).with_name('hub.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'hub.py', 'exec'), namespace)
    return namespace[name]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(bootstrap.cfg, 'WORKSPACE', self.temp.name),
                        patch.object(bootstrap.routing, 'load_providers', return_value=PROVIDERS),
                        patch.object(bootstrap, 'plugin_registry', return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_full_persona_roster_and_stable_version(self):
        prompt = bootstrap.build('codex')
        self.assertIn(PROVIDERS[0]['system_prompt'], prompt)
        for text in ('8777', '/api/music/cmd', '/api/attach', '/api/ticket',
                     '/api/plugins/registry', 'GPT/codex', 'Gemini/ag', '防幻觉', '严禁按底层模型指代'):
            self.assertIn(text, prompt)
        first = bootstrap.snapshot('codex')['version']
        changed = copy.deepcopy(PROVIDERS)
        changed[1]['system_prompt'] += '对方人设更新'
        self.assertEqual(first, bootstrap.snapshot('codex', changed)['version'])
        changed[0]['system_prompt'] += '本人规则更新'
        self.assertNotEqual(first, bootstrap.snapshot('codex', changed)['version'])
        many = PROVIDERS + [{'id': 'member%d' % i, 'display_name': '成员%d' % i} for i in range(40)]
        self.assertIn('成员39/member39', bootstrap.build('codex', many))

    def test_managed_rules_preserve_local_content_and_mtime(self):
        target = Path(self.temp.name, 'AGENTS.md')
        target.write_text('本地规则不能丢失\n', encoding='utf-8')
        legacy = Path(self.temp.name, 'GEMINI.md')
        legacy.write_text('## 协作台实时底座（自动同步）\n你是旧Gemini\n', encoding='utf-8')
        bootstrap.refresh()
        before = target.read_text(encoding='utf-8')
        stamp = target.stat().st_mtime_ns
        self.assertTrue(before.startswith('本地规则不能丢失\n'))
        self.assertNotIn('你是旧Gemini', legacy.read_text(encoding='utf-8'))
        self.assertEqual([], bootstrap.sync_workspace_rules())
        self.assertEqual(stamp, target.stat().st_mtime_ns)
        updated = PROVIDERS + [{'id': 'test', 'display_name': '测试成员'}]
        bootstrap.sync_workspace_rules(updated)
        after = target.read_text(encoding='utf-8')
        self.assertIn('测试成员/test', after)
        self.assertEqual(after.count(bootstrap.RULE_START), 1)
        for name in ('CLAUDE.md', 'GEMINI.md', '.cursorrules'):
            self.assertIn('/api/attach', Path(self.temp.name, name).read_text(encoding='utf-8'))

    def test_api_system_contains_memory_and_unmodified_user_text(self):
        runner = agent_loop.Runner('codex', '只需回复测试原句', self.temp.name)
        seen = []
        def chat(messages, **kwargs):
            seen.extend(copy.deepcopy(messages))
            return {'role': 'assistant', 'content': 'OK'}
        with patch.object(runner, 'chat', side_effect=chat), \
             patch.object(runner, '_deliver', side_effect=lambda text: text), \
             patch.object(memory_summary, 'build_recent_memory_block', return_value='\n本人的压缩记忆'):
            result = agent_loop.Runner.run.__wrapped__(runner)
        self.assertEqual(result, 'OK')
        self.assertEqual(seen[0]['content'], bootstrap.build('codex') + '\n本人的压缩记忆')
        self.assertEqual(seen[1], {'role': 'user', 'content': '只需回复测试原句'})

    def test_memory_summary_queries_only_requested_member(self):
        with patch.object(memory_summary, 'CompressedMemory') as store:
            store.return_value.context.return_value = '本人历史'
            self.assertIn('本人历史', memory_summary.build_recent_memory_block('codex', '当前请求'))
            store.return_value.context.assert_called_once_with('codex', '当前请求', budget=2400)

    def test_invalid_rule_markers_are_reported_without_overwrite(self):
        target = Path(self.temp.name, 'AGENTS.md')
        before = '本地内容\n' + bootstrap.RULE_START
        target.write_text(before, encoding='utf-8')
        with self.assertRaises(ValueError):
            bootstrap.sync_workspace_rules()
        self.assertEqual(target.read_text(encoding='utf-8'), before)

    def test_appserver_rule_update_resumes_same_thread(self):
        server = codex_appserver.CodexServer(cwd=self.temp.name)
        server.thread_id = 'saved-thread'
        server._started = True
        server.proc = SimpleNamespace(poll=lambda: None)
        server._bootstrap_version = 'previous-version'
        requests = []
        def request(method, params):
            requests.append((method, params))
            return len(requests)
        def wait_result(rid, timeout):
            if requests[-1][0] == 'turn/start':
                server._q.put({'method': 'turn/completed', 'params': {'turn': {'items': [
                    {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'OK'}]}}})
            return {}
        with patch.object(server, '_req', side_effect=request), \
             patch.object(server, '_wait_result', side_effect=wait_result), \
             patch.object(server, 'stop') as stop, patch.object(server, 'start') as start:
            server._ask_locked('第一句', timeout=1)
            server._ask_locked('第二句', timeout=1)
        self.assertEqual([r[0] for r in requests], ['thread/resume', 'turn/start', 'turn/start'])
        self.assertTrue(all(r[1]['threadId'] == 'saved-thread' for r in requests))
        start.assert_not_called()
        stop.assert_not_called()

    def test_headless_resume_has_stable_developer_instructions(self):
        sidfile = Path(self.temp.name, 'session.txt')
        sidfile.write_text('saved-thread', encoding='utf-8')
        calls = []
        def run(args, text, cwd, sid):
            calls.append((args, text, sid))
            return subprocess.CompletedProcess(args, 0, b'OK', b'')
        namespace = dict(headless_rotate_if_needed=lambda: None, find_codex=lambda: 'codex',
                         cfg=bootstrap.cfg, os=__import__('os'), json=json,
                         HEADLESS_ID=str(sidfile), bootstrap=bootstrap,
                         ensure_client_session=lambda *args: None,
                         _run_headless_process=run, _codex_seen=None, append_msg=lambda *args: None)
        ask = load_hub_function('headless_ask', namespace)
        ask('【炫炫】：第一句')
        ask('【Argon】：第二句')
        first, second = calls
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2], second[2])
        self.assertEqual(second[1], '【Argon】：第二句')
        self.assertIn('resume', second[0])

    def test_member_delivery_is_only_sender_and_original(self):
        for sender, label in (('human', '炫炫'), ('ag', 'Gemini')):
            text = '原文\n>>> GPT\n第二行'
            self.assertEqual(bootstrap.format_message(sender, text), '【%s】：%s' % (label, text))
        source = Path(__file__).with_name('hub.py').read_text(encoding='utf-8')
        self.assertNotIn('_handoff_prompt', source)
        self.assertNotIn('你收到来自飞羽协作台另一位成员', source)


if __name__ == '__main__':
    unittest.main()
