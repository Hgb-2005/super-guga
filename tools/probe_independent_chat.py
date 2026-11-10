"""Actual driver + local scoped memory + LingShu; no DSH gateway, mic or TTS."""
import json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from core.chat_answer import AnswerWorker
from core.chat_workspace import ChatWorkspace
from core.agent_memory import AgentMemoryStore
from core.daily_plan import DailyPlan
from core.companion_memory import CompanionMemory
from core.model_settings import public_settings
from core.chat_memory import close


def run():
    app=QApplication([]);store=ChatWorkspace();memory=AgentMemoryStore(store.path.parent/'agent_memory.sqlite3');daily=DailyPlan(ROOT/'runtime/chat/daily-plan.sqlite3')
    from tools.guga_mcp_server import invoke
    live=invoke('app_state',{}).get('result',{});ident=live.get('current_conversation_id')
    prompt='咕嘎，请用大白话和一个生活里的例子，解释一下大模型为什么能回答问题。两三句话就好。'
    context=CompanionMemory(memory,store,daily).context('001',prompt,ident)
    worker=AnswerWorker();result={};started=time.monotonic()
    def completed(value):result.update(value);app.quit()
    worker.completed.connect(completed)
    worker.start(ident or 'guga-independent-qa',[],prompt,profile=memory.profile('001'),memory_context=context)
    QTimer.singleShot(55000,app.quit);app.exec_()
    if worker.active:worker.cancel()
    report=dict(ok=result.get('ok',False),answer=result.get('text',''),error=result.get('error',''),
                receipt=worker.last_receipt,models=public_settings(),seconds=round(time.monotonic()-started,2),
                dsh_task_used=False,microphone_used=False,tts_used=False)
    out=ROOT/'runtime/independent-chat-qa';out.mkdir(parents=True,exist_ok=True)
    (out/'real-driver-answer.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True));close()
    return 0 if report['ok'] else 1


def run_window():
    from tools.test_personal_harness import IntegrationTests as Harness,APP
    from unittest.mock import patch
    Harness.setUpClass();p=Harness.panel;p.proactive.stop();p.read_aloud.setChecked(False)
    result={};started=time.monotonic()
    try:
        p.send('记住：我喜欢用生活里的例子解释问题')
        p.show()
        with patch.object(p.runtime.dsh,'start',side_effect=AssertionError('ordinary chat must not start DSH')):
            accepted=p.send('按我喜欢的方式，解释一下什么是缓存，两三句话就好。')
            deadline=time.monotonic()+45
            while p.busy and time.monotonic()<deadline:APP.processEvents();time.sleep(.01)
        messages=p.store.messages(p.active_id)
        answer=messages[-1]['content'] if messages and messages[-1]['role']=='assistant' else ''
        result=dict(ok=bool(accepted and not p.busy and p.answer.last_receipt.get('state')=='completed' and answer in p.view.toPlainText()),
                    answer=answer,receipt=p.answer.last_receipt,rendered=bool(answer and answer in p.view.toPlainText()),
                    seconds=round(time.monotonic()-started,2),isolated_ui=True,isolated_memory=True,dsh_task_used=False)
        folder=ROOT/'runtime/independent-chat-qa';folder.mkdir(parents=True,exist_ok=True)
        (folder/'real-chat-window.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True))
    finally:
        p.answer.cancel();Harness.tearDownClass();close()
    return 0 if result.get('ok') else 1


if __name__=='__main__':sys.exit(run_window() if '--window' in sys.argv else run())
