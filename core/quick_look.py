"""On-demand looking: structured controls first, local OCR second, cloud vision last.

The pet must not stare at the screen all day. This module looks only when asked,
and spends the cheapest resource that can actually answer the question:

1. UI Automation - free, milliseconds, exact, but only for windows that expose
   a control tree (plain Win32 apps). Chromium shells expose almost nothing.
2. Windows local OCR - free, offline, a few hundred milliseconds. Reads text
   from any window, including browsers and games.
3. Cloud vision - about one second, billed per image, understands layout,
   icons, charts and colour. Last resort, always asked to answer briefly.

No capture is scheduled here. Callers decide when a look is justified.
"""
import base64
import io
import json
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONFIG_PATH=ROOT/'ai_config.json'
DASHSCOPE='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
DEFAULT_MODEL='qwen3-vl-plus'
MIN_UIA_ELEMENTS=3
MIN_OCR_CHARS=16
CLOUD_MAX_TOKENS=140


def _config():
    try:return json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def _cloud_credentials(cfg):
    voice=cfg.get('realtime_voice') or {}
    key=str(voice.get('api_key') or cfg.get('dashscope_api_key') or '').strip()
    base=str(cfg.get('vision_qwen_base') or DASHSCOPE)
    model=str(cfg.get('vision_qwen_model') or DEFAULT_MODEL)
    return key,base,model


def _window_info(hwnd):
    import win32gui
    try:title=win32gui.GetWindowText(hwnd)[:80]
    except Exception:title=''
    return dict(hwnd=int(hwnd),title=title)


def grab_window(hwnd):
    """Render one window into an image without stealing the foreground.

    PrintWindow asks the window to paint itself, so a covered window still
    yields its own content. GPU-composited surfaces may return black; callers
    fall back to a screen grab in that case.
    """
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image
    left,top,right,bottom=win32gui.GetWindowRect(hwnd)
    width,height=right-left,bottom-top
    if width<=0 or height<=0:raise RuntimeError('窗口没有可见区域')
    width,height=min(width,3840),min(height,2160)
    window_dc=win32gui.GetWindowDC(hwnd)
    source=win32ui.CreateDCFromHandle(window_dc)
    memory=source.CreateCompatibleDC()
    bitmap=win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(source,width,height)
        memory.SelectObject(bitmap)
        ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)
        info=bitmap.GetInfo()
        bits=bitmap.GetBitmapBits(True)
        image=Image.frombuffer('RGB',(info['bmWidth'],info['bmHeight']),bits,'raw','BGRX',0,1)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
        win32gui.ReleaseDC(hwnd,window_dc)
    if image.convert('L').getextrema()[1]<8:raise RuntimeError('窗口没有画出内容')
    return image


def grab_screen(hwnd):
    """Fallback: grab the window rectangle from the screen (needs it visible)."""
    import win32gui
    from PIL import ImageGrab
    rect=win32gui.GetWindowRect(hwnd)
    if rect[2]<=rect[0] or rect[3]<=rect[1]:raise RuntimeError('窗口没有可见区域')
    return ImageGrab.grab(bbox=rect,all_screens=True).convert('RGB')


def _uia_lines(hwnd):
    """Read the control tree. Chromium shells build their tree lazily, so a
    skeletal first read is retried once before we spend a screenshot."""
    from core.uia_service import shared_service
    service=shared_service()
    problem=None
    for attempt in range(2):
        try:
            value=service.perceive(int(hwnd),limit=120,timeout=1.0)
        except Exception as exc:
            # A timeout means the tree is too large to walk, not that it is
            # still being built. Retrying only doubles the wait.
            raise exc
        elements=[item for item in value.get('elements',[]) if item.get('name') or item.get('value')]
        if len(elements)>=MIN_UIA_ELEMENTS:
            priorities={'Edit':0,'ComboBox':0,'Button':1,'TabItem':2,'MenuItem':2,'Document':3,'Text':4}
            elements.sort(key=lambda item:priorities.get(item.get('type'),5))
            lines=[]
            for item in elements[:40]:
                text=str(item.get('name') or '')
                if item.get('value'):text+=('（当前内容：'+str(item['value'])[:120]+'）')
                if not text.strip():continue
                lines.append(str(item.get('type') or '控件')+'：'+text[:160])
            if lines:return '\n'.join(lines)
        if attempt==0:time.sleep(.18)
    if problem:raise problem
    return None


def _local_ocr(image):
    from core.screen_question import recognize
    buffer=io.BytesIO();image.save(buffer,format='PNG')
    text=recognize(buffer.getvalue())
    return str(text or '').strip()


