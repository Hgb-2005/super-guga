import json,os,sys,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch,Mock,PropertyMock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from core.personal_harness import PersonalHarness,use_dsh


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=PersonalHarness(self.root/'tickets.db')
    def tearDown(self):self.temp.cleanup()
    def ticket(self):return self.store.create('001','chat','写入并验证文件',self.root)
    def test_recovery_never_replays_and_retains_session(self):
        row=self.ticket();self.store.update(row['id'],session='resume-me',summary='文件已写，尚未回读')
        other=PersonalHarness(self.root/'tickets.db');self.assertEqual(other.recover(),1)
        self.assertEqual(other.get(row['id'])['state'],'interrupted')
        self.assertEqual(other.get(row['id'])['session'],'resume-me')
    def test_experience_requires_human_review_and_receipt(self):
        row=self.ticket();lesson=self.store.propose('001',row['id'],'写入后回读，未确认不要重发')
        self.assertFalse(self.store.lessons('001',active=True))
        self.store.receipt(row['id'],'local_computer',{'action':'read'},{'ok':True})
        self.store.update(row['id'],state='awaiting_review')
        self.store.review('001',row['id'],True)
        self.assertEqual(self.store.lessons('001',active=True)[0]['id'],lesson['id'])
        self.store.disable_lesson('001',lesson['id']);self.assertFalse(self.store.lessons('001',active=True))
    def test_failed_or_receiptless_tasks_cannot_activate_experience(self):
        row=self.ticket();self.store.propose('001',row['id'],'候选')
        self.store.update(row['id'],state='failed');self.store.review('001',row['id'],True)
        self.assertFalse(self.store.lessons('001',active=True))
    def test_member_isolation_and_secret_redaction(self):
        row=self.ticket();self.assertFalse(self.store.recent('002'))
        with self.assertRaises(ValueError):self.store.propose('002',row['id'],'不属于此成员')
        self.store.update(row['id'],summary='sk-'+'A'*32)
        self.assertNotIn('A'*32,self.store.get(row['id'])['summary'])
    def test_followups_persist_and_are_consumed_once(self):
        row=self.ticket();self.store.add_input(row['id'],'再保存一份摘要')
        other=PersonalHarness(self.root/'tickets.db')
        self.assertIn('摘要',other.take_inputs(row['id']));self.assertEqual(other.take_inputs(row['id']),'')
    def test_backend_is_local_first_and_dsh_only_for_engineering(self):
        self.assertFalse(use_dsh('给微信小军发一段介绍并发送'))
        self.assertFalse(use_dsh('打开腾讯会议找到最新录制并上传B站'))
        self.assertTrue(use_dsh('重构这个Python项目并修复多文件测试'))
        self.assertFalse(use_dsh('分析当前截图',image=True))
        self.assertFalse(use_dsh('旧执行器',backend='self'))


