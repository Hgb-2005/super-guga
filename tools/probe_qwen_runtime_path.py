"""Probe the runtime path the pet actually uses: full session -> instructions-only
updates (like _sync_screen_context) -> continuous silence audio frames.

No response.create and no user audio: minimal token/noise. Prints event types and
server errors only; never prints the API key or instructions.
"""
import base64
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
engine = AgentRealtimeVoice()
engine._cfg = cfg
settings = engine._session_settings()
ws = websocket.create_connection(connection_url(cfg),
                                 header=['Authorization: Bearer ' + cfg['api_key'].strip()], timeout=12)
ws.settimeout(0.2)
errors = []
events = []
started = time.monotonic()
sent_full = False
updates = 0
frames = 0
try:
    while time.monotonic() - started < 25:
        try:
            message = ws.recv()
        except websocket.WebSocketTimeoutException:
            message = None
        if message:
            event = json.loads(message)
            kind = event.get('type'); events.append(kind)
            if kind == 'session.created' and not sent_full:
                ws.send(json.dumps(dict(type='session.update', session=settings), ensure_ascii=False))
                sent_full = True
            if kind == 'error':
                err = event.get('error', {}) or {}
                errors.append(dict(code=err.get('code'), message=str(err.get('message', ''))[:300]))
                print('ERROR', errors[-1], 'at', round(time.monotonic() - started, 1))
            if kind == 'session.updated':
                print('SESSION_UPDATED at', round(time.monotonic() - started, 1))
        now = time.monotonic() - started
        # Instructions-only update like _sync_screen_context (no tools field).
        if 2.5 < now < 25 and updates < 3 and (now >= 2.5 + updates * 3):
            weak = dict(instructions=settings['instructions'] + '\n【当前屏幕事实资料】active=false sequence=' + str(updates))
            ws.send(json.dumps(dict(type='session.update', session=weak), ensure_ascii=False))
            updates += 1
            print('SENT instructions-only update', updates, 'at', round(now, 1))
        # Continuous silence frames at ~25 fps like the mic callback.
        if frames < 25 * 20:
            ws.send(json.dumps(dict(type='input_audio_buffer.append',
                                    audio=base64.b64encode(b'\x00' * 1280).decode('ascii'))))
            frames += 1
        time.sleep(0.04)
finally:
    try: ws.close(timeout=1)
    except Exception: pass

print('frames_sent=', frames, 'instructions_updates=', updates)
print('errors=', errors)
print('event_types=', sorted(set(events)))
print('held_seconds=', round(time.monotonic() - started, 1))
