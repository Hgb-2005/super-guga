"""Bounded real desktop/MCP probe. Creates only clearly marked temporary tasks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.guga_mcp_server import invoke
from PyQt5.QtCore import QCoreApplication
from core.daily_plan import DailyPlan


def main():
    app=QCoreApplication([])
    folder=ROOT/'runtime/daily-dsh-qa'
    folder.mkdir(parents=True,exist_ok=True)
    token='验收临时事项_'+uuid.uuid4().hex[:8]
    report={};ids=[]
    try:
        def call(name,args):
            result=invoke(name,args)
            if not result.get('ok'):raise RuntimeError(str(result.get('error') or result.get('result')))
            return result['result']
        state=call('app_state',{})
        report['runtime']=state['runtime']
        report['chat_add']=call('applications',dict(action='command',name='每日计划 添加 '+token))['ok']
        state=call('daily_plan',dict(action='list'))
        item=next(x for x in state['templates'] if x['title']==token);ids.append(item['id'])
        checked=call('applications',dict(action='command',name='每日计划 完成 '+token))
        report['chat_check']=next(x for x in checked['daily_plan']['items'] if x['task_id']==item['id'])['done']
        updated=call('applications',dict(action='command',name='每日计划 把 '+token+' 改成 '+token+'_已改名'))
        report['chat_rename']=any(x['title']==token+'_已改名' and x['done'] for x in updated['daily_plan']['items'])
        call('daily_plan',dict(action='open'))
        report['dialog_visible']=call('app_state',{})['daily_plan']['dialog_visible']
        windows=call('computer',dict(action='windows',query='每日计划'))
        report['desktop_windows']=len(windows.get('windows',[]))
        from tools.run_dsh_guga import command as dsh_command
        command,env=dsh_command('headless',json_events=True)
        prompt=('这是咕嘎插件的隔离验收。只执行以下三项，不用shell、不创建/改写计划、不发微信。'
                '1.调用mcp__guga__daily_plan(action=list)，读出已勾选的验收临时事项。'
                '2.调用mcp__guga__computer(action=windows,query=每日计划)，确认桌面窗口。'
                '3.调用mcp__guga__pet_say(text=DSH插件已经连接咕嘎，每日计划可以一起使用啦。)。'
                '然后只简短说明真实回执，不调用其他工具。')
        started=time.monotonic()
        proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,encoding='utf-8',env=env,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        try:out,err=proc.communicate(prompt,timeout=120)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=8,creationflags=subprocess.CREATE_NO_WINDOW)
            out,err=proc.communicate(timeout=5)
            report['dsh_timeout']=True
        report.update(dsh_exit_code=proc.returncode,dsh_seconds=round(time.monotonic()-started,1))
        events=[]
        for line in out.splitlines():
            try:
                event=json.loads(line)
                if isinstance(event,dict):events.append(event)
            except ValueError:pass
        # Keep only event types, tool names and final text, never provider headers/configs.
        report['dsh_events']=[{k:e[k] for k in ('type','name','toolName','tool','status','text','message','ok','error') if k in e}
                              for e in events if e.get('type')!='thinking']
        called=set()
        for e in events:
            if e.get('type') in ('tool_call','tool_result'):
                called.add(str(e.get('tool') or e.get('name') or e.get('toolName') or ''))
        report['dsh_called_tools']=sorted(called)
        report['dsh_plugin_complete']=all(any(name in x for x in called) for name in ('daily_plan','computer','pet_say')) and proc.returncode==0
        if not report['dsh_plugin_complete']:
            from core.desktop_access import redact
            report['dsh_diagnostic']=redact(err[-1600:])
        report['ok']=all(report.get(k) for k in ('chat_add','chat_check','chat_rename','dialog_visible','dsh_plugin_complete'))
    finally:
        # Remove precisely the probe's rows, keeping every user-created task intact.
        plan=DailyPlan()
        with plan._db() as db:
            for ident in ids:
                row=db.execute('SELECT title FROM tasks WHERE id=?',(ident,)).fetchone()
                if row and row['title'].startswith(token):
                    for table,key in (('checks','task_id'),('versions','task_id'),('tasks','id')):
                        db.execute('DELETE FROM '+table+' WHERE '+key+'=?',(ident,))
        invoke('daily_plan',dict(action='list'))
        report['temporary_tasks_removed']=True
        (folder/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    return 0 if report.get('ok') else 1


if __name__=='__main__':sys.exit(main())
