"""Real hub scheduler/recipient dispatcher with fake clock and captured transports."""
import ast,json,os,re,threading,time,uuid,tempfile,types,unittest,concurrent.futures
from pathlib import Path
from unittest.mock import Mock
from broadcast_policy import BroadcastPolicy

class Clock:
 def __init__(self):self.now=0;self.timers=[]
 def timer(self,seconds,callback):
  clock=self
  class Timer:
   daemon=False
   def __init__(self):self.at=clock.now+seconds;self.cancelled=False;self.started=False
   def start(self):self.started=True;clock.timers.append(self)
   def cancel(self):self.cancelled=True
   def fire(self):
    if not self.cancelled:callback()
  return Timer()
 def advance(self,seconds):
  self.now+=seconds
  for timer in list(self.timers):
   if timer.started and timer.at<=self.now and not timer.cancelled:
    timer.fire();timer.cancelled=True

class RecipientTests(unittest.TestCase):
 def setUp(self):
  self.clock=Clock();self.deliveries=[]
  self.p=BroadcastPolicy(lambda *a:self.deliveries.append(a),timer_factory=self.clock.timer)
 def put(self,target,sender,mid,**kw):self.p.submit(target,[(sender,mid,mid)],1,**kw)
 def test_counts_each_recipient_excluding_self(self):
  self.put('dsh','dsh','self');self.put('dsh','ag','1');self.put('dsh','arch','2')
  self.assertEqual(self.deliveries,[]);self.put('dsh','codex','3')
  self.assertEqual([x[2] for x in self.deliveries[0][1]],['1','2','3'])
 def test_different_targets_need_own_three(self):
  for i in range(3):self.put('ag','codex',str(i))
  self.put('dsh','ag','only')
  self.assertEqual([d[0] for d in self.deliveries],['ag'])
  self.clock.advance(60);self.assertEqual([d[0] for d in self.deliveries],['ag','dsh'])
 def test_timeout_first_item_not_reset(self):
  self.put('ag','dsh','1');self.clock.advance(59);self.put('ag','codex','2')
  self.assertEqual(self.deliveries,[]);self.clock.advance(1)
  self.assertEqual(len(self.deliveries[0][1]),2);self.assertEqual(self.deliveries[0][3],'peer_timeout')
 def test_full_cancels_old_timer(self):
  for i in range(3):self.put('ag','dsh',str(i))
  self.put('ag','arch','next');self.clock.advance(60)
  self.assertEqual([len(d[1]) for d in self.deliveries],[3,1])
 def test_human_immediate_piggyback_then_separate(self):
  self.put('ag','dsh','old');self.put('ag','human','user1');self.put('ag','human','user2')
  self.assertEqual([[m[2] for m in d[1]] for d in self.deliveries],[['old','user1'],['user2']])
  self.clock.advance(60);self.assertEqual(len(self.deliveries),2)
 def test_repeat_mid_ignored_same_text_different_mid_kept(self):
  self.put('ag','dsh','1');self.put('ag','dsh','1')
  self.p.submit('ag',[('dsh','1','2'),('dsh','1','3')],1)
  self.assertEqual(len(self.deliveries),1);self.assertEqual(len(self.deliveries[0][1]),3)
 def test_reset_and_disable_cancel(self):
  self.put('ag','dsh','old');old=self.clock.timers[-1];self.p.reset(2)
  self.put('ag','dsh','new');old.fire();self.assertEqual(self.deliveries,[])
  self.p.drop('ag');self.clock.advance(60);self.assertEqual(self.deliveries,[])
 def test_explicit_work_call_immediate(self):
  self.put('ag','dsh','task',immediate=True);self.assertEqual(len(self.deliveries),1)
 def test_old_submission_after_reset_rejected(self):
  self.p.reset(2, enabled=True)
  self.p.submit('ag',[('dsh','OLD','old')],1)
  self.p.submit('ag',[('arch','NEW','new')],2)
  self.clock.advance(60)
  self.assertEqual([[m[2] for m in d[1]] for d in self.deliveries],[['new']])
 def test_disabled_mode_rejects_old_broadcast(self):
  self.p.reset(2,enabled=False)
  self.p.submit('ag',[('dsh','OLD','old')],1)
  self.clock.advance(60);self.assertEqual(self.deliveries,[])
 def test_silence_is_free(self):
  for i in range(10):
   self.assertFalse(self.p.observe('ag',True,str(i)));self.assertFalse(self.p.must_reply('ag'));self.assertEqual(self.p.context('ag'),'')
 def test_concurrent_count_timeout_no_loss(self):
  with concurrent.futures.ThreadPoolExecutor(8) as pool:
   list(pool.map(lambda i:self.put('ag','dsh',str(i)),range(100)))
  self.clock.advance(60)
  ids=[m[2] for d in self.deliveries for m in d[1]]
  self.assertEqual(len(ids),100);self.assertEqual(len(set(ids)),100)

class HubFlowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.clock=Clock()
  self.calls=[];self.rows=[];self.ids=0;self.enabled={'codex','ag','dsh','arch'}
  class Thread:
   def __init__(self,target,args=(),**kw):self.target,self.args=target,args
   def start(self):self.target(*self.args)
  ids=['codex','ag','dsh','arch','mimo','doubao','workbuddy']
  route=types.SimpleNamespace(sender_ids=lambda:set(ids),agent_ids=lambda:set(ids),load_providers=lambda:[{'id':i,'display_name':i} for i in ids],RESERVED_IDS=set(ids))
  env=dict(os=os,re=re,json=json,time=time,uuid=uuid,threading=types.SimpleNamespace(Thread=Thread),
   _lock=threading.RLock(),_pending_lock=threading.Lock(),_pending_timer=None,_pending=[],
   _broadcast_enabled=True,_broadcast_generation=1,_broadcast_message_generations={},
   _brainstorm_local=threading.local(),_brainstorms=Mock(),_routing=route,AGENT_LABELS={},_to_map={},DATA_DIR=self.tmp.name,
   ROUNDTABLE_ENABLED=False,_agent_current_tasks={},display_agents=lambda:list(self.enabled),
   agent_is_enabled=lambda i:i in self.enabled,make_auto_dispatcher=lambda i,fn:fn,
   _blocked_targets=lambda t:t,codex_available=lambda:True,activity=lambda *a,**kw:None,
   is_no_reply_needed=lambda t:t in ('[[silent]]','收到','OK'),dispatch_once=lambda *a,**kw:True,
   push_expecting_target=lambda *a:None,bootstrap=types.SimpleNamespace(format_message=lambda s,t:'【'+s+'】：'+t),
   read_msgs=lambda *a:self.rows)
  env['_brainstorms'].load.return_value={'questions':[]}
  for i,name in [('codex','ask_codex'),('ag','ask_antigravity'),('dsh','inject_qq'),('arch','ask_arch'),('mimo','ask_mimo'),('doubao','ask_doubao'),('workbuddy','ask_workbuddy')]:
   env[name]=lambda text,*a,_id=i,**kw:self.calls.append((_id,text))
  env['launch_agent']=lambda target,fn,text,**kw:fn(text)
  source=Path(__file__).with_name('hub.py').read_text(encoding='utf-8-sig')
  names=['dispatch_targets','broadcast_state','broadcast_active','schedule_batch','flush_batch','_deliver_to_target','_brainstorm_dispatch']
  nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in names]
  exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual-hub>','exec'),env)
  env['_broadcast_policy']=BroadcastPolicy(lambda *args:env['_deliver_to_target'](*args),timer_factory=self.clock.timer)
  self.env=env
 def send(self,sender,text,to=None):
  self.ids+=1;mid=str(self.ids)
  self.rows.append({'id':mid,'sender':sender,'text':text,'broadcast':True,'broadcast_generation':1})
  self.env['_broadcast_message_generations'][mid]=1
  self.env['_to_map'][mid]=to or [i for i in self.enabled if i!=sender]
  self.env['schedule_batch'](text,sender,mid)
 def test_three_other_messages_for_every_target(self):
  self.send('ag','A');self.send('dsh','B');self.send('codex','C')
  self.assertEqual([i for i,t in self.calls],['arch'])
  self.assertTrue(all(x in self.calls[0][1] for x in ['A','B','C']))
  self.send('arch','D')
  self.assertEqual({i for i,t in self.calls},self.enabled)
  for i,t in self.calls:self.assertEqual(t.count('】：'),3);self.assertNotIn('广播模式执行约束',t)
 def test_real_dispatch_timeout(self):
  self.send('ag','TAIL');self.clock.advance(59);self.assertEqual(self.calls,[])
  self.clock.advance(1);self.assertEqual({i for i,t in self.calls},self.enabled-{'ag'})
 def test_human_not_waiting_and_no_forced_prefix(self):
  self.send('ag','PEER');self.send('human','USER1');self.send('human','USER2')
  for i in self.enabled:
   texts=[t for who,t in self.calls if who==i];self.assertEqual(len(texts),2)
   self.assertIn('USER1',texts[0]);self.assertIn('USER2',texts[1]);self.assertNotIn('上一轮',''.join(texts))
 def test_ack_not_counted(self):
  self.send('ag','收到');self.send('dsh','[[silent]]');self.clock.advance(60)
  self.assertEqual(self.calls,[])
 def test_stale_generation_and_disabled_target(self):
  self.send('ag','OLD');self.env['_broadcast_enabled']=False;self.clock.advance(60);self.assertEqual(self.calls,[])
 def test_workmode_single_target_no_debounce(self):
  self.env['_broadcast_enabled']=False
  self.env['_broadcast_message_generations']={}
  self.rows.append({'id':'direct','sender':'ag','text':'TASK'})
  self.env['_to_map']['direct']=['codex']
  self.env['schedule_batch']('TASK','ag','direct')
  self.assertEqual(self.calls,[('codex','【ag】：TASK')])
 def test_disabled_worker_drops_queued_job(self):
  source=Path(__file__).with_name('hub.py').read_text(encoding='utf-8-sig')
  node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_agent_queue_worker')
  fn=Mock();env={'agent_is_enabled':lambda name:False,'_agent_queue_lock':threading.Lock(),'_agent_queues':{'ag':[{'fn':fn}]},'_agent_workers':{'ag':True}}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<worker>', 'exec'),env)
  env['_agent_queue_worker']('ag');fn.assert_not_called();self.assertNotIn('ag',env['_agent_queues'])
 def test_close_control_reaches_all_immediately(self):
  source=Path(__file__).with_name('hub.py').read_text(encoding='utf-8-sig')
  node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='set_broadcast_mode')
  self.env.update(_agent_queue_lock=threading.Lock(),_agent_queues={})
  def append(sender,text,kind='text',to=None,schedule=True):
   mid='close-control'
   self.rows.append({'id':mid,'sender':sender,'text':text,'to':to,'kind':kind})
   self.env['_to_map'][mid]=to
   return {'id':mid}
  self.env['append_msg']=append
  exec(compile(ast.Module(body=[node],type_ignores=[]),'<toggle>', 'exec'),self.env)
  self.send('ag','QUEUED-OLD')
  self.env['set_broadcast_mode'](False)
  self.assertEqual({i for i,t in self.calls},self.enabled)
  self.assertTrue(all('已关闭' in t and 'QUEUED-OLD' not in t for i,t in self.calls))
  self.clock.advance(60);self.assertEqual(len(self.calls),4)
 def test_no_forced_code_paths(self):
  s=Path(__file__).with_name('hub.py').read_text(encoding='utf-8-sig')
  self.assertNotIn('请对刚才收到的话题补充一条实质回复',s)
  self.assertNotIn('上轮静默的成员下轮必须实质回复',s)

if __name__=='__main__':unittest.main(verbosity=2)
