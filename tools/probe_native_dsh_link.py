"""Isolated native plugin integration, no user account, mic or desktop actions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtCore import QCoreApplication,QObject
from core.daily_plan import DailyPlan
from core.dsh_link import DshLink
from ui.mcp_bridge import SelfMCPBridge


def main():
    app=QCoreApplication([])
    os.environ['GUGA_MCP_PIPE']='guga-self-native-probe-'+uuid.uuid4().hex[:20]
    class Pet(QObject):
        def _ensure_agent_panel(self):return panel
    pet=Pet();panel=QObject(pet)
    panel.busy=False;panel.voice=None;panel.voice_active=False;panel.active_id='isolated-chat'
    notices=[];panel._say_active_reply=lambda *a,**kw:notices.append(a[0]);panel.render=lambda:None
    panel.store=SimpleNamespace(append=lambda *a:None)
    with tempfile.TemporaryDirectory() as folder:
        plan=DailyPlan(Path(folder)/'plan.sqlite3')
        link=DshLink(panel)
        panel.runtime=SimpleNamespace(dsh_link=link,execute=lambda name,args:plan.execute(args) if name=='daily_plan' else {})
        bridge=SelfMCPBridge(pet)
        if not bridge.available:raise RuntimeError('隔离管道未启动')
        cfg=dict(projectRoot=str(ROOT),python=sys.executable,pipeName=os.environ['GUGA_MCP_PIPE'],
                 profileManifest=str(Path.home()/'.dsh/profiles/web/package.json'),
                 runtimeAnchor=str(Path.home()/'.local/bin/node_modules/@deepseek-ai/dsh/package.json'))
        file=Path(folder)/'config.json';file.write_text(json.dumps(cfg),encoding='utf-8')
        proc=subprocess.Popen([shutil.which('node'),str(ROOT/'tools/probe_native_dsh_plugin.mjs'),str(file)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+20
        while proc.poll() is None and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        if proc.poll() is None:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
        out,err=proc.communicate(timeout=5)
        status=link.status();rows=status['sessions']
        link.tick()
        report=dict(node_exit=proc.returncode,native_plugin_booted=proc.returncode==0,events_received=status['events_received'],
            plugin_list_metadata_verified=proc.returncode==0 and '"plugin_list_metadata_verified":true' in out,
            terminal_received=bool(rows and rows[0]['state']=='finished'),goal_received=bool(rows and '打开测试浏览器' in rows[0].get('goal','')),
            public_reply_received=bool(rows and '合成结果已返回' in rows[0].get('summary','')),
            reasoning_filtered='DO_NOT_FORWARD' not in json.dumps(status),narration_received=bool(notices),
            real_desktop_verified=False,model_called=False,diagnostic=err[-700:] if proc.returncode else '')
        report['ok']=all(report[k] for k in ('native_plugin_booted','terminal_received','goal_received','public_reply_received','reasoning_filtered','narration_received'))
        bridge.close();link.timer.stop()
    output=ROOT/'runtime/native-dsh-voice-qa';output.mkdir(exist_ok=True)
    (output/'native-plugin-integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True));return 0 if report['ok'] else 1


if __name__=='__main__':sys.exit(main())
