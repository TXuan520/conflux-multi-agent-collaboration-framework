"""Broadcast regression: AST-load real hub functions; never import/start hub or send messages."""
import ast, copy, json, re, tempfile, threading, time, types, unittest, uuid
from pathlib import Path
from unittest.mock import Mock
from broadcast_policy import BroadcastPolicy

SOURCE = Path(__file__).with_name('hub.py')
NODES = {n.name:n for n in ast.parse(SOURCE.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}
AGENTS = {'codex','ag','dsh','arch','mimo','doubao','workbuddy'}

class BroadcastTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'messages.jsonl'
        self.route=types.SimpleNamespace(sender_ids=lambda:AGENTS,target_names=lambda:sorted(AGENTS),resolve_names=lambda s:[s] if s in AGENTS else [])
        self.env=dict(re=re,json=json,time=time,threading=threading,uuid=uuid,
          _lock=threading.RLock(),_agent_queue_lock=threading.RLock(),_agent_queues={},
          _broadcast_enabled=True,_broadcast_generation=1,_broadcast_message_generations={},
          _broadcast_policy=BroadcastPolicy(), _broadcast_record_response=Mock(), launch_agent=Mock(),
          _brainstorm_local=threading.local(),_routing=self.route,_active_reply_target={},
          _codex_seen=None,_to_map={},MSG_FILE=str(self.path),
          normalize_agent_id=lambda s:s,normalize_recipients=lambda t:list(t or []),
          _is_duplicate_reply=lambda *a:False,is_garbled_reply=lambda t:False,clean_agent_text=lambda s,t:t,
          split_arrow_blocks=lambda t:[],read_msgs=lambda *a:[],pop_expecting_target=Mock(return_value=[]),
          dispatch_targets=lambda:{a:Mock() for a in AGENTS},
          _file_store=types.SimpleNamespace(markers=lambda t:[]),
          _remember_routed_message=Mock(),_brainstorms=Mock(),inbox_push=Mock(),
          post_append_msg=Mock(),_dsh_dup_recent=lambda *a,**k:False)
        names=['broadcast_state','set_broadcast_mode','broadcast_active','clean_broadcast_routes','is_silent','is_silent_text','is_pure_ack','append_msg']
        # Also load new broadcast helpers when a patch introduces them.
        names += [n for n in NODES if ('broadcast' in n and n not in names and n!='flush_batch')]
        exec(compile(ast.Module(body=[copy.deepcopy(NODES[n]) for n in names if n in NODES],type_ignores=[]),str(SOURCE),'exec'),self.env)
    def append(self,text,sender='mimo',**kw):
        return self.env['append_msg'](sender,text,**kw)
    def messages(self):
        return [json.loads(s) for s in self.path.read_text(encoding='utf-8').splitlines()] if self.path.exists() else []
    def test_confirmations_suppressed_before_persistence_and_dispatch(self):
        for text in ['收到','收到！',' OK。 ','好的','明白了','了解','>>> codex\n收到！']:
            with self.subTest(text=text):
                self.append(text)
                self.assertEqual(self.messages(),[])
                self.env['post_append_msg'].assert_not_called()
    def test_silent_marker_not_persisted(self):
        for s in ['[[silent]]', '[[slient]]', 'silent', 'slient']:
            with self.subTest(s=s):
                self.append(s)
                self.assertEqual(self.messages(),[])
    def test_substantive_ack_prefix_preserved(self):
        cases = [
            ('收到，但接口仍返回500', '但接口仍返回500'),
            ('OK，根因是线程锁未释放', '根因是线程锁未释放'),
            ('明白，方案二成本更低', '方案二成本更低'),
            ('了解，这里有竞态条件', '这里有竞态条件'),
            ('收到！OK，我来补充一个创意视角', '我来补充一个创意视角'),
        ]
        for raw, expected in cases:
            with self.subTest(text=raw):
                self.assertEqual(self.append(raw)['text'], expected)
    def test_normal_work_ack_preserved(self):
        self.env['_broadcast_enabled']=False
        self.assertEqual(self.append('收到')['text'],'收到')
    def test_human_ack_not_suppressed(self):
        self.assertEqual(self.append('收到',sender='human')['text'],'收到')
    def test_all_agent_messages_fanout_except_sender(self):
        for sender in sorted(AGENTS):
            with self.subTest(sender=sender):
                msg=self.append('我认为应该先处理竞态条件',sender=sender)
                self.assertTrue(msg.get('broadcast'))
                self.assertEqual(set(self.env['_to_map'][msg['id']]),AGENTS-{sender})
    def test_human_messages_fanout_to_everyone(self):
        msg=self.append('来聊聊这个方案',sender='human')
        self.assertEqual(set(self.env['_to_map'][msg['id']]),AGENTS)
    def test_explicit_recipient_does_not_restrict_broadcast(self):
        msg=self.append('这个方案有个新思路',to=['codex'])
        self.assertEqual(set(self.env['_to_map'][msg['id']]),AGENTS-{'mimo'})
    def test_turn_off_invalidates_generation(self):
        self.env['set_broadcast_mode'](False)
        self.assertFalse(self.env['broadcast_active'](1))
    def test_switch_requires_real_boolean(self):
        for value in ['false','true',0,1,None]:
            with self.subTest(value=value),self.assertRaises(ValueError): self.env['set_broadcast_mode'](value)
    def test_enable_notification_is_delivered_and_explains_silence(self):
        self.env['_broadcast_enabled']=False
        self.env['set_broadcast_mode'](True)
        notices=[m for m in self.messages() if m['sender']=='system']
        self.assertTrue(notices,'activation notice must survive system-message filters')
        notice=notices[-1]
        self.assertIn('[[silent]]',notice['text'])
        self.assertTrue(any(word in notice['text'] for word in ['性格','吐槽','交锋']))
        self.assertEqual(set(self.env['_to_map'].get(notice['id'],[])),AGENTS)
        self.env['post_append_msg'].assert_called()
    def test_repeated_enable_has_no_duplicate_notice(self):
        self.env['_broadcast_enabled']=False
        self.env['set_broadcast_mode'](True)
        before=len(self.messages())
        self.env['set_broadcast_mode'](True)
        self.assertEqual(len(self.messages()),before)

if __name__=='__main__': unittest.main(verbosity=2)
