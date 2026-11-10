"""Finite real-service duplex test with synthetic echo, speech and a waiting task."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from core.realtime_agent import AgentRealtimeVoice
from core.realtime_voice import load_settings


class EchoStream:
    lock=threading.Lock();loopback=bytearray();near=None;near_offset=0;first_audio=None
    inserted_at=None;overlap=False;voice=None;capture_frames=0
    inject_echo=True

    def __init__(self,**kwargs):self.kwargs=kwargs;self.ended=threading.Event()

    def __enter__(self):
        import numpy as np
        rate=self.kwargs['samplerate'];frames=self.kwargs['blocksize'];interval=frames/rate
        def run():
            next_at=time.monotonic()+interval
            while not self.ended.wait(max(0,next_at-time.monotonic())):
                next_at+=interval
                if rate==24000:
                    data=bytearray(frames*2);self.kwargs['callback'](data,frames,None,None)
                    source=np.frombuffer(data,dtype='<i2').astype(np.float32)/32768
                    rendered=np.interp(np.arange(0,len(source),1.5),np.arange(len(source)),source)
                    with EchoStream.lock:
                        if np.max(np.abs(rendered))>.001 and EchoStream.first_audio is None:EchoStream.first_audio=time.monotonic()
                        EchoStream.loopback.extend((rendered*32767).astype(np.int16).tobytes())
                else:
                    with EchoStream.lock:
                        raw=bytes(EchoStream.loopback[:frames*2]);del EchoStream.loopback[:len(raw)]
                        reference=np.frombuffer(raw.ljust(frames*2,b'\0'),dtype='<i2').astype(np.float32)/32768
                        near=np.zeros(frames,dtype=np.float32)
                        if EchoStream.first_audio and time.monotonic()-EchoStream.first_audio>=1.2:
                            if EchoStream.inserted_at is None:
                                EchoStream.inserted_at=time.monotonic();EchoStream.overlap=EchoStream.voice._playing
                            chunk=EchoStream.near[EchoStream.near_offset:EchoStream.near_offset+frames]
                            near[:len(chunk)]=chunk;EchoStream.near_offset+=len(chunk)
                    mixed=np.clip(reference*(.65 if EchoStream.inject_echo else 0)+near*.8,-.99,.99)
                    self.kwargs['callback']((mixed*32767).astype(np.int16).tobytes(),frames,None,None)
                    EchoStream.capture_frames+=1
        self.thread=threading.Thread(target=run,name='guga-qa-echo-stream',daemon=True);self.thread.start();return self

    def __exit__(self,*args):self.ended.set();self.thread.join(2)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true')
    parser.add_argument('--seconds',type=int,default=40,choices=range(25,61))
    parser.add_argument('--headphone-control',action='store_true',help='same speech, no echo or AEC, to measure service baseline')
    args=parser.parse_args()
    if not args.live:parser.error('--live required; uses confirmed free voice quota')
    cfg=load_settings()
    if not (cfg.get('free_only') and cfg.get('free_quota_confirmed_model')==cfg.get('model') and
            cfg.get('free_quota_confirmed_provider')==cfg.get('provider')):raise RuntimeError('Confirmed free-only configuration required')
    cfg=dict(cfg,headphones=args.headphone_control,owner_only=False);EchoStream.inject_echo=not args.headphone_control
    from tools.probe_echo_dsp import synthetic_speech
    EchoStream.near=synthetic_speech('咕嘎，你现在还能听到我说话吗，请马上回答收到。')
    EchoStream.loopback=bytearray(640*2*3);EchoStream.near_offset=0;EchoStream.first_audio=None
    EchoStream.inserted_at=None;EchoStream.overlap=False;EchoStream.capture_frames=0
    app=QApplication.instance() or QApplication([]);voice=AgentRealtimeVoice();EchoStream.voice=voice
    counts=Counter();errors=[];drops=0;connections=0;heard=False;answered=False;echo_turns=0;delegations=0
    interrupted_at=None;reply_audio_at=None;started=time.monotonic();sent=False;synthetic_transcripts=[]
    background_release=threading.Event();background=threading.Thread(target=lambda:background_release.wait(args.seconds+5),daemon=True)
    background.start();voice.set_task_context(dict(running=True,kind='controlled_background',summary='隔离后台任务仍在等待'))
    voice.screen_context_provider=lambda:dict(active=True,fresh=True,source='windows_uia',visual_content=False,
        context='隔离网页有一个搜索输入框和一个执行测试按钮；后台仍在等待。')
    original=voice._handle_event
    def event(value):
        nonlocal interrupted_at,reply_audio_at
        kind=value.get('type');counts[kind]+=1
        if kind=='error' or (not kind and value.get('code')):
            failure=value.get('error') or value;errors.append(dict(code=failure.get('code'),param=failure.get('param')))
        if kind=='input_audio_buffer.speech_started' and EchoStream.inserted_at is not None and interrupted_at is None:interrupted_at=time.monotonic()
        if kind=='response.audio.delta' and heard and reply_audio_at is None:reply_audio_at=time.monotonic()
        original(value)
    voice._handle_event=event
    def transcript(role,text):
        nonlocal heard,answered,echo_turns
        if role=='饲养员':
            synthetic_transcripts.append(text[:200])
            if ('听' in text and ('话' in text or '我' in text)) or '收到' in text:heard=True
            elif text.strip('，,。！？!? ') not in ('咕嘎','谷歌','哥哥'):echo_turns+=1
        elif heard and any(word in text for word in ('收到','听到','听见','听清')):answered=True
    def connection(value):
        nonlocal connections,drops
        if value:connections+=1
        elif not voice._stop.is_set():drops+=1
    def delegated(ident,text):
        nonlocal delegations
        delegations+=1;voice.resolve_task(ident,dict(ok=True,answer='收到，本鹅听到你说话了。后台仍在执行。'))
    voice.transcript.connect(transcript);voice.connected_changed.connect(connection);voice.task_requested.connect(delegated)
    voice.failed.connect(lambda message:errors.append(dict(failure=message)))
    try:
        with patch('sounddevice.RawOutputStream',EchoStream),patch('sounddevice.RawInputStream',EchoStream):
            voice.start(cfg)
            while time.monotonic()-started<args.seconds:
                app.processEvents()
                if voice.connected and not sent:
                    voice.submit_user_text('这是语音打断验收，请只原样朗读下面这段话，不调用工具：本鹅正在处理当前任务，后台会继续运行。现在检查第一个步骤，然后查看第二个步骤，再检查第三个步骤，继续查看第四个步骤，接下来还要检查第五个步骤，再继续核对第六个步骤，最后检查第七个步骤。')
                    sent=True
                if errors or drops or (answered and reply_audio_at is not None):break
                time.sleep(.005)
            result=dict(ok=bool(heard and answered and EchoStream.overlap and voice._interruptions>=1 and connections==1 and not drops and not errors),
                heard=heard,answered=answered,overlap=EchoStream.overlap,interruptions=voice._interruptions,
                echo_transcripts=echo_turns,connections=connections,drops=drops,errors=errors,delegations=delegations,
                synthetic_input_transcripts=synthetic_transcripts,
                background_still_waiting=background.is_alive(),events=dict(counts),audio_path=voice.audio_path_status(),
                input_to_interrupt_ms=round((interrupted_at-EchoStream.inserted_at)*1000,1) if interrupted_at and EchoStream.inserted_at else None,
                input_to_reply_audio_ms=round((reply_audio_at-EchoStream.inserted_at)*1000,1) if reply_audio_at and EchoStream.inserted_at else None,
                elapsed_seconds=round(time.monotonic()-started,2),microphone=False,speaker=False,audio_saved=False)
            voice.stop()
            if voice._thread:voice._thread.join(5)
            app.processEvents();result['worker_stopped']=not voice.active
    finally:voice.stop();background_release.set();background.join(1)
    result['ok']=bool(result['ok'] and result['worker_stopped'] and echo_turns==0)
    folder=ROOT/'runtime/voice-aec-qa';folder.mkdir(parents=True,exist_ok=True)
    target='synthetic-headphone-control.json' if args.headphone_control else 'synthetic-barge-in.json'
    (folder/target).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True));return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
