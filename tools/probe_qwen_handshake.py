"""Read-only probe: qianwen realtime session handshake with the current tools.

No audio capture, no model response request, no user data in the log. Only
prints event types and server error messages (never the API key).
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
import websocket

app = QApplication.instance() or QApplication([])
from core.realtime_voice import load_settings, connection_url
from core.realtime_agent import AgentRealtimeVoice

cfg = load_settings()
class Dummy: pass
engine = AgentRealtimeVoice()
engine._cfg = cfg
settings = engine._session_settings()
url = connection_url(cfg)
print('provider=', cfg.get('provider'), 'model=', cfg.get('model'),
      'tools=', [t['name'] for t in settings.get('tools', [])],
      'instructions_chars=', len(settings.get('instructions', '')))

ws = websocket.create_connection(url, header=['Authorization: Bearer ' + cfg['api_key'].strip()], timeout=12)
ws.settimeout(1.0)
events = []
errors = []
t0 = time.monotonic()
sent_update = False
try:
    while time.monotonic() - t0 < 20:
        try:
            message = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if not message:
            print('CLOSED_EMPTY at', round(time.monotonic() - t0, 1)); break
        event = json.loads(message)
        kind = event.get('type')
        events.append(kind)
        if kind == 'session.created' and not sent_update:
            ws.send(json.dumps(dict(type='session.update', session=settings), ensure_ascii=False))
            sent_update = True
        if kind == 'error':
            err = event.get('error', {}) or {}
            errors.append(dict(code=err.get('code'), message=str(err.get('message', ''))[:300]))
        if kind == 'session.updated':
            print('SESSION_UPDATED at', round(time.monotonic() - t0, 1))
finally:
    try: ws.close(timeout=1)
    except Exception: pass

print('events=', events[:20])
print('errors=', errors)
print('held_seconds=', round(time.monotonic() - t0, 1))
