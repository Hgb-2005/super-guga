"""Finite offline AEC check: generated echo and independent near-end signal."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def synthetic_speech(text):
    import numpy as np
    import win32com.client
    stream=win32com.client.Dispatch('SAPI.SpMemoryStream');stream.Format.Type=18
    voice=win32com.client.Dispatch('SAPI.SpVoice')
    voices=list(voice.GetVoices())
    chinese=[token for token in voices if '804' in token.GetAttribute('Language').lower().split(';')]
    if not chinese:raise RuntimeError('Chinese Windows SAPI voice unavailable')
    voice.Voice=chinese[0];voice.AllowAudioOutputFormatChangesOnNextSet=False;voice.AudioOutputStream=stream
    voice.Speak(text)
    fmt=stream.Format.GetWaveFormatEx()
    if fmt.SamplesPerSec!=16000 or fmt.Channels!=1 or fmt.BitsPerSample!=16:raise RuntimeError('SAPI did not honor PCM format')
    return np.frombuffer(bytes(stream.GetData()),dtype='<i2').astype(np.float32)/32768


def main():
    import numpy as np
    from core.voice_echo import VoiceEcho
    rate=16000;seconds=20;n=rate*seconds
    # Local Windows TTS, rendered to memory; no speaker, mic or service call.
    rendered=synthetic_speech('本鹅正在执行任务，接下来会读取当前网页并点击输入框，请继续告诉我下一步怎么做。')
    spoken=synthetic_speech('咕嘎，你现在还能听到我说话吗，请马上回答收到。')
    far=np.tile(rendered,int(np.ceil(n/len(rendered))))[:n]*.7
    near=np.zeros(n,dtype=np.float32);count=min(len(spoken),rate*6)
    near[rate*9:rate*9+count]=spoken[:count]*.8
    delay=rate//10
    echo=np.zeros(n,dtype=np.float32);echo[delay:]=far[:-delay]*.65
    echo[delay+80:]+=far[:-(delay+80)]*.2
    mixed=echo+near;output=np.zeros(n,dtype=np.float32)
    processor=VoiceEcho()
    far24=np.interp(np.arange(0,n,2/3),np.arange(n),far)
    timings=[]
    for offset in range(0,n,640):
        before=time.perf_counter()
        begin24=offset*3//2
        processor.render((far24[begin24:begin24+960]*32767).astype(np.int16).tobytes())
        output[offset:offset+640]=np.frombuffer(processor.process((mixed[offset:offset+640]*32767).astype(np.int16).tobytes()),dtype='<i2').astype(np.float32)/32767
        timings.append((time.perf_counter()-before)*1000)
    silence=slice(rate*5,rate*8);double=slice(rate*9,rate*9+count)
    rms=lambda x:float(np.sqrt(np.mean(x*x)))
    attenuation=20*np.log10((rms(echo[silence])+1e-9)/(rms(output[silence])+1e-9))
    preserved=rms(output[double])/(rms(near[double])+1e-9)
    result=dict(echo_attenuation_db=round(float(attenuation),2),near_end_rms_ratio=round(preserved,3),
        processing_p95_ms=round(float(np.percentile(timings,95)),3),processing_max_ms=round(max(timings),3),
        seconds=seconds,network=False,microphone=False,speech_source='windows_sapi_memory',audio_saved=False,engine=processor.status())
    processor.close()
    result['ok']=bool(attenuation>=20 and .4<=preserved<=1.5 and result['processing_p95_ms']<5)
    folder=ROOT/'runtime/voice-aec-qa';folder.mkdir(parents=True,exist_ok=True)
    (folder/'offline-dsp.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