def _cloud_ocr(image,cfg,timeout):
    """qwen-vl-ocr reads text in roughly 400ms, far cheaper than full vision."""
    import requests
    key,base,_=_cloud_credentials(cfg)
    if not key:raise RuntimeError('没有可用的视觉模型密钥')
    frame=image.copy()
    frame.thumbnail((1280,960))
    buffer=io.BytesIO();frame.save(buffer,format='JPEG',quality=85)
    payload=dict(model=str(cfg.get('vision_ocr_model') or 'qwen-vl-ocr'),max_tokens=500,temperature=0,
        messages=[dict(role='user',content=[
            dict(type='image_url',image_url=dict(url='data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode())),
            dict(type='text',text='只输出图里的文字，按原顺序，不要解释。')])])
    response=requests.post(base,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
        json=payload,timeout=(4,timeout))
    if response.status_code!=200:raise RuntimeError('云端识字返回 HTTP '+str(response.status_code))
    text=(response.json().get('choices') or [{}])[0].get('message',{}).get('content','')
    return str(text or '').strip()


def _cloud_vision(image,question,cfg,timeout):
    import requests
    key,base,model=_cloud_credentials(cfg)
    if not key:raise RuntimeError('没有可用的视觉模型密钥')
    frame=image.copy()
    frame.thumbnail((1400,1000))
    buffer=io.BytesIO();frame.save(buffer,format='JPEG',quality=82)
    payload=dict(model=model,max_tokens=CLOUD_MAX_TOKENS,temperature=0.1,
        messages=[dict(role='user',content=[
            dict(type='image_url',image_url=dict(url='data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode())),
            dict(type='text',text=(question or '简要说明画面内容')+' 只回答结论，一句到三句，不要复述题目，不要列步骤。')])])
    response=requests.post(base,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
        json=payload,timeout=(4,timeout))
    if response.status_code!=200:
        raise RuntimeError('视觉模型返回 HTTP '+str(response.status_code))
    data=response.json()
    text=(data.get('choices') or [{}])[0].get('message',{}).get('content','')
    if not str(text).strip():raise RuntimeError('视觉模型没有返回内容')
    return str(text).strip(),dict(model=model,usage=data.get('usage'))


def look(question='',hwnd=0,allow_cloud=True,timeout=12,force_image=False):
    """Answer a question about what is on screen, cheapest channel first."""
    import win32gui
    started=time.monotonic();notes=[]
    hwnd=int(hwnd or win32gui.GetForegroundWindow() or 0)
    if not hwnd or not win32gui.IsWindow(hwnd):
        return dict(ok=False,error='没有可读取的窗口',elapsed_ms=round((time.monotonic()-started)*1000,1))
    window=_window_info(hwnd)
    if not force_image:
        try:
            lines=_uia_lines(hwnd)
            if lines:
                return dict(ok=True,level='uia',cost='free',text=lines,window=window,
                    notes=notes+['结构化控件可读，未截图'],elapsed_ms=round((time.monotonic()-started)*1000,1))
            notes.append('窗口没有可读的控件树')
        except Exception as exc:
            notes.append('控件读取失败：'+type(exc).__name__)
    image=None
    for grab,label in ((grab_window,'窗口自绘'),(grab_screen,'屏幕抓取')):
        try:
            image=grab(hwnd);notes.append(label+'成功');break
        except Exception as exc:
            notes.append(label+'失败：'+str(exc)[:40])
    if image is None:
        return dict(ok=False,error='截不到这个窗口的画面',window=window,notes=notes,
            elapsed_ms=round((time.monotonic()-started)*1000,1))
    text=''
    try:
        text=_local_ocr(image)
        if len(text)>=MIN_OCR_CHARS and not force_image:
            return dict(ok=True,level='ocr',cost='free',text=text[:2000],window=window,
                notes=notes+['本机识字够用，未调用云端'],elapsed_ms=round((time.monotonic()-started)*1000,1))
        notes.append('本机识字内容过少' if text else '画面里没有可识别的文字')
    except Exception as exc:
        notes.append('本机识字不可用：'+str(exc)[:50])
    if allow_cloud and not force_image:
        try:
            cloud_text=_cloud_ocr(image,_config(),max(5,min(timeout,8)))
            if len(cloud_text)>=MIN_OCR_CHARS:
                return dict(ok=True,level='cloud_ocr',cost='cloud',text=cloud_text[:3000],window=window,
                    notes=notes+['用云端识字，未动用视觉理解'],elapsed_ms=round((time.monotonic()-started)*1000,1))
            notes.append('云端识字也没读到多少文字')
        except Exception as exc:
            notes.append('云端识字失败：'+str(exc)[:50])
    if not allow_cloud:
        return dict(ok=False,error='需要看懂画面，但云端视觉被关闭',window=window,notes=notes,
            elapsed_ms=round((time.monotonic()-started)*1000,1))
    try:
        text,extra=_cloud_vision(image,question,_config(),max(5,timeout))
        return dict(ok=True,level='vision',cost='cloud',text=text,window=window,notes=notes,
            vision=extra,elapsed_ms=round((time.monotonic()-started)*1000,1))
    except Exception as exc:
        return dict(ok=False,error='云端视觉不可用：'+str(exc)[:80],window=window,notes=notes,
            elapsed_ms=round((time.monotonic()-started)*1000,1))


def describe(hwnd=0,**kwargs):
    """Shortcut for the common 'what is this window' question."""
    return look('这是什么窗口或页面，正在显示什么内容？',hwnd=hwnd,**kwargs)
