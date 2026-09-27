"""Agent approval/cancellation tests with a stub model and device; no network."""
import asyncio,os,pathlib,tempfile,time,unittest,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
TEMP=tempfile.TemporaryDirectory();os.environ.update(GLASSHAND_ACCESS_TOKEN='test',GLASSHAND_DEVICE_TOKEN='test',GLASSHAND_DATA=TEMP.name,VULTR_INFERENCE_KEY='stub')
import backend.app as m
class Response:
 status_code=200
 def __init__(self,action):self.action=action
 def json(self):
  import json
  return {'choices':[{'message':{'content':json.dumps(self.action)}}]}
class Model:
 actions=[]
 def __init__(self,*a,**kw):pass
 async def __aenter__(self):return self
 async def __aexit__(self,*a):pass
 async def post(self,*a,**kw):return Response(self.actions.pop(0))
class Device:
 def __init__(self):self.commands=[]
 async def send_json(self,p):
  self.commands.append(p)
  if p['kind']=='command':m.pending[p['id']].set_result({'ok':True})
class Tests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  m.httpx.AsyncClient=Model;m.frame=b'synthetic';m.state.update(connected=True,video=True,hid=True,control=True,frame_at=time.time(),last_seen=time.time());m.device=Device();m.approval=None
  m.run={'id':'test','task':'Test task','status':'running','step':0,'pending':None,'approve_each':True}
  Model.actions=[{'type':'tap','x':.5,'y':.5,'reason':'Test tap'},{'type':'done','reason':'Observed completion'}]
 async def wait_approval(self):
  for _ in range(100):
   if m.approval:return
   await asyncio.sleep(.01)
  self.fail('No approval request')
 async def test_decline_sends_no_input(self):
  t=asyncio.create_task(m.agent(m.run));await self.wait_approval();self.assertFalse(m.device.commands);m.approval.set_result(False);await t;self.assertEqual(m.run['status'],'stopped');self.assertFalse(m.device.commands)
 async def test_stop_during_approval(self):
  m.runner=asyncio.create_task(m.agent(m.run));await self.wait_approval();await m.stop();await m.runner;self.assertEqual(m.run['status'],'stopped');self.assertEqual([c['kind'] for c in m.device.commands],['stop'])
 async def test_readonly_rejects_command(self):
  m.state['control']=False
  with self.assertRaisesRegex(RuntimeError,'Read-only'):await m.execute(m.Action(type='tap',x=.5,y=.5),'manual')
  self.assertFalse(m.device.commands)
 async def test_invalid_model_coordinates_fail_closed(self):
  Model.actions=[{'type':'tap','x':2,'y':.5}];await m.agent(m.run);self.assertEqual(m.run['status'],'error');self.assertFalse(m.device.commands)
 async def test_approved_step_then_observed_completion(self):
  async def frames():
   while True:m.state.update(frame_at=time.time(),last_seen=time.time());await asyncio.sleep(.1)
  pump=asyncio.create_task(frames());t=asyncio.create_task(m.agent(m.run));await self.wait_approval();m.approval.set_result(True);await t;pump.cancel();self.assertEqual(m.run['status'],'completed');self.assertEqual(len(m.device.commands),1)
if __name__=='__main__':unittest.main()
