#!/usr/bin/env python3
"""Outbound-only Pi bridge: CSI video + existing USB gadget. No model/API credentials."""
import glob,json,os,queue,re,select,struct,subprocess,threading,time
import websocket
URL=os.environ['GLASSHAND_DEVICE_URL'];TOKEN=os.environ['GLASSHAND_DEVICE_TOKEN']
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
 for path,data in [('/dev/hidg0',bytes(8)),('/dev/hidg1',bytes(6)),('/dev/hidg2',bytes(2))]:
  try:report(path,data)
  except OSError:pass
  except RuntimeError:pass
def mouse(x,y,pressed=0):report('/dev/hidg1',struct.pack('<BHHb',pressed,round(x*32767),round(y*32767),0))
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
    if a['key']=='home':report('/dev/hidg2',struct.pack('<H',0x223));time.sleep(.08);report('/dev/hidg2',bytes(2))
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
  signal=bool(re.search(r'power_present:\s*1',cmd(['v4l2-ctl','--get-ctrl=power_present'])))
  hid=hid_ready()
  try:send({'kind':'status','video':False,'hid':hid,'detail':'Waiting for HDMI video' if not signal else 'Starting capture'})
  except Exception:time.sleep(2);continue
  if not signal:time.sleep(2);continue
  timing=cmd(['v4l2-ctl','--query-dv-timings']);w=re.search(r'Active width:\s*(\d+)',timing);h=re.search(r'Active height:\s*(\d+)',timing)
  if not w or not h:time.sleep(2);continue
  width,height=int(w[1]),int(h[1]);cmd(['v4l2-ctl','--set-dv-bt-timings=query']);cmd(['v4l2-ctl',f'--set-fmt-video=width={width},height={height},pixelformat=UYVY'])
  proc=subprocess.Popen(['ffmpeg','-nostdin','-loglevel','error','-threads','1','-f','v4l2','-input_format','uyvy422','-video_size',f'{width}x{height}','-i','/dev/video0','-vf',"fps=3,scale='min(1280,iw)':-2",'-threads','1','-f','image2pipe','-vcodec','mjpeg','-q:v','5','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  buf=b'';last=time.monotonic();last_status=0
  try:
   while ws and proc.poll() is None:
    ready,_,_=select.select([proc.stdout],[],[],1)
    if not ready:
     if time.monotonic()-last>5:break
     continue
    chunk=os.read(proc.stdout.fileno(),65536)
    if not chunk:break
    buf+=chunk
    while b'\xff\xd9' in buf:
     end=buf.index(b'\xff\xd9')+2;img=buf[:end];buf=buf[end:];start=img.find(b'\xff\xd8')
     if start<0:continue
     send(img[start:],True);last_frame=last=time.monotonic()
     if last-last_status>1:
      send({'kind':'status','video':True,'hid':hid_ready(),'detail':'Live HDMI capture'});last_status=last
    if len(buf)>4_000_000:buf=b''
  except Exception:pass
  finally:
   proc.terminate()
   try:proc.wait(timeout=2)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
  time.sleep(1)
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