from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QSettings,QTimer
APP=QApplication.instance() or QApplication([])


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.pipe='guga-self-test-'+uuid.uuid4().hex
        cls.patches=[patch.dict(os.environ,{'GUGA_MCP_PIPE':cls.pipe}),
                     patch('core.keyboard_hook.KeyboardHook.start'),patch('ui.tts_engine.TTSEngine.speak')]
        for p in cls.patches:p.start()
        QSettings.setDefaultFormat(QSettings.IniFormat);QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,cls.temp.name)
        from ui.pet_window import PetWindow
        from core.chat_workspace import ChatWorkspace
        from ui.agent_chat import AgentChatWindow
        cls.pet=PetWindow()
        for timer in cls.pet.findChildren(QTimer):timer.stop()
        cls.panel=AgentChatWindow(cls.pet,ChatWorkspace(cls.root/'chat.db'));cls.pet._agent_panel=cls.panel
    @classmethod
    def tearDownClass(cls):
        cls.panel.shutdown();cls.pet._self_mcp.close();cls.pet._screen_tools.stop();cls.pet._tray.hide();cls.pet.hide();cls.panel.hide()
        for p in reversed(cls.patches):p.stop()
        cls.temp.cleanup()
    def setUp(self):
        self.panel.busy=False;self.panel._run_kind=None;self.panel.agent.cancel_event.clear()
        self.panel.open_conversation(self.panel.store.create_conversation())
    def pump(self,predicate,seconds=6):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:APP.processEvents();time.sleep(.01)
        self.assertTrue(predicate())
    def test_daily_task_uses_local_agent_while_fast_commands_stay_without_llm(self):
        p=self.panel
        with patch.object(p.runtime.dsh,'start',return_value={'accepted':True}) as dsh,patch.object(p.agent,'start') as old:
            self.assertTrue(p.send('搜索三个来源，写入文件并核对内容'))
            self.assertEqual(p._run_kind,'task');old.assert_called_once();dsh.assert_not_called()
        p.busy=False;p._run_kind=None
        with patch.object(p.runtime.dsh,'start',return_value={'accepted':True,'job_id':'code-job'}) as dsh,patch.object(p.agent,'start') as old:
            self.assertTrue(p.send('重构这个Python项目的多个代码文件并修复测试'))
            self.assertEqual(p._run_kind,'dsh');dsh.assert_called_once();old.assert_not_called()
        p.busy=False;p._run_kind=None
        with patch.object(p,'_fast_command',return_value={'ok':True}) as quick,patch.object(p.runtime.dsh,'start') as dsh:
            p.send('打开浏览器');quick.assert_called_once();dsh.assert_not_called()

    def test_complex_voice_task_requires_one_confirmation_then_starts(self):
        p=self.panel;voice=Mock();p.voice=voice;p.voice_active=True;p.busy=False;p._pending_execution=None
        try:
            with patch.object(p,'sender',return_value=voice),patch.object(p,'_show_active_bubble'),patch.object(p,'send',return_value=True) as send:
                p._voice_request('c1','把最新会议录制上传到B站并准备投稿')
                self.assertFalse(send.called)
                self.assertIsNotNone(p._pending_execution)
                self.assertIn('确认执行',voice.resolve_task.call_args.args[1]['answer'])
                p._voice_request('c2','确认执行')
                send.assert_called_once()
                self.assertIsNone(p._pending_execution)
        finally:
            p.busy=False;p.voice_active=False;p._pending_execution=None
    def test_local_personal_memory_survives_new_conversation(self):
        p=self.panel;p.send('记住：我喜欢简洁的项目报告')
        p.open_conversation(p.store.create_conversation());p.send('查看我的记忆')
        self.assertIn('简洁的项目报告',p.store.messages(p.active_id)[-1]['content'])
        self.assertFalse(p.busy)
    def test_dsh_final_releases_ui_and_remains_awaiting_review(self):
        from tools.test_dsh_worker import FakeProc
        p=self.panel;mgr=p.runtime.dsh
        lines=['{"type":"session","sessionId":"ui-snapshot"}\n',
               '{"type":"status","phase":"step_start"}\n',
               '{"type":"final","text":"这是执行器的交付，需要核对。"}\n']
        with patch('core.dsh_worker.subprocess.Popen',return_value=FakeProc(lines)):
            self.assertTrue(p.send('重构这个Python项目的源码并运行单元测试'))
            self.pump(lambda:not p.busy)
            mgr.thread.join(2)
        self.assertEqual(mgr.status()['ticket_state'],'awaiting_review')
        self.assertIn('尚未独立核验',p.store.messages(p.active_id)[-1]['content'])
        self.assertTrue(p.new_button.isEnabled())
        self.assertNotIn('dsh:'+mgr.state['job_id'],p.pet._anim2d._task_motion.jobs)
        self.assertNotIn('turn:'+p.active_id,p.pet._anim2d._task_motion.jobs)
    def test_important_reports_speak_through_edge_without_a_call(self):
        from core.task_speech import TaskSpeechQueue
        p=self.panel;p.voice_active=False;p._task_speech=TaskSpeechQueue()
        with patch.object(type(p.pet._tts),'is_speaking',new_callable=PropertyMock,return_value=False),patch.object(p.pet._tts,'speak') as speak:
            p._dsh_event(dict(type='dsh_event',job_id='edge-proof',conversation_id=p.active_id,
                             event=dict(type='report',state='progress',milestone=True,summary='录制视频已经下载，接下来处理字幕。')))
            speak.assert_called_once();self.assertIn('字幕',speak.call_args.args[0])
        p._dsh_reports.clear()
    def test_needs_input_is_persisted_as_a_question_instead_of_success(self):
        from tools.test_dsh_worker import FakeProc
        p=self.panel;mgr=p.runtime.dsh
        def launch(*args,**kwargs):
            mgr.state.update(report_state='needs_input',report_summary='成片已准备好，是否发布？')
            return FakeProc(['{"type":"session","sessionId":"needs-input-session"}\n',
                             '{"type":"final","text":"等待用户决定。"}\n'])
        with patch('core.dsh_worker.subprocess.Popen',side_effect=launch):
            self.assertTrue(p.send('修复这个Python项目的源码和单元测试'));self.pump(lambda:not p.busy);mgr.thread.join(2)
        self.assertEqual(mgr.status()['ticket_state'],'needs_input')
        self.assertIn('等待你的决定',p.store.messages(p.active_id)[-1]['content'])
    def test_important_reports_use_the_live_call_without_edge_overlap(self):
        from core.task_speech import TaskSpeechQueue
        p=self.panel;old=p.voice;p._task_speech=TaskSpeechQueue()
        from types import SimpleNamespace
        notify=Mock();p.voice=SimpleNamespace(is_speaking=False,notify_battleground=notify);p.voice_active=True
        try:
            with patch.object(p.pet._tts,'speak') as edge:
                p._dsh_event(dict(type='dsh_event',job_id='voice-proof',conversation_id=p.active_id,
                                 event=dict(type='report',state='needs_input',summary='成片已经准备好，是否发布？')))
                notify.assert_called_once();self.assertTrue(notify.call_args.kwargs['during_task']);edge.assert_not_called()
        finally:p.voice=old;p.voice_active=False;p._dsh_reports.clear()
    def test_stop_confirmation_cancels_dsh(self):
        p=self.panel;p.busy=True;p._run_kind='dsh'
        with patch.object(p.runtime.dsh,'stop') as stop:
            p.send('停止任务');stop.assert_not_called()
            p.send('停止');stop.assert_called_once()
    def test_real_stdio_mcp_to_qt_thread_to_existing_file_executor(self):
        p=self.panel;mgr=p.runtime.dsh
        row=mgr.archive.create('001',p.active_id,'读取测试文件',self.root)
        mgr.state=dict(running=True,job_id='test-job',ticket_id=row['id'],agent_id='001',conversation_id=p.active_id,cwd=str(self.root))
        mgr.stop_event=threading.Event()
        from core.dsh_tools import DshTools
        mgr.tool_executor=DshTools(p,mgr)
        target=self.root/'proof.txt';target.write_text('BRIDGE_PROOF',encoding='utf-8')
        results=[]
        def run():
            from core.mcp_host import StdioMCP
            cli=StdioMCP(dict(command=sys.executable,args=[str(ROOT/'tools/guga_mcp_server.py'),'--dsh-tools'],
                              env={'GUGA_DSH_JOB':'test-job','GUGA_MCP_PIPE':self.pipe}))
            try:
                cli.initialize();tools=cli.request('tools/list')
                results.append(tools)
                results.append(cli.request('tools/call',dict(name='local_computer',arguments=dict(action='read',path=str(target)))))
            except Exception as exc:results.append({'error':str(exc)})
            finally:cli.close()
        thread=threading.Thread(target=run);thread.start();self.pump(lambda:not thread.is_alive(),12)
        self.assertEqual(len(results),2,results)
        self.assertIn('personal_memory',[x['name'] for x in results[0]['tools']])
        self.assertIn('BRIDGE_PROOF',json.dumps(results[1]));self.assertFalse(results[1].get('isError'))
        self.assertEqual(mgr.archive.receipt_count(row['id']),1)
        mgr.stop_event.set();self.assertFalse(mgr.execute_tool('test-job','local_computer',{'action':'read','path':str(target)})['ok'])
        mgr.state['running']=False


if __name__=='__main__':unittest.main()
