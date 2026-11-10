"""Real configured driver -> Edge audio artifact; no microphone or speaker."""
import json,os,sys,time
from pathlib import Path
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from core.chat_answer import AnswerWorker
from core.model_settings import load_config,read_raw
from ui.tts_engine import TTSEngine


def run():
    app=QApplication([]);worker=AnswerWorker();result={};started=time.monotonic()
    def completed(value):result.update(value);app.quit()
    worker.completed.connect(completed)
    worker.start('guga-driver-edge-check',[],'只回复：咕嘎连接成功',read_aloud=True)
    QTimer.singleShot(28000,app.quit);app.exec_()
    if worker.active:worker.cancel()
    cfg=load_config();raw=read_raw();folder=ROOT/'runtime/model-voice-repair-qa';folder.mkdir(parents=True,exist_ok=True)
    report=dict(driver_ok=bool(result.get('ok')),model=cfg.get('model'),answer=result.get('text','')[:200],
                microphone_tested=False,speaker_tested=False,realtime_model_used=False,
                driver_seconds=round(time.monotonic()-started,2))
    if result.get('ok'):
        target=folder/'driver-reply-edge.mp3'
        engine=SimpleNamespace(EDGE_VOICE=raw.get('edge_voice') or TTSEngine.EDGE_VOICE,
            EDGE_PITCH=raw.get('edge_pitch') or TTSEngine.EDGE_PITCH,
            _current_rate=lambda:raw.get('edge_rate') or TTSEngine.EDGE_RATE,
            _safe_remove=lambda p:Path(p).unlink(missing_ok=True))
        report['edge_ok']=TTSEngine._synth_edge(engine,result['text'],str(target))
        report['audio_bytes']=target.stat().st_size if target.exists() else 0
    else:report['error']=result.get('error','驱动模型未在限定时间内返回')
    (folder/'driver-edge-chain.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    return 0 if report['driver_ok'] and report.get('edge_ok') else 1


if __name__=='__main__':sys.exit(run())
