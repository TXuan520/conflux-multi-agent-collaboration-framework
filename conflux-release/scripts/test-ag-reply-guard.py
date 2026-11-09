"""Regression for one manual API reply plus one automatic final in a single turn."""
import ast
import json
import re
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
from ag_reply_guard import manual_reply_conflicts


def resolve(text):
    return {'炫炫': ['human'], 'GPT': ['codex'], '炫炫 GPT': ['human', 'codex']}.get(text, [])


class ReplyGuardTests(unittest.TestCase):
    def test_original_incident(self):
        self.assertTrue(manual_reply_conflicts('ag', ['human'], '>>> 炫炫\nGemini 到！随时待命，请指示！', {'sender': 'human'}))

    def test_default_public_reply(self):
        self.assertTrue(manual_reply_conflicts('ag', [], '回答', True))

    def test_arrow_public_reply(self):
        self.assertTrue(manual_reply_conflicts('ag', [], '>>> 炫炫\n回答', True, resolve_names=resolve))

    def test_peer_call_allowed(self):
        for targets, text in [(['codex'], '讨论'), ([], '>>> GPT\n讨论')]:
            self.assertFalse(manual_reply_conflicts('ag', targets, text, True, resolve_names=resolve))

    def test_mixed_public_recipients_blocked(self):
        self.assertTrue(manual_reply_conflicts('ag', ['human', 'codex'], '回答', True))

    def test_file_and_image_allowed(self):
        for kind in ('file', 'image', 'conflux'):
            self.assertFalse(manual_reply_conflicts('ag', ['human'], '文件', True, kind=kind))

    def test_idle_manual_message_allowed(self):
        self.assertFalse(manual_reply_conflicts('ag', ['human'], '回答', None))

    def test_member_probe_original_recipient_blocked(self):
        self.assertTrue(manual_reply_conflicts('ag', ['dsh'], '收到探针，编号是 PROBE-120641。', {'sender': 'dsh'}))
        self.assertFalse(manual_reply_conflicts('ag', ['codex'], '独立协作请求', {'sender': 'dsh'}))

    def test_multiple_original_recipients_blocked(self):
        self.assertTrue(manual_reply_conflicts('ag', ['arch'], '回答', {'sender': 'dsh'}, original_recipients=['dsh', 'arch']))

    def test_repeat_original_recipient_with_new_peer_blocked(self):
        self.assertTrue(manual_reply_conflicts('ag', ['dsh', 'codex'], '回答', {'sender': 'dsh'}))

    def test_other_channels_unchanged(self):
        for sender in ('codex', 'dsh', 'arch', 'human'):
            self.assertFalse(manual_reply_conflicts(sender, ['human'], '回答', True))

    def test_real_api_branch_rejects_before_append(self):
        source = Path(__file__).with_name('hub.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        route = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "self.path == '/api/send'")
        env = dict(re=re, normalize_agent_id=lambda s: s,
                   normalize_recipients=lambda targets: targets or [],
                   _routing=types.SimpleNamespace(sender_ids=lambda: {'ag'}, resolve_names=resolve),
                   _agent_current_tasks={'ag': {'sender': 'human'}},
                   _active_reply_target={},
                   append_msg=Mock(side_effect=AssertionError('must not publish')))
        exec('def send(self, body):\n' + '\n'.join('    ' + line for line in ast.unparse(route).splitlines()), env)
        for extra in ({}, {'client_mid': 'turn:some-id'}, {'kind': 'unknown'}):
            handler = types.SimpleNamespace(path='/api/send', headers={}, _json=Mock())
            if extra.get('client_mid'):
                env['read_msgs'] = lambda **kw: []
            env['send'](handler, {'from': 'ag', 'text': 'Gemini 到！', 'to': ['human'], **extra})
            self.assertEqual(handler._json.call_args.args[1], 409)
            self.assertEqual(handler._json.call_args.args[0]['code'], 'automatic_reply_owned')
        env['append_msg'].assert_not_called()

        env['_agent_current_tasks']['ag'] = {'sender': 'dsh'}
        env['_active_reply_target']['ag'] = ['dsh']
        handler = types.SimpleNamespace(path='/api/send', headers={}, _json=Mock())
        env['send'](handler, {'from': 'ag', 'text': '收到探针，编号是 PROBE-120641。', 'to': ['dsh']})
        self.assertEqual(handler._json.call_args.args[1], 409)
        env['append_msg'].assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
