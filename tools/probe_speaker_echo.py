"""20-second local speaker/mic AEC check. No network or recorded audio files."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    import numpy as np
    import sounddevice as sd
    from core.voice_echo import VoiceEcho
    from core.realtime_voice import load_settings
    from tools.probe_echo_dsp import synthetic_speech
    cfg=load_settings();voice=synthetic_speech('这是咕嘎的本地外放回声测试，正在检查说话和收音能否同时进行。')
    source=np.tile(voice,int(np.ceil(16000*16/len(voice))))[:16000*16]*.4
    source=np.concatenate((np.zeros(16000),source,np.zeros(16000*3)))
    rendered=(np.interp(np.arange(0,len(source),2/3),np.arange(len(source)),source)*32767).astype(np.int16)
    echo=VoiceEcho();offset=0;rows=[];input_errors=0;output_errors=0;begun=time.monotonic()
    def output(data,frames,timing,status):
        nonlocal offset,output_errors
        chunk=rendered[offset:offset+frames];offset+=len(chunk)
        raw=chunk.tobytes().ljust(frames*2,b'\0');data[:]=raw;echo.render(raw);output_errors+=bool(status)
    def capture(data,frames,timing,status):
        nonlocal input_errors
        raw=bytes(data);echo.process(raw);input_errors+=bool(status)
        rows.append((time.monotonic()-begun,echo.input_rms,echo.clean_rms))
    try:
        with sd.RawOutputStream(samplerate=24000,channels=1,dtype='int16',blocksize=960,device=cfg.get('output_device'),callback=output):
            with sd.RawInputStream(samplerate=16000,channels=1,dtype='int16',blocksize=640,device=cfg.get('input_device'),callback=capture):
                while time.monotonic()-begun<20:time.sleep(.02)
        baseline=[r for r in rows if r[0]<.8];speaking=[r for r in rows if 5<r[0]<16]
        mean=lambda values:float(np.mean(values)) if values else 0.
        ambient=mean([r[1]**2 for r in baseline]);raw=mean([r[1]**2 for r in speaking]);clean=mean([r[2]**2 for r in speaking])
        echo_captured=raw>max(1e-7,ambient*1.5)
        reduction=10*np.log10(max(raw-ambient,1e-12)/max(clean-ambient,1e-12)) if echo_captured else None
        result=dict(ok=bool(echo_captured and reduction>=10 and input_errors==0 and output_errors==0),
            echo_captured=echo_captured,echo_reduction_db=round(float(reduction),2) if reduction is not None else None,
            ambient_rms=round(ambient**.5,5),raw_playback_rms=round(raw**.5,5),clean_playback_rms=round(clean**.5,5),
            input_callbacks=len(rows),input_errors=input_errors,output_errors=output_errors,
            engine=echo.status(),seconds=round(time.monotonic()-begun,2),microphone=True,speaker=True,
            network=False,audio_saved=False,devices_closed=True)
    except Exception as exc:
        result=dict(ok=False,error=type(exc).__name__,network=False,audio_saved=False,devices_closed=True)
    finally:echo.close()
    folder=ROOT/'runtime/voice-aec-qa';folder.mkdir(parents=True,exist_ok=True)
    (folder/'physical-speaker-echo.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
