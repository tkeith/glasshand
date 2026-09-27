#!/usr/bin/env python3
"""Outbound-only Pi bridge: CSI video + existing USB gadget. No model/API credentials."""
import glob,json,os,queue,re,select,struct,subprocess,threading,time
from pathlib import Path
import websocket
import urllib.request
URL=os.environ['GLASSHAND_DEVICE_URL'];TOKEN=os.environ['GLASSHAND_DEVICE_TOKEN']
CONTROL=os.getenv('GLASSHAND_CONTROL_ENABLED','0')=='1'
CAPTURE=os.getenv('GLASSHAND_CAPTURE_URL','http://127.0.0.1:8092')
ws=None;send_lock=threading.Lock();generation=0;commands=queue.Queue(maxsize=5);last_frame=0

def send(data,binary=False):
 with send_lock:
  if ws:ws.send(data if binary else json.dumps(data),opcode=websocket.ABNF.OPCODE_BINARY if binary else websocket.ABNF.OPCODE_TEXT)
def cmd(args):
 try:return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=3,text=True).stdout
 except Exception:return ''
def hid_ready():
 try:return any(open(p).read().strip()=='configured' for p in glob.glob('/sys/class/udc/*/state'))
 except OSError:return False
def report(path,data):
 fd=os.open(path,os.O_WRONLY|os.O_NONBLOCK)
 try:
  _,ready,_=select.select([],[fd],[],.5)
  if not ready:raise RuntimeError('USB HID write timeout')
  if os.write(fd,data)!=len(data):raise RuntimeError('Incomplete USB report')
 finally:os.close(fd)
def release():
 if not CONTROL:return
 for path,data in [('/dev/hidg0',bytes(8)),('/dev/hidg1',bytes(6)),('/dev/hidg2',bytes(2))]:
  try:report(path,data)
  except OSError:pass
  except RuntimeError:pass
def mouse(x,y,pressed=0):
 if Path('/sys/kernel/config/usb_gadget/solid_iphone/functions/hid.mouse/report_length').read_text().strip()!='6':raise RuntimeError('Absolute mouse is not configured; pointer input blocked')
 report('/dev/hidg1',struct.pack('<BHHb',pressed,round(x*32767),round(y*32767),0))
def keycode(code,modifier=0):
 report('/dev/hidg0',bytes([modifier,0,code,0,0,0,0,0]));time.sleep(.035);report('/dev/hidg0',bytes(8))
plain="1234567890\n\x1b\b\t -=[]\\;\'`,./"
# Explicit standard HID usage mapping (US keyboard layout).
keys={c:(4+i,0) for i,c in enumerate('abcdefghijklmnopqrstuvwxyz')}
keys.update({c.upper():(4+i,2) for i,c in enumerate('abcdefghijklmnopqrstuvwxyz')})
keys.update({c:(30+i,0) for i,c in enumerate('1234567890')})
keys.update({c:(code,0) for c,code in {'\n':40,'\t':43,' ':44,'-':45,'=':46,'[':47,']':48,'\\':49,';':51,"'":52,'`':53,',':54,'.':55,'/':56}.items()})
for a,b in zip('!@#$%^&*()_+{}|:"~<>?','1234567890-=[]\\;\'`,./'):keys[a]=(keys[b][0],2)
def worker():
 while True:
  m,g=commands.get();a=m['action']
  def check():
   if not CONTROL:raise RuntimeError('Read-only session: another operator controls this phone')
   if g!=generation:raise RuntimeError('Action cancelled')
   if time.time()>m['deadline']:raise RuntimeError('Action expired')
   if not hid_ready():raise RuntimeError('USB host is not connected')
   if time.monotonic()-last_frame>6:raise RuntimeError('Video signal is stale')
  try:
   check();typ=a['type']
   if typ=='tap':mouse(a['x'],a['y']);time.sleep(.06);check();mouse(a['x'],a['y'],1);time.sleep(.08);mouse(a['x'],a['y'])
   elif typ=='swipe':
    x,y=a['x'],a['y'];mouse(x,y);time.sleep(.08);check();mouse(x,y,1)
    for i in range(1,21):
     check();mouse(x+(a['end_x']-x)*i/20,y+(a['end_y']-y)*i/20,1);time.sleep(a.get('duration',.4)/20)
    mouse(a['end_x'],a['end_y'])
   elif typ=='type':
    if any(c not in keys for c in a['text']):raise RuntimeError('Unsupported keyboard character')
    for c in a['text']:check();keycode(*keys[c]);time.sleep(.02)
   elif typ=='key':
    if a['key']=='home':
     if Path('/sys/kernel/config/usb_gadget/solid_iphone/configs/c.1/hid.consumer').exists():
      report('/dev/hidg2',struct.pack('<H',0x223));time.sleep(.12);report('/dev/hidg2',bytes(2))
     else:keycode(11,8) # Command-H on iPhone
    else:keycode({'enter':40,'backspace':42,'escape':41,'tab':43}[a['key']])
   else:raise RuntimeError('Unknown action')
   send({'kind':'ack','id':m['id'],'ok':True})
  except Exception as e:
   release()
   try:send({'kind':'ack','id':m['id'],'ok':False,'error':str(e)[:200]})
   except Exception:pass
  finally:commands.task_done()
def capture():
 global last_frame
 while True:
  if not ws:time.sleep(1);continue
  try:
   with urllib.request.urlopen(CAPTURE+'/state',timeout=3) as r:info=json.load(r)
   online=bool(info['result']['source']['online'])
   if online:
    with urllib.request.urlopen(CAPTURE+'/snapshot',timeout=3) as r:img=r.read(2_000_000)
    if not img.startswith(b'\xff\xd8'):raise RuntimeError('Invalid capture image')
    send(img,True);last_frame=time.monotonic()
   send({'kind':'status','video':online,'hid':hid_ready(),'control':CONTROL,'detail':('Live HDMI capture' if CONTROL else 'Read-only: phone in use by another operator') if online else 'Waiting for HDMI video'})
  except Exception:
   try:send({'kind':'status','video':False,'hid':hid_ready(),'control':CONTROL,'detail':'Capture service unavailable'})
   except Exception:pass
  time.sleep(.35)
threading.Thread(target=worker,daemon=True).start();threading.Thread(target=capture,daemon=True).start()
while True:
 try:
  connection=websocket.create_connection(URL,header=['Authorization: Bearer '+TOKEN],timeout=15,enable_multithread=True);ws=connection
  while True:
   try:raw=connection.recv()
   except websocket.WebSocketTimeoutException:connection.ping();continue
   if not raw:break
   data=json.loads(raw)
   if data['kind']=='stop':generation+=1;release()
   elif data['kind']=='command':
    try:commands.put_nowait((data,generation))
    except queue.Full:send({'kind':'ack','id':data['id'],'ok':False,'error':'Device busy'})
 except Exception as e:print('Bridge reconnect:',type(e).__name__,flush=True)
 finally:
  generation+=1;ws=None;release()
  try:connection.close()
  except Exception:pass
 time.sleep(3)
