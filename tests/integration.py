"""Isolated integration test: synthetic device; NEVER connects to the real Pi."""
import asyncio,base64,io,json,os,pathlib,subprocess,tempfile,time
import httpx,websockets
from PIL import Image
ROOT=pathlib.Path(__file__).resolve().parents[1]
async def main():
 with tempfile.TemporaryDirectory() as data:
  env=dict(os.environ,GLASSHAND_ACCESS_TOKEN='test-access',GLASSHAND_DEVICE_TOKEN='test-device',GLASSHAND_DATA=data,GLASSHAND_ORIGIN='https://test.local',VULTR_INFERENCE_KEY='')
  proc=subprocess.Popen([str(pathlib.Path(os.sys.executable).parent/'uvicorn'),'backend.app:app','--host','127.0.0.1','--port','18081','--no-access-log'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  base='http://127.0.0.1:18081'
  try:
   async with httpx.AsyncClient(base_url=base,timeout=10) as c:
    for _ in range(60):
     try:await c.get('/');break
     except httpx.ConnectError:await asyncio.sleep(.1)
    assert (await c.get('/api/state')).status_code==401
    assert (await c.get('/api/frame')).status_code==401
    assert (await c.post('/api/session',json={'token':'test-access'})).status_code==403
    h={'Origin':'https://test.local'}
    assert (await c.post('/api/session',json={'token':'wrong'},headers=h)).status_code==401
    r=await c.post('/api/session',json={'token':'test-access'},headers=h);assert r.status_code==200
    h['Cookie']='gh_session='+r.cookies['gh_session']
    assert (await c.post('/api/action',json={'type':'key','key':'home'},headers=h)).status_code==409
    assert (await c.post('/api/calibration',json={'left':60,'right':60},headers=h)).status_code==422
    try:
     async with websockets.connect('ws://127.0.0.1:18081/api/device'):raise AssertionError('unauthorized websocket accepted')
    except websockets.exceptions.InvalidStatus:pass
    async with websockets.connect('ws://127.0.0.1:18081/api/device',additional_headers={'Authorization':'Bearer test-device'}) as ws:
     im=Image.new('RGB',(320,640),'#91b48a');buf=io.BytesIO();im.save(buf,'JPEG')
     await ws.send(json.dumps({'kind':'status','video':True,'hid':True,'control':True,'detail':'SYNTHETIC TEST DEVICE'}));await ws.send(buf.getvalue());await asyncio.sleep(.1)
     assert (await c.get('/api/frame',headers=h)).status_code==200
     task=asyncio.create_task(c.post('/api/action',json={'type':'tap','x':.4,'y':.6},headers=h))
     # Manual takeover emits stop before the action.
     assert json.loads(await ws.recv())['kind']=='stop'
     command=json.loads(await ws.recv());assert command['action']['type']=='tap';assert not task.done()
     await ws.send(json.dumps({'kind':'ack','id':command['id'],'ok':True}))
     assert (await task).status_code==200
     events=(await c.get('/api/state',headers=h)).json()['events'];assert any('acknowledged' in e['text'] for e in events)
     assert (await c.post('/api/action',json={'type':'tap','x':-1,'y':.5},headers=h)).status_code==422
     assert (await c.post('/api/action',json={'type':'type','text':'☃'},headers=h)).status_code==422
     # A stop reaches the device channel; it does not merely update the UI.
     assert (await c.post('/api/stop',json={},headers=h)).status_code==200
     assert json.loads(await ws.recv())['kind']=='stop'
     await ws.send(json.dumps({'kind':'status','video':False,'hid':True,'detail':'signal lost'}));await asyncio.sleep(.1)
     assert (await c.get('/api/frame',headers=h)).status_code==503
     assert (await c.post('/api/action',json={'type':'key','key':'home'},headers=h)).status_code==409
    await asyncio.sleep(.1)
    assert not (await c.get('/api/state',headers=h)).json()['device']['connected']
    print('PASS: auth, Origin checks, WebSocket auth, frame relay, acknowledged input, validation, stop forwarding, signal loss, disconnect')
  finally:proc.terminate();proc.wait(timeout=5)
asyncio.run(main())
