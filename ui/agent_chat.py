"""Guga's floating workspace: projects, durable chats, voice and observable self-control."""
import html
import json
import time
from pathlib import Path

FAST_DIAGNOSTICS_PATH=Path(__file__).resolve().parents[1]/'runtime/voice/fast_command_diagnostics.json'

from PyQt5.QtCore import Qt, QEvent, QTimer
from PyQt5.QtGui import QFont, QPixmap
from PyQt5.QtWidgets import (QDialog, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QTextBrowser, QPlainTextEdit, QInputDialog, QMenu,
    QSplitter, QCheckBox, QMessageBox)

from core.chat_workspace import ChatWorkspace
from core.self_agent import SelfAgent, ToolBridge
from core.realtime_agent import AgentRealtimeVoice
from core.task_control import TaskControlGate
from ui.agent_runtime import AppRuntime


class AgentChatWindow(QDialog):
    def __init__(self, pet, store=None, memory=None, agent_id=None):
        super().__init__(pet, Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint)
        self.pet = pet
        self.store = store or ChatWorkspace()
        from core.agent_memory import AgentMemoryStore
        self.memory=memory or AgentMemoryStore(self.store.path.parent/'agent_memory.sqlite3')
        # Every app launch begins with Guga 001. Companion identities are entered
        # explicitly by summon/right-click during this session.
        requested_agent=str(agent_id or '001')
        self.active_profile=self.memory.profile(requested_agent) or self.memory.profile('001')
        if not self.active_profile:raise ValueError('咕嘎身份档案不可用')
        self.active_agent_id=self.active_profile['agent_id']
        self.active_id = None
        self.busy = False
        self.voice_active = False
        self.voice = None
        self._voice_task = None
        self._voice_readback = False
        self._task_control = TaskControlGate()
        self._last_fast_result=None
        self.show_archived = False
        self.expanded_logs = set()
        self.runtime = AppRuntime(pet, self, self.store)
        self.bridge = ToolBridge(self.runtime.execute, self)
        self.agent = SelfAgent(self.bridge, self)
        self.agent.completed.connect(self._completed)
        self.agent.activity.connect(self._activity)
        self.agent.progress.connect(self._task_progress)
        from core.chat_answer import AnswerWorker
        from core.short_voice import ShortVoice
        self.answer=AnswerWorker(self);self.answer.completed.connect(self._completed);self.answer.partial.connect(self._answer_partial)
        self.short_voice=ShortVoice(self);self.short_voice.state.connect(self._short_voice_state)
        self.short_voice.failed.connect(self.status_error);self.short_voice.transcript.connect(self._short_transcript)
        from core.music_stop import MusicStopListener
        self.music_listener=MusicStopListener(self);self.music_listener.stop_requested.connect(self.runtime.music.stop)
        from core.proactive_conversation import ProactiveConversation
        self.proactive=ProactiveConversation(self,self.memory)
        self._run_kind=None;self._answer_text=''
        self._last_task_notice=0
        self._task_summary='正在规划第一步，尚未修改目标文件'
        self._task_started=0
        self._task_feedback_sequence=0
        self._dsh_last_notice=0
        self._task_reporter=None
        self._dsh_reports={}
        self._pending_execution=None
        self._active_workflow=None
        self._active_route=None
        self._active_task_text=''
        from core.task_speech import TaskSpeechQueue
        self._task_speech=TaskSpeechQueue()
        self._task_heartbeat_timer=QTimer(self)
        self._task_heartbeat_timer.setInterval(1000)
        self._task_heartbeat_timer.timeout.connect(self._task_heartbeat)
        self.logs_visible = False
        self._pending_image = None
        self._window_drag_pos = None
        from ui.chat_shell import build
        build(self)
        self.refresh_tree()
        saved = self.store.preference('agent/%s/active_conversation'%self.active_agent_id) or self.store.preference('active_conversation')
        ids = [c['id'] for c in self.store.snapshot(agent_id=self.active_agent_id)['conversations']]
        self.open_conversation(saved if saved in ids else (ids[0] if ids else self.store.create_conversation(agent_id=self.active_agent_id)))

    def _pin(self, enabled):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        self.show()

    def hide_to_pet(self):
        self.hide()
        self._show_active_bubble('我在听，可以继续说。' if self.voice_active else '点击这里，继续我们的对话。')

    def _show_active_bubble(self,text):
        manager=getattr(self.pet,'_multi_agent',None)
        if manager is not None:manager.say(self.active_agent_id,text)
        else:self.pet._show_agent_bubble(text,self.active_id)

    def _active_gesture(self,name):
        manager=getattr(self.pet,'_multi_agent',None)
        if manager is not None and self.active_agent_id!='001':manager.gesture(self.active_agent_id,name)
        else:self.pet._perform_gesture(name)

    def _active_expression(self,name):
        manager=getattr(self.pet,'_multi_agent',None)
        if manager is not None and self.active_agent_id!='001':manager.set_expression(self.active_agent_id,name)
        else:self.pet._anim.set_expression(name)

    def refresh_tree(self):
        self.tree.clear()
        snapshot = self.store.snapshot(self.show_archived,agent_id=self.active_agent_id)
        groups = {}
        root = QTreeWidgetItem(self.tree, ['独立对话' if not self.show_archived else '已归档'])
        groups[None] = root
        for p in snapshot['projects']:
            item = QTreeWidgetItem(self.tree, [p['name']])
            item.setData(0, Qt.UserRole, ('project', p['id']))
            groups[p['id']] = item
        for c in snapshot['conversations']:
            item = QTreeWidgetItem(groups.get(c['project_id'], root), ['◌  ' + c['name']])
            item.setToolTip(0, c['name'])
            item.setData(0, Qt.UserRole, ('conversation', c['id']))
            if c['id'] == self.active_id:
                self.tree.setCurrentItem(item)
        self.tree.expandAll()
        if self.active_id:
            self.title.setText(self.active_profile['display_name']+' · '+self.store.conversation(self.active_id)['name'])
            self.model_label.setText(self.active_profile['role']+' · '+self.active_profile['specialty'])

    def _selected(self, item, column=0):
        data = item.data(0, Qt.UserRole)
        if data and data[0] == 'conversation':
            if self.busy or self.voice_active:
                self.status.setText('请先结束本轮或语音通话，再切换会话')
                return
            self.open_conversation(data[1])

    def open_conversation(self, ident):
        if hasattr(self,'attachment_bar'):self._clear_attachment()
        self.active_id = ident
        self.store.preference('active_conversation', ident)
        self.store.preference('agent/%s/active_conversation'%self.active_agent_id,ident)
        self.refresh_tree()
        self.render()

    def render(self):
        from core.chat_presentation import is_progress_ack
        messages = self.store.messages(self.active_id)
        hidden=[m for m in messages if m['role'] in ('tool','system') or
                (m['role']=='assistant' and is_progress_ack(m['content']))]
        self.log_button.setText('执行记录' + (' · '+str(len(hidden)) if hidden else ''))
        self.log_button.setVisible(bool(hidden))
        visible=[m for m in messages if m not in hidden or self.logs_visible]
        if self._answer_text:visible.append(dict(id='draft',role='assistant',content=self._answer_text))
        if not visible:
            self.view.setHtml('<div style="margin-top:95px;text-align:center;color:#303a50">'
                '<p style="font-size:34px">🐧</p><p style="font-size:20px;font-weight:600">有什么想和咕嘎聊聊？</p>'
                '<p style="color:#9aa1ae;font-size:12px;line-height:1.9">问个问题，或者交给我一件小事。<br>也可以截图提问、随时开始语音。</p></div>')
            return
        parts=[]
        for m in visible:
            role=m['role'];is_user=role=='user';detail=m in hidden
            text=html.escape(m['content']).replace('\n','<br>')
            if detail:
                key=str(m['id'])
                summary=html.escape(m['content'].split('\n',1)[0])
                text=(summary+f'　<a href="guga-log:{key}">收起</a><br>'+text) if key in self.expanded_logs else (summary+f'　<a href="guga-log:{key}">展开</a>')
            color='#edf2ff' if is_user else ('#f6f7f9' if detail else '#ffffff')
            who='你' if is_user else ('执行记录' if detail else self.active_profile['display_name'])
            pad='12' if not detail else '8'
            content=f'<td bgcolor="{color}" style="padding:{pad}px;color:#303540"><span style="font-size:10px;color:#9aa1ad">{who}</span><br><p style="line-height:1.65;font-size:13px">{text}</p></td>'
            blank='<td width="30"></td>'
            parts.append('<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:12px"><tr>'+ (blank+content if is_user else content+blank)+'</tr></table>')
        self.view.setHtml(''.join(parts))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _toggle_logs(self,on):
        self.logs_visible=bool(on);self.render()

    def _toggle_sidebar(self):
        show=self.sidebar.isHidden();self.sidebar.setVisible(show)
        self.resize(max(720,self.width()+210) if show else max(480,self.width()-210),self.height())
        self.history_button.setChecked(show)

    def attach_screenshot(self,image_url):
        from core.chat_presentation import user_content
        user_content('截图',image_url)
        self._pending_image=image_url;self.attachment_bar.show()
        self.status.setText('截图已附加，输入问题后发送 · 仅本轮使用')

    def _clear_attachment(self):
        self._pending_image=None;self.attachment_bar.hide()

    def _screen_tool(self,mode):
        self.pet._screen_tools.begin(mode)

    def _shortcut_help(self):
        failed=getattr(self.pet._screen_tools,'hotkey_errors',[])
        QMessageBox.information(self,'快捷操作','在支持读取文字选区的软件里选中文字，鼠标旁出现「解释 / 截图分析」。\nCtrl+Alt+A 或「截图」：截取当前屏幕并直接发给模型。\nCtrl+Alt+E：直接解释当前选词。\n更多菜单保留框选截图和框选识字。\n临时解释不加入聊天记录，右下角可调整对话窗大小。'+('\n快捷键已被其他软件占用：'+', '.join(failed)+'；可使用窗口按钮。' if failed else ''))

    def _log_link(self, url):
        if url.scheme() == 'guga-log':
            key = url.path()
            if key in self.expanded_logs:
                self.expanded_logs.remove(key)
            else:
                self.expanded_logs.add(key)
            self.render()

    def open_permissions(self):
        from ui.app_permissions import AppPermissionsDialog
        AppPermissionsDialog(self.runtime.apps, self).exec_()

    def _project_selection(self):
        item = self.tree.currentItem()
        data = item.data(0, Qt.UserRole) if item else None
        if data:
            return data[1] if data[0] == 'project' else self.store.conversation(data[1])['project_id']
        return None

    def new_conversation(self):
        if self.busy or self.voice_active:
            return
        self.show_archived = False
        self.open_conversation(self.store.create_conversation(project_id=self._project_selection(),agent_id=self.active_agent_id))

    def activate_agent(self,agent_id):
        """Switch this shared workspace to one member's isolated conversations."""
        profile=self.memory.profile(agent_id)
        if not profile:raise ValueError('未知咕嘎编号')
        if str(agent_id)==self.active_agent_id:
            self.active_profile=profile
            return True
        if self.busy or self.voice_active:
            self.status.setText('请先结束当前任务或长语音，再切换另一位咕嘎')
            return False
        self.active_agent_id=profile['agent_id'];self.active_profile=profile
        self.store.preference('agent/active_id',self.active_agent_id)
        saved=self.store.preference('agent/%s/active_conversation'%self.active_agent_id)
        ids=[c['id'] for c in self.store.snapshot(agent_id=self.active_agent_id)['conversations']]
        ident=saved if saved in ids else (ids[0] if ids else self.store.create_conversation(agent_id=self.active_agent_id,name=profile['role']+'对话'))
        self.open_conversation(ident);self.status.setText('已切换到 '+profile['display_name']+' · '+profile['role'])
        return True

    def new_project(self):
        name, ok = QInputDialog.getText(self, '新建项目', '项目名称')
        if ok and name.strip():
            self._guard(lambda: self.store.create_project(name))

    def _guard(self, callback):
        try:
            callback()
            self.refresh_tree()
        except Exception as exc:
            self.status.setText(str(exc))

    def _rename_item(self, item, column=0):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        old_name = (self.store.conversation(data[1])['name'] if data[0] == 'conversation' else
                    next(p['name'] for p in self.store.snapshot()['projects'] if p['id'] == data[1]))
        name, ok = QInputDialog.getText(self, '更名', '新名称', text=old_name)
        if ok and name.strip():
            self._guard(lambda: self.store.rename(data[0], data[1], name))

    def rename_active(self):
        name, ok = QInputDialog.getText(self, '更名对话', '对话名称', text=self.store.conversation(self.active_id)['name'])
        if ok and name.strip():
            self._guard(lambda: self.store.rename('conversation', self.active_id, name))

    def _context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item or not item.data(0, Qt.UserRole):
            return
        kind, ident = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        menu.addAction('更名').triggered.connect(lambda: self._rename_item(item))
        if kind == 'conversation':
            move = menu.addMenu('移动到项目')
            move.addAction('独立对话').triggered.connect(lambda: self._guard(lambda: self.store.move(ident, None)))
            for p in self.store.snapshot()['projects']:
                move.addAction(p['name']).triggered.connect(lambda checked=False, pid=p['id']: self._guard(lambda: self.store.move(ident, pid)))
            menu.addAction('恢复' if self.show_archived else '归档').triggered.connect(
                lambda: self._guard(lambda: self.store.archive(ident, not self.show_archived)))
        menu.exec_(self.tree.viewport().mapToGlobal(position))

    def _toggle_archive(self):
        self.show_archived = not self.show_archived
        self.archive_button.setText('返回全部对话' if self.show_archived else '已归档对话')
        self.refresh_tree()

    def eventFilter(self, obj, event):
        if obj in (getattr(self,'_drag_header',None),getattr(self,'title',None)):
            if event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton:
                self._window_drag_pos=event.globalPos()-self.frameGeometry().topLeft();return True
            if event.type()==QEvent.MouseMove and self._window_drag_pos is not None and event.buttons() & Qt.LeftButton:
                self.move(event.globalPos()-self._window_drag_pos);return True
            if event.type()==QEvent.MouseButtonRelease:self._window_drag_pos=None
        if obj is getattr(self,'editor',None) and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Enter, Qt.Key_Return) and not event.modifiers() & Qt.ShiftModifier:
                self.send()
                return True
        return super().eventFilter(obj, event)

    def send(self, text=None, record_user=True, force_agent=False, execution_context=None, use_attachment=None,read_reply=False):
        text = text if isinstance(text, str) else self.editor.toPlainText().strip()
        image=self._pending_image if (record_user if use_attachment is None else use_attachment) else None
        if image:
            if self.busy:
                self.status.setText('当前任务执行中，请等待结束后发送截图；附件已保留')
                return False
            text=text.strip() or '请解释这张截图中的内容。'
            execution_context=dict(execution_context or {},_user_image_url=image)
        if not text.strip():
            return
        self._last_personal_answer=None
        # Explicit user memory/review controls are local, with no model/API round trip.
        if not image and not self.busy and self._personal_command(text,record_user):
            self.editor.clear();self.render();return True
        memory_context=''
        if record_user:
            recalled=self.memory.recall(self.active_agent_id,text,limit=6)
            memory_context='\n'.join('- ['+item['kind']+'] '+item['content'] for item in recalled)
            self.memory.add_dialogue(self.active_agent_id,'user',text)
        if record_user:
            self.pet._on_user_interaction()
            team=getattr(self.pet,'_multi_agent',None)
            if team:team.user_interaction(self.active_agent_id)
        from core.fast_commands import match
        fast=match(text)
        if fast.get('matched') and not force_agent and not image and self.route_combo.currentData()!='chat':
            if record_user:self.store.append(self.active_id,'user',text)
            self._fast_command(fast)
            self.editor.clear();self.render()
            return True
        if self.busy:
            if self._run_kind=='answer':
                self.status.setText('正在回答，可先点停止再提新问题');return False
            control=self._task_control.observe(text,busy=True)
            if control['action'] in ('ask_stop','pending'):
                if record_user:self.store.append(self.active_id,'user',text)
                question='当前任务仍在执行。确定要停止吗？请说“停止”确认，或说“继续”任务。'
                self.status.setText(question)
                self.store.append(self.active_id,'system',question)
                self._show_active_bubble(question)
                self.editor.clear();self.render();return True
            if control['action']=='cancel':
                self._cancel_execution();self.status.setText('已请求停止任务');self.editor.clear();return True
            if control['action']=='continue':
                self.status.setText('好，继续当前任务');self._show_active_bubble('好，继续当前任务。')
                self.editor.clear();return True
            self._steer_execution(text)
            if record_user:self.store.append(self.active_id,'user',text)
            self.editor.clear();self.status.setText('追加要求已排队；当前操作结束后处理');self.render()
            return True
        if len(text) > 16000:
            self.status.setText('单条消息最多 16000 字，请分段发送')
            return
        history = self.store.messages(self.active_id, 40)
        from core.chat_answer import uses_task,LEVELS
        from core.daily_workflows import workflow_for
        from core.task_router import choose_task_route
        from core.task_feedback import EvidenceProgress
        workflow=self.runtime.workflows.match(text) or workflow_for(text)
        task=force_agent or uses_task(text,self.route_combo.currentData(),bool(image)) or bool(workflow and self.route_combo.currentData()!='chat')
        from core.personal_harness import use_dsh
        backend=self.store.preference('execution_backend') or 'auto'
        route=choose_task_route(text,bool(image),backend,workflow) if task else None
        dsh=bool(route and route.get('executor')=='dsh')
        self._active_workflow=workflow if task else None
        self._active_route=route
        self._active_task_text=text if task else ''
        try:
            if not task:
                self.answer.start(self.active_id,history,text,image_url=image,effort=self.effort_combo.currentData(),read_aloud=read_reply,
                    profile=self.active_profile,memory_context=memory_context)
            else:
                handoff=dict(execution_context or {},_agent_id=self.active_agent_id,
                    _agent_profile=self.active_profile,_memory_context=memory_context,
                    _route=route,_workflow=workflow)
                if dsh:
                    self.agent.cancel_event.clear()
                    context=memory_context or '\n'.join(x['content'] for x in self.memory.recall(self.active_agent_id,text,limit=6))
                    accepted=self.runtime.dsh.start(text,self.active_id,profile=dict(self.active_profile),memory_context=context,workflow=workflow)
                    from core.task_brief import DshTaskBrief
                    job_id=accepted.get('job_id','')
                    self._dsh_reports[job_id]=dict(agent_id=self.active_agent_id,
                        conversation_id=self.active_id,reporter=EvidenceProgress(interval=60),
                        brief=DshTaskBrief(job_id,text))
                else:self.agent.start(self.active_id,history,text,handoff=handoff)
        except Exception as exc:
            self.status.setText(str(exc))
            self._active_workflow=None;self._active_route=None;self._active_task_text=''
            return False
        self.busy = True
        self._run_kind='dsh' if dsh else 'task' if task else 'answer';self._answer_text=''
        self.pet._multi_agent.work_state(self.active_agent_id,'thinking','turn:'+self.active_id)
        if image:self._clear_attachment()
        self.pet._tts.stop()
        if record_user:
            self.store.append(self.active_id, 'user', ('[本轮截图]\n' if image else '')+text)
        if self.store.conversation(self.active_id)['name'] == '新对话':
            self.store.rename('conversation', self.active_id, text.replace('\n', ' ')[:24])
        self.editor.clear()
        self.send_button.setEnabled(True)
        self.new_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText('DSH 正在执行 · 可查看执行记录' if dsh else '正在执行任务…' if task else ('正在回答…' if self.effort_combo.currentData()=='quick' else '思考中 · '+LEVELS[self.effort_combo.currentData()][0]))
        self._task_started=time.monotonic()
        self._last_task_notice=self._task_started
        self._task_summary='正在规划第一步，尚未修改目标文件'
        if task:
            self._task_reporter=None if dsh else EvidenceProgress()
            self._task_heartbeat_timer.start()
        self.refresh_tree()
        self.render()
        return True

    def _cancel_execution(self):
        self._task_speech.clear()
        if self.voice and hasattr(self.voice,'clear_task_notices'):self.voice.clear_task_notices()
        self.agent.cancel()
        if self._run_kind=='dsh' or self.runtime.dsh.state.get('running'):self.runtime.dsh.stop()

    def _steer_execution(self,text):
        if self._run_kind=='dsh':
            self.runtime.dsh.archive.add_input(self.runtime.dsh.state['ticket_id'],text)
        else:self.agent.steer(text)

    def _personal_command(self,text,record_user):
        from core.agent_memory import safe_text
        owner=self.active_agent_id;archive=self.runtime.dsh.archive
        answer=None
        if text.startswith(('记住：','记住:','请记住：','请记住:')):
            content=text.replace('：',':').split(':',1)[1].strip()
            if not content or safe_text(content)!=content:
                answer='没有保存：记忆不能为空，也不能包含凭据或超长文本。'
            else:
                self.memory.remember(owner,content,kind='user_preference',importance=4)
                answer='已记住，可以跨对话使用。你可以说“查看我的记忆”或“忘记记忆 编号”。'
        elif text.strip() in ('查看我的记忆','查看长期记忆'):
            facts=self.memory.facts(owner)
            answer='\n'.join(str(x['id'])+'：'+x['content'] for x in facts) or '还没有保存的长期记忆。'
        elif text.startswith('忘记记忆 '):
            try:answer='已删除。' if self.memory.forget(owner,text.split()[-1])['deleted'] else '没有找到该条记忆。'
            except ValueError:answer='请使用“忘记记忆 编号”。'
        elif text.strip() in ('查看长任务','查看任务进度'):
            rows=archive.recent(owner,limit=5)
            answer='\n'.join(x['id'][:8]+' · '+x['state']+' · '+x['summary'][:160] for x in rows) or '暂无长任务。'
        elif text.strip() in ('确认上个任务完成','上个任务验收失败'):
            rows=archive.recent(owner,self.active_id,1)
            if not rows:answer='当前对话没有可验收的长任务。'
            else:
                archive.review(owner,rows[0]['id'],text=='确认上个任务完成')
                answer='已记录你的验收结果；有真实工具回执的成功任务，其经验候选才会启用。'
        elif text.strip()=='查看学习经验':
            answer='\n'.join(x['id']+' · '+x['state']+' · '+x['recipe'] for x in archive.lessons(owner)) or '暂无经验候选。'
        elif text.startswith('停用经验 '):
            try:archive.disable_lesson(owner,text.split()[-1]);answer='经验已停用。'
            except ValueError as exc:answer=str(exc)
        elif text.startswith(('定义工作流：','定义工作流:')):
            content=text.replace('：',':',1).split(':',1)[1]
            parts=content.replace('｜','|').split('|',1)
            try:
                flow=self.runtime.workflows.define(parts[0],parts[1] if len(parts)>1 else '')
                answer='已保存工作流“'+flow['title']+'”，以后说“'+flow['trigger']+'”即可调用。'
            except (ValueError,IndexError) as exc:answer='没有保存工作流：'+str(exc)
        elif text.strip() in ('查看工作流','查看已学工作流'):
            rows=self.runtime.workflows.list()
            answer='\n'.join(x['id']+' · '+x['title']+' · 触发：'+x['trigger'] for x in rows) or '暂无工作流。'
        elif text.startswith('停用工作流 '):
            try:self.runtime.workflows.disable(text.split()[-1]);answer='工作流已停用。'
            except ValueError as exc:answer=str(exc)
        elif text.startswith('启用工作流 '):
            try:
                flow=self.runtime.workflows.activate(text.split()[-1])
                answer='工作流“'+flow['title']+'”已启用。'
            except ValueError as exc:answer=str(exc)
        elif text.strip() in ('切换到旧执行器','切换到本地智能体','切换到DSH执行器','切换到自动执行器'):
            backend={'切换到旧执行器':'self','切换到本地智能体':'self','切换到DSH执行器':'dsh',
                     '切换到自动执行器':'auto'}[text.strip()]
            self.store.preference('execution_backend',backend)
            label={'self':'本地视觉智能体','dsh':'DSH 执行器','auto':'自动路由（日常本地，代码工程 DSH）'}[backend]
            answer='后续执行任务使用 '+label+'；简单命令快通道保持。'
        if answer is None:return False
        self._last_personal_answer=answer
        if record_user:self.store.append(self.active_id,'user',text)
        self.store.append(self.active_id,'assistant',answer);self.status.setText(answer[:100])
        self._show_active_bubble(answer)
        return True

    def _answer_partial(self,ident,generation,text):
        if self._run_kind=='answer' and self.busy and ident==self.active_id and generation==self.answer.generation:
            if text.strip() and not self._answer_text:
                self.pet._multi_agent.work_state(self.active_agent_id,'idle','turn:'+ident)
            self._answer_text=text;self.render()

    def status_error(self,text):
        self.status.setText(text);self._show_active_bubble(text)

    def _short_voice_state(self,state):
        labels={'preparing':'准备中','recording':'说完点此','transcribing':'识别中','idle':'说话'}
        self.short_voice_button.setText(labels[state]);self.short_voice_button.setEnabled(state in ('idle','recording'))
        if state=='idle':return
        self.status.setText({'preparing':'正在准备离线语音识别…','recording':'正在录音 · 说完再点一次，最长30秒','transcribing':'正在识别…','idle':'就绪'}[state])

    def toggle_short_voice(self):
        if self.short_voice.active:self.short_voice.stop();return
        if self.voice_active or self.pet._realtime_active():self.status_error('请先结束实时通话，再使用短语音');return
        if self.busy:self.status_error('请先等待当前回答完成，或点停止');return
        self.pet._tts.stop();self.short_voice.start()

    def _short_transcript(self,text):
        self.editor.setPlainText(text)
        self.send(text,read_reply=True)

    def _activity(self, ident, event):
        if ident==self.active_id and self._task_reporter:
            self._task_reporter.tool_result(event['name'],event.get('arguments',{}),event['result'])
        if ident==self.active_id and self._run_kind=='task':
            self.pet._multi_agent.work_state(self.active_agent_id,'thinking','turn:'+ident)
        result = event['result']
        label = {'app_state': '查看自身状态', 'pet_control': '操纵自身', 'workspace': '管理项目与会话',
                 'self_ask': '自己询问自己', 'ecosystem': '调用生态软件', 'applications': '授权软件操作'}.get(event['name'], event['name'])
        label += ' · ' + ('完成' if result.get('ok') else '未完成')
        text = label + '\n' + json.dumps(event['arguments'], ensure_ascii=False)
        text += '\n' + ('完成：' if result.get('ok') else '失败：') + json.dumps(result, ensure_ascii=False)[:6000]
        self.store.append(ident, 'tool', text)
        if ident == self.active_id:
            self.status.setText(label + (' · 已完成' if result.get('ok') else ' · 未完成'))
            self.render()

    def _task_progress(self,ident,event):
        if ident!=self.active_id:return
        state='working' if event.get('phase')=='tool_start' else 'thinking'
        if self._run_kind=='task':self.pet._multi_agent.work_state(self.active_agent_id,state,'turn:'+ident)
        if self._task_reporter and event.get('phase')=='tool_start' and event.get('name'):
            self._task_reporter.tool_start(event['name'],event.get('arguments',{}))
        summary=event.get('summary','正在执行任务')
        self._task_summary=summary
        self.status.setText(summary[:120])
        # Detailed task/model events stay inside the expandable execution log.

    def _task_heartbeat(self):
        self._flush_task_speech()
        reports=[]
        if self.busy and self._task_reporter:
            reports.append((self.active_agent_id,self.active_id,'task',self._task_reporter,None))
        reports.extend((item['agent_id'],item['conversation_id'],job,item['reporter'],item.get('brief'))
            for job,item in self._dsh_reports.items())
        if not reports:
            if not self._task_speech.pending:self._task_heartbeat_timer.stop()
            return
        for agent_id,ident,key,reporter,brief in reports:
            if self.voice_active and self.voice and self.voice.is_speaking:continue
            summary=brief.watchdog() if brief is not None else reporter.notice()
            if not summary:continue
            self.pet._multi_agent.say(agent_id,summary)
            if self.voice_active and self.voice and ident==self.active_id and agent_id==self.active_agent_id:
                beat=brief.beat if brief is not None else reporter.beat
                self.voice.notify_battleground('progress-'+key+'-'+str(beat),summary,during_task=True)
            elif not self.voice_active:
                beat=brief.beat if brief is not None else reporter.beat
                self._queue_task_speech('heartbeat-'+key+'-'+str(beat),summary)

    def _queue_task_speech(self,key,text,important=False):
        if self._task_speech.offer(key,text,important):
            self._task_heartbeat_timer.start()
            self._flush_task_speech()

    def _flush_task_speech(self):
        speaking=bool(self.voice and self.voice.is_speaking) if self.voice_active else bool(self.pet._tts.is_speaking)
        # A real-time queue already waits for a break in the user's speech.
        item=self._task_speech.next(busy=speaking)
        if not item:return
        self._show_active_bubble(item['text'])
        if self.voice_active and self.voice:
            self.voice.notify_battleground('task-'+item['key'],item['text'],during_task=True,persistent=item['important'])
        else:
            from core.agent_identity import voice_preferences
            self.pet._tts.speak(item['text'],voice_profile=voice_preferences(self.active_profile))

    def _battleground_event(self, event):
        ident = event.get('conversation_id')
        if not ident:
            return
        speech = event.get('speech')
        if speech:
            self.store.append(ident, 'system', speech)
            if self.active_id == ident:
                self.status.setText(speech[:100])
                self.render()
                if self.voice_active and self.voice:
                    self.voice.notify_battleground(event['event_id'], speech)
        elif event['type'] in ('action_confirmed','unconfirmed','input_sent','memory_snapshot'):
            self.store.append(ident, 'tool', '战棋后台 · '+json.dumps(event,ensure_ascii=False)[:6000])
            if self.active_id == ident:
                self.render()

    def _dsh_event(self,event):
        ident=event.get('conversation_id')
        if not ident:return
        if event.get('type')=='dsh_event':
            item=event.get('event') or {};phase=item.get('phase')
            from core.task_feedback import EvidenceProgress
            from core.task_brief import DshTaskBrief
            job=event.get('job_id','')
            if job not in self._dsh_reports:
                self._dsh_reports[job]=dict(agent_id=event.get('agent_id') or self.active_agent_id,
                    conversation_id=ident,reporter=EvidenceProgress(interval=60),
                    brief=DshTaskBrief(job,self.runtime.dsh.state.get('goal','')))
            reporter=self._dsh_reports[job]['reporter']
            brief=self._dsh_reports[job].get('brief')
            if brief is not None:brief.observe(item)
            if item.get('type')=='status' and phase in ('turn_start','step_start','step_end'):
                self.pet._multi_agent.work_state(self._dsh_reports[job]['agent_id'],
                    'working' if phase=='step_start' else 'thinking','dsh:'+job)
            if item.get('type')=='status' and phase in ('turn_start','step_start'):
                reporter.pending='执行器已进入新步骤，等待具体操作回执'
            receipt=item.get('receipt')
            if isinstance(receipt,dict):reporter.tool_result(receipt['name'],receipt.get('arguments',{}),receipt['result'])
            if item.get('type') in ('tool_receipt','report'):
                self.store.append(ident,'tool','DSH · '+json.dumps(item,ensure_ascii=False)[:2000])
            if item.get('type')=='report' and ident==self.active_id and item.get('state')!='ready':
                summary=item.get('summary','')
                important=item.get('state') in ('needs_input','blocked','failed','ready') or item.get('milestone',False)
                self.status.setText(summary[:120])
                self._queue_task_speech(job+'-'+item.get('state','')+'-'+summary[:80],summary,important)
            elif item.get('type')=='status' and phase=='turn_start' and ident==self.active_id:
                self._queue_task_speech(job+'-start','DSH 已开始处理，我会在重要步骤完成或遇到困难时告诉你。')
            self._task_heartbeat_timer.start()
            if self.active_id==ident and item.get('type')!='report':self.status.setText('DSH 后台任务执行中…')
            return
        if event.get('type')=='dsh_completed':
            job_id=event.get('job_id','')
            report=self._dsh_reports.pop(job_id,None)
            if report and report.get('brief') is not None:
                report['brief'].complete(bool(event.get('ok')),self.runtime.dsh.status().get('ticket_state',''),
                    event.get('text') or event.get('error') or '')
            agent_id=event.get('agent_id') or (report or {}).get('agent_id') or self.active_agent_id
            ok=bool(event.get('ok'));text=event.get('text') or event.get('error') or 'DSH 任务未返回结果'
            self.pet._multi_agent.work_state(agent_id,'idle' if event.get('needs_input') else 'result' if ok else 'error',
                                             'dsh:'+event.get('job_id',''))
            if ok:text='执行器返回（尚未独立核验）：\n'+text
            if self._run_kind=='dsh' and ident==self.active_id:
                self._task_speech.clear()
                self._completed(dict(kind='dsh',conversation_id=ident,ok=ok,text=text,error=event.get('error',''),
                                     expression='normal',expression_explicit=False,action='none',needs_input=event.get('needs_input',False)))
                if not self.voice_active:
                    self._queue_task_speech(event['job_id']+'-done',
                        (event.get('error') if not ok else 'DSH 已交付执行结果，请核对；'+event.get('text','')[:180]),True)
                ticket=self.runtime.dsh.state['ticket_id']
                pending=self.runtime.dsh.archive.pending_inputs(ticket) if not self.runtime.dsh.stop_event.is_set() else []
                if pending:
                    def followup():
                        if self.busy:return
                        if self.send('继续处理用户保存的追加要求：\n'+'\n'.join(x['content'] for x in pending),record_user=False,force_agent=True):
                            self.runtime.dsh.archive.acknowledge_inputs(ticket,[x['id'] for x in pending])
                    QTimer.singleShot(150,followup)
                return
            self.store.append(ident,'assistant' if ok else 'system',text)
            self.memory.add_dialogue(agent_id,'assistant',text)
            self.pet._multi_agent.work_state(agent_id,'result' if ok else 'error','dsh:'+event.get('job_id',''))
            if self.active_id==ident:
                self.status.setText('后台执行器已返回 · 待核验' if ok else 'DSH 任务未完成')
                self._show_active_bubble(text);self.render()
            if self.voice_active and self.voice and ident==self.active_id and agent_id==self.active_agent_id:
                self.voice.notify_battleground(event.get('job_id','')+'done',text[:800])

    def _music_event(self,event):
        playing=event.get('type')=='playing'
        self.music_listener.enable(bool(playing and self.voice_active))
        self.stop_button.setEnabled(bool(self.busy or self.runtime.music.requested or self.runtime.music.preparing))
        # Music owns both layers; switching appearance must not lose the stage.
        team=getattr(self.pet,'_multi_agent',None)
        companion=team.companions.get(self.active_agent_id) if team else None
        animators=[companion.anim] if companion and playing else [self.pet._anim2d,self.pet._anim3d]
        if not playing and team:animators.extend(c.anim for c in team.companions.values())
        for animator in animators:
            if animator is None: continue
            if event.get('type')=='preparing':continue
            if hasattr(animator,'set_music_stage'):animator.set_music_stage(playing)
            else:
                animator.dancing=playing
                if playing:
                    animator.sunglasses=True;animator._sunglasses_until=0
                else:animator.sunglasses=False
        if playing: self.pet._tts.stop()
        if self.voice:
            self.voice._external_playing=playing
            if playing:self.voice.interrupt()
            else:self.voice._echo_until=time.monotonic()+.5
        text=('正在'+('循环' if self.runtime.music.repeat else '')+'播放咕嘎翻唱《'+event.get('title','')+'》 · 说“停止唱歌”或右键停止') if playing else (
            '正在制作咕嘎音色翻唱，完成后再播放' if event.get('type')=='preparing' else (
            '歌曲播放失败：'+event.get('error','') if event.get('type')=='error' else '已停止唱歌，可以继续聊天'))
        if event.get('type')=='interaction':
            text='唱到这里歇一会儿～想聊什么？说“继续唱歌”我再接着唱。'
            if self.voice_active and self.voice:
                self.voice.notify_battleground('music-interaction-'+str(time.monotonic()),text)
        self.status.setText(text);self._show_active_bubble(text);self.pet.update()

    def _completed(self, result):
        if result.get('kind')=='answer' and result.get('generation',self.answer.generation)!=self.answer.generation:return
        ident = result['conversation_id']
        was_task=self._run_kind in ('task','dsh')
        self.busy = False
        self._answer_text='';self._run_kind=None
        workflow=self._active_workflow;self._active_workflow=None
        route=self._active_route;self._active_route=None
        task_text=self._active_task_text;self._active_task_text=''
        if workflow and result.get('kind')!='answer' and not result.get('needs_input'):
            try:self.runtime.workflows.record_run(workflow,bool(result.get('ok')),
                result.get('text') or result.get('error') or '')
            except Exception:pass
        if (task_text and result.get('ok') and result.get('kind')!='answer' and
                (route or {}).get('executor')=='local_agent'):
            try:
                events=self.agent.journal.data.get('events',[])
                learned=self.runtime.workflows.observe_local_success(task_text,
                    result.get('text',''),[x.get('tool') for x in events if x.get('ok')])
                if learned:self.store.append(ident,'system','已形成工作流候选：“'+learned['title']+'”。说“启用工作流 '+learned['id']+'”才会用于下次。')
            except Exception:pass
        self._task_control.reset()
        self._task_reporter=None
        if not self._dsh_reports:self._task_heartbeat_timer.stop()
        self.send_button.setEnabled(True)
        self.new_button.setEnabled(not self.voice_active)
        self.stop_button.setEnabled(bool(self.runtime.music.requested))
        if result.get('kind')!='answer' and self.agent.cancel_event.is_set():
            result = dict(result, ok=False, error='本轮已停止，已完成的操作保留在记录中')
        if was_task:
            state='idle' if result.get('needs_input') else 'cancelled' if self.agent.cancel_event.is_set() else 'success' if result.get('ok') else 'error'
            self.pet._multi_agent.work_state(self.active_agent_id,state,'turn:'+ident)
        else:self.pet._multi_agent.work_state(self.active_agent_id,'idle','turn:'+ident)
        voice_task = self._voice_task
        self._voice_task = None
        if voice_task and self.voice_active and voice_task[0] is self.voice:
            self._voice_readback = True
            self.voice.resolve_task(voice_task[1], dict(ok=result['ok'],
                answer=result.get('text', '') if result['ok'] else '', error=result.get('error', '')))
        elif self.voice_active and self.voice:
            self._voice_readback=True
            self.voice.notify_battleground('task-finished-'+result.get('task_id',ident),
                '任务执行结果：'+(result.get('text','') if result['ok'] else result.get('error','未完成')))
        if result['ok']:
            self.store.append(ident, 'assistant', result['text'])
            self.memory.add_dialogue(self.active_agent_id,'assistant',result['text'])
            if not self.voice_active and not self.isVisible():
                self._show_active_bubble(result['text'])
            self.status.setText('●  就绪')
            expression = result['expression'] if result.get('expression_explicit',result['expression']!='normal') else None
            if (self.read_aloud.isChecked() or result.get('read_aloud')) and not self.voice_active and result.get('kind')!='dsh':
                if self.active_agent_id=='001':self.pet._say_with_expression(result['text'], expression, result['action'])
                else:
                    self._show_active_bubble(result['text'])
                    from core.agent_identity import voice_preferences
                    self.pet._tts.speak(result['text'],voice_profile=voice_preferences(self.active_profile))
                    self._active_expression(expression or 'happy')
                    if result['action']!='none':self._active_gesture(result['action'])
            else:
                if expression is not None:self._active_expression(expression)
                if result['action'] != 'none':
                    if self.voice_active:self.pet._queue_voice_gesture(result['action'])
                    else:self._active_gesture(result['action'])
        else:
            self.store.append(ident, 'assistant', ('等待你的决定：' if result.get('needs_input') else '这次没有完成：')+result.get('error', '本轮未完成'))
            self.memory.add_dialogue(self.active_agent_id,'assistant','这次没有完成：'+result.get('error','本轮未完成'))
            self.status.setText(result.get('error', '本轮未完成'))
        if self.active_id == ident:
            self.render()

    def stop_turn(self):
        self._task_speech.clear()
        self.runtime.media.stop()
        self.pet._multi_agent.work_state(self.active_agent_id,'cancelled','turn:'+self.active_id)
        self.answer.cancel();self.short_voice.stop(cancel=True);self.runtime.music.stop()
        self.runtime.battleground.stop()
        self.runtime.dsh.stop()
        self.agent.cancel()
        self.pet._tts.stop()
        if self.voice_active:
            self.voice_button.setChecked(False)
        self.status.setText('已请求停止 · 不再执行后续操作')
        self.stop_button.setEnabled(self.busy)

    def toggle_voice(self, enabled):
        self.voice_controls.setVisible(bool(enabled))
        if enabled:
            if self.short_voice.active:
                self.status_error('请先结束短语音录音，再开启实时通话');self.voice_button.setChecked(False);return
            if self.pet._realtime_active() or getattr(self.pet, '_recording', False):
                self.status.setText('请先结束原语音通话，再开启智能体语音')
                self.voice_button.setChecked(False)
                return
            if self.voice and self.voice.active:
                self.status.setText('上一段语音正在释放麦克风，请稍后重试')
                self.voice_button.setChecked(False)
                return
            self.pet._voice_mode = False
            from core.realtime_voice import load_settings, connection_url
            from ui.voice_dialog import VoiceSettingsDialog
            cfg = load_settings()
            try:
                connection_url(cfg)
            except ValueError:
                if VoiceSettingsDialog(self).exec_() != QDialog.Accepted:
                    self.voice_button.setChecked(False)
                    return
                cfg = load_settings()
            self.voice_active = True
            self._voice_readback = False
            if self.voice:
                self.voice.deleteLater()
            self.voice = AgentRealtimeVoice(self)
            self.voice._music_stop_listener=self.music_listener
            self.voice.agent_profile=dict(self.active_profile)
            self.voice.long_term_memory=[x['content'] for x in self.memory.recall(self.active_agent_id,'',limit=6)]
            self.voice.context_history = [('饲养员' if m['role'] == 'user' else '咕嘎', m['content'])
                for m in self.store.messages(self.active_id, 40) if m['role'] in ('user', 'assistant')]
            self.voice.task_requested.connect(self._voice_request)
            self.voice.tasks_cancelled.connect(self._voice_cancelled)
            self.voice.transcript.connect(self._voice_transcript)
            self.voice.preview.connect(self._voice_preview)
            self.voice.gesture.connect(lambda name:self._active_gesture(name) if self.active_agent_id!='001' else self.pet._queue_voice_gesture(name))
            self.voice.expression_requested.connect(lambda name:self._active_expression(name))
            self.voice.fast_command.connect(self._fast_command)
            self.voice.status.connect(self.status.setText)
            self.voice.failed.connect(self._voice_error)
            self.voice.finished.connect(self._voice_finished)
            self.voice.speaking.connect(self.pet._on_realtime_speaking)
            if self.pet._voice_call_panel:
                self.pet._voice_call_panel.close()
                self.pet._voice_call_panel.deleteLater()
                self.pet._voice_call_panel = None
            self.pet._realtime_voice = self.voice
            self.pet._tts.stop()
            self.new_button.setEnabled(False)
            self.voice_settings_button.setEnabled(False)
            self.mute_button.setEnabled(True)
            self.interrupt_button.setEnabled(True)
            self.read_aloud.setEnabled(False)
            self.voice_button.setText('结束通话')
            self.pet._agent_bubble.set_call_active(True,self.active_id)
            self.voice.start(cfg)
            self.proactive.start()
            self.voice._external_playing=self.runtime.music.playing
            self.music_listener.enable(bool(self.runtime.music.playing))
        else:
            self.proactive.stop()
            self.music_listener.enable(False)
            self.voice_active = False
            self.pet._pending_voice_gesture=None
            self.agent.cancel()
            self.runtime.battleground.stop()
            self.runtime.dsh.stop()
            if self.voice:
                self.voice.stop()
            self._voice_cancelled()
            self.new_button.setEnabled(not self.busy)
            self.voice_settings_button.setEnabled(True)
            self.mute_button.setEnabled(False)
            self.mute_button.setChecked(False)
            self.interrupt_button.setEnabled(False)
            self.read_aloud.setEnabled(True)
            self.voice_button.setText('通话')
            self.pet._agent_bubble.set_call_active(False,self.active_id)

    def _voice_error(self, message):
        self.voice_button.setChecked(False)
        self.status.setText(message)
        self._show_active_bubble(message)

    def open_voice_settings(self):
        from ui.voice_dialog import VoiceSettingsDialog
        VoiceSettingsDialog(self).exec_()

    def _voice_request(self, call_id, request):
        if not self.voice_active or self.sender() is not self.voice:
            return
        confirmed=False
        from core.fast_commands import match
        fast=match(request)
        if fast.get('matched') and not self._pending_image:
            self.voice._dispatch_fast(fast,call_id)
            return
        if not self.busy and self._pending_execution:
            pending=self._pending_execution
            if time.monotonic()-pending['created']>180:
                self._pending_execution=None
            else:
                value=str(request or '').strip().strip('。！! ')
                if value in ('确认执行','确认','对','是的','好的','开始','执行','可以'):
                    self._pending_execution=None
                    request=pending['text']
                    confirmed=True
                    self.store.append(self.active_id,'system','已确认任务理解，交给 DSH 执行。')
                elif value in ('取消','不对','先不要','算了','不执行'):
                    self._pending_execution=None
                    answer='已取消，不会启动这个复杂任务。'
                    self.store.append(self.active_id,'system',answer)
                    self._show_active_bubble(answer)
                    self.voice.resolve_task(call_id,dict(ok=True,answer=answer))
                    return
        if self.busy:
            control=self._task_control.observe(request,busy=True)
            if control['action'] in ('ask_stop','pending'):
                answer='当前任务仍在执行。确定要停止吗？请说“停止”确认，或说“继续”任务。'
                self.status.setText(answer)
                self.store.append(self.active_id,'system',answer)
                self._show_active_bubble(answer)
                self.voice.resolve_task(call_id,dict(ok=True,answer=answer))
            elif control['action']=='cancel':
                self._cancel_execution()
                answer='已确认停止当前任务；已经完成的操作保留在记录中。'
                self.store.append(self.active_id,'system',answer)
                self.voice.resolve_task(call_id,dict(ok=True,answer=answer))
            elif control['action']=='continue':
                answer='好，继续当前任务。'
                self.status.setText(answer)
                self.store.append(self.active_id,'system',answer)
                self.voice.resolve_task(call_id,dict(ok=True,answer=answer))
            elif (answer:=self._running_task_answer(request)):
                self.status.setText(answer[:120])
                self.store.append(self.active_id,'system',answer)
                self._show_active_bubble(answer)
                self.voice.resolve_task(call_id,dict(ok=True,answer=answer))
            else:
                self._steer_execution(request)
                self.voice.resolve_task(call_id,dict(ok=True,
                    answer='收到，这句会作为当前任务的补充要求保留；我没有把它当成新任务，也不会中断正在执行的步骤。'))
            return
        if not self._pending_image and not confirmed:
            from core.chat_answer import uses_task
            if uses_task(request,self.route_combo.currentData(),False):
                from core.daily_workflows import workflow_for
                from core.task_router import choose_task_route
                from core.task_feedback import acknowledgement
                workflow=self.runtime.workflows.match(request) or workflow_for(request)
                route=choose_task_route(request,False,self.store.preference('execution_backend') or 'auto',workflow)
                owner='DSH 深度执行器' if route.get('executor')=='dsh' else '咕嘎本地视觉智能体'
                understanding=acknowledgement(request).replace('我现在开始处理。','')
                question=(understanding+'这是一个复杂任务。我理解的是：由'+owner+'负责执行，我负责盯进度、核对回执并随时回答你。'
                    '确认无误请说“确认执行”；不执行请说“取消”。')
                self._pending_execution=dict(text=request,created=time.monotonic())
                self.status.setText('等待确认任务理解')
                self.store.append(self.active_id,'system',question)
                self._show_active_bubble(question)
                self.voice.resolve_task(call_id,dict(ok=True,answer=question,execution_pending=False))
                return
        self._voice_task = (self.voice, call_id)
        self._task_control.reset()
        messages = self.store.messages(self.active_id, 1)
        record_user = not (messages and messages[-1]['role'] == 'user')
        if not self.send(request, record_user=record_user,use_attachment=True,force_agent=not bool(self._pending_image)):
            self._voice_task = None
            self.voice.resolve_task(call_id, dict(ok=False, error=self.status.text()))
        else:
            if not self.busy and getattr(self,'_last_personal_answer',None):
                self._voice_task=None;self._voice_readback=True
                self.voice.resolve_task(call_id,dict(ok=True,answer=self._last_personal_answer))
                return
            from core.task_feedback import acknowledgement
            ack=acknowledgement(request)
            # Close the speech FC immediately; execution remains in the worker.
            # Otherwise pending _tasks blocks every queued progress narration.
            self._voice_task=None
            self._voice_readback=True
            self.voice.resolve_task(call_id,dict(ok=True,answer=ack,execution_pending=True))
            self._show_active_bubble(ack)
            self.store.append(self.active_id,'system','任务已接收：'+ack)

    def _running_task_answer(self,question):
        """DSH events are the source of truth for immediate spoken status."""
        for item in self._dsh_reports.values():
            if item.get('conversation_id')!=self.active_id:continue
            brief=item.get('brief')
            if brief is not None:
                answer=brief.answer(question)
                if answer:return answer
        if self._run_kind=='dsh':
            value=str(question or '')
            if any(term in value for term in ('进度','进展','做到哪','怎么样了','在做什么','为什么','卡住','失败','下一步','汇报')):
                state=self.runtime.dsh.status()
                elapsed=state.get('started')
                summary=state.get('summary') or 'DSH 尚未返回可核验的新进展'
                return ('当前任务仍在执行。最新记录是：'+summary+
                    '。目前没有新的明确错误原因，我不会把等待说成完成。')
        return None

    def _voice_cancelled(self):
        if self._voice_task:
            self._voice_task = None
            # Speech interruption stops old playback, not a still-authorized task.
            # Explicit stop/end-call/shutdown still cancels the worker.
            if not self.voice_active:self.agent.cancel()

    def _voice_preview(self, text):
        if self.voice_active and self.sender() is self.voice:
            from core.embodiment import spoken_reply
            self._show_active_bubble(spoken_reply(text)[2])

    def _fast_command(self, command):
        if command.get('fast_id') and not self.voice_active:return
        if command.get('action')=='stop_call':
            self.status.setText('已停止实时语音对话')
            self.voice_button.setChecked(False)
            self._last_fast_result=dict(ok=True,message='已停止实时语音对话')
            return self._last_fast_result
        from core.fast_commands import execute
        if command.get('action')=='multiagent':
            manager=getattr(self.pet,'_multi_agent',None)
            if manager is None:result=dict(ok=False,error='多智能体管理器不可用')
            elif command.get('kind')=='summon':result=dict(ok=True,message='已召唤成员 '+command.get('target',''))
            elif command.get('kind')=='summon_pair':result=dict(ok=True,message='已召唤大肥鱼和 Dora')
            elif command.get('kind')=='discuss':result=dict(ok=manager.start_discussion(tuple(command.get('target','').split(',')),command.get('topic','')),message='团队讨论已开始')
            else:result=dict(ok=manager.start_discussion(tuple(command.get('target','').split(',')),None),message='等待讨论话题')
            if result.get('ok'):
                if command.get('kind')=='summon':manager.summon(command['target'])
                elif command.get('kind')=='summon_pair':manager.summon_pair()
        elif command.get('action')=='performance':
            if command.get('kind')=='gesture':
                self._active_gesture(command['target']);result=dict(ok=True)
            else:
                result=self.runtime.execute('music_performance',dict(action={'music_stop':'stop','music_pause':'pause','music_resume':'resume'}.get(command.get('kind'),'sing'),title=command.get('target','')))
        else:result=execute(command)
        if result.get('ok') and command.get('report_query') and not self.busy:
            query=command['report_query']
            result.update(execution_pending=True,message='已请求默认浏览器搜索，正在核对网页摘要后汇报')
            QTimer.singleShot(0,lambda:self.send('请用web_search搜索：'+query+'。根据工具返回的摘要简短汇报，并附来源链接。浏览器已提交打开请求，无需再次打开。',record_user=False))
        if not result.get('ok') and command.get('action') in ('open_app','open_url','web_search') and command.get('source_text') and not result.get('launch_requested'):
            original=command['source_text'];failure=dict(result)
            result=dict(ok=True,execution_pending=True,delegated=True,silent=True,message='快捷路径未完成，已交给内部 DeepSeek 继续处理')
            context=dict(origin='voice' if self.voice_active else 'text',fast_failure=failure,original_request=original)
            QTimer.singleShot(0,lambda:self.send(original,record_user=False,force_agent=True,execution_context=context))
        self._last_fast_result=result
        # Last-command numeric timings only. No transcript, URL, key or audio.
        try:
            path=FAST_DIAGNOSTICS_PATH
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(dict(updated=time.time(),ok=bool(result.get('ok')),
                asr_after_speech_ms=command.get('asr_after_speech_ms'),dispatch_ms=result.get('dispatch_ms'),
                transcript_to_result_ms=result.get('transcript_to_result_ms')),indent=2),encoding='utf-8')
        except OSError:pass
        if command.get('fast_id'):
            self.voice.complete_fast_command(command['fast_id'],result)
        if result.get('ok'):
            self.status.setText(result.get('message','快速命令已完成'))
            return result
        message='快速命令执行失败：'+str(result.get('error','未知原因'))
        self.status.setText(message)
        self._show_active_bubble(message)
        return result

    def _voice_transcript(self, who, text):
        if not self.voice_active or self.sender() is not self.voice:
            return
        self.pet._on_user_transcript(who, text)
        if who=='饲养员':
            team=getattr(self.pet,'_multi_agent',None)
            if team:team.user_interaction(self.active_agent_id)
        if who=='饲养员' and self.busy and self._task_control.pending:
            control=self._task_control.observe(text,busy=True)
            if control['action'] in ('cancel','continue','ask_stop','pending'):
                self._finish_voice_task_confirmation(text,control['action'])
                return
        if who!='饲养员':
            from core.embodiment import spoken_reply
            _,_,text=spoken_reply(text)
        if who == '饲养员':
            self._voice_readback = False
            self.store.append(self.active_id, 'user', text)
            self.memory.add_dialogue(self.active_agent_id,'user',text)
            if self.store.conversation(self.active_id)['name'] == '新对话':
                self.store.rename('conversation', self.active_id, text[:24])
                self.refresh_tree()
        elif not self._voice_readback:
            from core.chat_presentation import is_progress_ack
            self.store.append(self.active_id, 'tool' if is_progress_ack(text) else 'assistant', text)
            if not is_progress_ack(text):self.memory.add_dialogue(self.active_agent_id,'assistant',text)
        if who != '饲养员':
            self._show_active_bubble(text)
        self.render()

    def _finish_voice_task_confirmation(self,text,action):
        """Consume a short yes/no locally; Qwen must not reinterpret it."""
        self._voice_readback=False
        self.store.append(self.active_id,'user',text)
        if action=='cancel':
            self._cancel_execution()
            answer='已确认停止当前任务；已经完成的操作保留在记录中。'
        elif action=='continue':
            answer='好，继续当前任务。'
        else:
            answer='当前任务仍在执行。确定要停止吗？请说“停止”确认，或说“继续”任务。'
        self.status.setText(answer)
        self.store.append(self.active_id,'system',answer)
        self._show_active_bubble(answer)
        if self.voice:
            self.voice.interrupt()
        self.render()

    def _voice_finished(self):
        if self.sender() is self.voice:
            self.voice_button.setChecked(False)
            if not self.voice.last_error:
                self.status.setText('通话已结束 · 阿里语音连接已关闭')

    def shutdown(self):
        self._task_speech.clear()
        self.runtime.media.stop()
        self.proactive.stop()
        self.music_listener.enable(False)
        self.answer.cancel();self.short_voice.stop(cancel=True)
        self.runtime.music.stop()
        self._task_heartbeat_timer.stop()
        self.runtime.battleground.stop()
        self.runtime.dsh.stop()
        self.agent.cancel()
        self.voice_button.setChecked(False)

    def closeEvent(self, event):
        self._clear_attachment()
        self.shutdown()
        self.pet._tts.stop()
        event.accept()

    def reject(self):
        self.close()
