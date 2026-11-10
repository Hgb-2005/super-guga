"""Bounded real-model + real-browser acceptance on a loopback synthetic form."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import Mock,patch
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from core.self_agent import SelfAgent,TOOLS
from core.agent_task import TaskJournal
from core.browser_agent import execute as real_browser
from core.browser_session import BrowserSession
from tools.test_browser_session import Handler,ThreadingHTTPServer
import requests


def main():
    app=QApplication.instance() or QApplication([])
    folder=ROOT/'runtime/computer-assistant-qa/model';folder.mkdir(parents=True,exist_ok=True)
    config=json.loads((ROOT/'ai_config.json').read_text(encoding='utf-8'));config['task_budget_seconds']=100
    bridge=Mock();bridge.call.return_value=dict(ok=True,result=dict(pet=dict(visible=False)))
    agent=SelfAgent(bridge,config=config);agent.journal=TaskJournal(folder/'tasks')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
    url='http://127.0.0.1:'+str(server.server_port)
    done=[];agent.completed.connect(done.append);started=time.monotonic();count=0;actions=[]
    request=agent._request;post=requests.post;before=Handler.submissions
    def bounded_request(*args,**kwargs):
        nonlocal count
        count+=1
        if count>10 or time.monotonic()-started>95:raise RuntimeError('验收达到10次请求/95秒预算，停止')
        return request(*args,**kwargs)
    def bounded_post(*args,**kwargs):
        kwargs['timeout']=(5,20);return post(*args,**kwargs)
    def browser(computer,args,cancel):
        args=dict(args);action=args.get('action');actions.append(action)
        if action in ('open','close_tab','upload','download'):raise ValueError('本次验收只允许专用浏览器本地表单')
        if args.get('url') and urlsplit(args['url']).netloc!=urlsplit(url).netloc:raise ValueError('验收只能访问指定回环测试服务')
        if action=='session_start':args['headless']=True
        return real_browser(computer,args,cancel)
    with tempfile.TemporaryDirectory() as temporary:
        session=BrowserSession(Path(temporary)/'profile');agent.computer.browser_session=session
        try:
            with patch('core.self_agent.TOOLS',[t for t in TOOLS if t['function']['name'] in ('browser','task_plan')]), \
                 patch.object(agent,'_request',side_effect=bounded_request), \
                 patch('core.self_agent.requests.post',side_effect=bounded_post), \
                 patch('core.browser_agent.execute',side_effect=browser), \
                 patch.object(agent.computer,'execute',side_effect=RuntimeError('本次验收不用宿主鼠标键盘')):
                agent._run('browser-workflow-acceptance',[],
                    '请用专用浏览器打开'+url+'，姓名填咕嘎验收，城市选上海，备注写多步任务验收，勾选同意。'
                    '点击下一步，编号填20261102，然后确认提交一次，读取页面核实出现提交成功。仅操作这个本地测试表单。',threading.Event())
            result=done[0] if done else dict(ok=False,error='没有完成事件')
            session_state=real_browser(agent.computer,dict(action='tabs'),threading.Event())
            tabs=session_state.get('tabs',[])
            verification=real_browser(agent.computer,dict(action='snapshot',page_id=tabs[0]['page_id']),threading.Event()) if tabs else {}
            out=dict(ok=bool(result.get('ok') and Handler.submissions-before==1 and '提交成功：咕嘎验收' in verification.get('text','')),
                agent_ok=result.get('ok'),submissions=Handler.submissions-before,model_requests=count,actions=actions,
                elapsed_seconds=round(time.monotonic()-started,2),error=result.get('error'),
                exact_success_text='提交成功：咕嘎验收' in verification.get('text',''),
                real_browser=True,host_input=False,external_site=False)
            (folder/'result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(out,ensure_ascii=False))
        finally:
            session.close();server.shutdown();server.server_close();server_thread.join(2)


if __name__=='__main__':main()
