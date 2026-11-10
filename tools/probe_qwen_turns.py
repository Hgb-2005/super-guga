"""Probe: does qianwen close the socket after real response turns?

Simulates the pet's real sequence: full session -> continuous audio frames ->
text user turns + response.create (3 rounds) -> instructions-only updates.
Prints event types, close exception, and any server error messages.
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
engine = AgentRealtimeVoice(); engine._cfg = cfg
settings = engine._session_settings()
ws = websocket.create_connection(connection_url(cfg),
                                 header=['Authorization: Bearer ' + cfg['api_key'].strip()], timeout=12)
ws.settimeout(0.2)
started = time.monotonic()
events = []
errors = []
close_reason = ''
sent_full = False
round_no = 0
last_response_done = 0.0
last_frame = 0.0
LAST = 55
try:
    while time.monotonic() - started < LAST:
        now = time.monotonic() - started
        try:
            message = ws.recv()
        except websocket.WebSocketTimeoutException:
            message = None
        except websocket.WebSocketConnectionClosedException as exc:
            close_reason = str(exc) or 'closed'
            print('CLOSED at', round(now, 1), 'reason=', close_reason)
            break
        if message:
            event = json.loads(message); kind = event.get('type'); events.append(kind)
            if kind == 'session.created' and not sent_full:
                ws.send(json.dumps(dict(type='session.update', session=settings), ensure_ascii=False))
                sent_full = True
            if kind == 'error':
                err = event.get('error', {}) or {}
                errors.append(dict(code=err.get('code'), message=str(err.get('message', ''))[:200]))
                print('ERROR', errors[-1], 'at', round(now, 1))
            if kind == 'session.updated':
                print('SESSION_UPDATED at', round(now, 1))
            if kind == 'response.done':
                last_response_done = now
                print('RESPONSE_DONE round', round_no, 'at', round(now, 1))
        if sent_full and round_no < 3 and now > (round_no + 1) * 2.5 and now > last_response_done + 1.5:
            round_no += 1
            ws.send(json.dumps(dict(type='conversation.item.create', item=dict(
                type='message', role='user',
                content=[dict(type='input_text', text='请只回复“收到”两个字')])), ensure_ascii=False))
            ws.send(json.dumps(dict(type='response.create',
                                    response=dict(modalities=['text', 'audio'])), ensure_ascii=False))
            print('SENT_TURN', round_no, 'at', round(now, 1))
        if now - last_frame > 0.04 and sent_full:
            last_frame = now
            ws.send(json.dumps(dict(type='input_audio_buffer.append',
                                    audio=base64.b64encode(b'\x00' * 1280).decode('ascii'))))
        if sent_full and 12 < now < 13 and round_no == 3:
            ws.send(json.dumps(dict(type='session.update',
                                    session=dict(instructions=settings['instructions'] + '\n测试更新')), ensure_ascii=False))
            print('SENT instructions-only update at', round(now, 1))
finally:
    try: ws.close(timeout=1)
    except Exception: pass

print('turns=', round_no, 'events=', sorted(set(events)))
print('errors=', errors)
print('close_reason=', close_reason)
print('held_seconds=', round(time.monotonic() - started, 1))
