"""Finite Edge synthesis -> local Vosk check. Does not open mic or speakers."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import wave

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from launcher import _prepare_qt
_prepare_qt()
from ui.tts_engine import TTSEngine


def run():
    folder = ROOT/'runtime'/'desktop-entry-qa'
    folder.mkdir(parents=True, exist_ok=True)
    output = folder/'edge-local-voice.mp3'
    cfg = SimpleNamespace(EDGE_VOICE=TTSEngine.EDGE_VOICE, EDGE_PITCH=TTSEngine.EDGE_PITCH,
                          _current_rate=lambda:TTSEngine.EDGE_RATE,
                          _safe_remove=lambda path:Path(path).unlink(missing_ok=True))
    result = dict(edge_synthesis=False, local_asr=False, microphone_tested=False, speakers_tested=False)
    result['edge_synthesis'] = TTSEngine._synth_edge(cfg, '咕嘎，打开浏览器。', str(output))
    if result['edge_synthesis']:
        import imageio_ffmpeg
        wav = folder/'edge-local-voice.wav'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error',
                        '-i',str(output),'-ar','16000','-ac','1',str(wav)],
                       check=True,timeout=15,creationflags=0x08000000)
        import vosk
        from core.asr import MODEL_DIR
        vosk.SetLogLevel(-1)
        model=vosk.Model(MODEL_DIR);recognizer=vosk.KaldiRecognizer(model,16000)
        parts=[]
        with wave.open(str(wav),'rb') as stream:
            while True:
                data=stream.readframes(4000)
                if not data:break
                if recognizer.AcceptWaveform(data):parts.append(json.loads(recognizer.Result()).get('text',''))
        parts.append(json.loads(recognizer.FinalResult()).get('text',''))
        result['transcript']=''.join(parts).replace(' ','')
        result['local_asr']='打开浏览器' in result['transcript']
        result['audio_bytes']=output.stat().st_size
    (folder/'local-voice-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['edge_synthesis'] and result['local_asr'] else 1


if __name__=='__main__':sys.exit(run())
