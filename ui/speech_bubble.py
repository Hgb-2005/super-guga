"""Non-activating, clickable speech above the pet; does not own audio lifetime."""
from PyQt5.QtCore import Qt, QEvent, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication


class PetSpeechBubble(QFrame):
    clicked = pyqtSignal()
    end_call = pyqtSignal()
    talk_started = pyqtSignal()
    talk_stopped = pyqtSignal(bool)

    def __init__(self, pet):
        super().__init__(pet, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.pet = pet
        self.call_active = False
        self.speaking = False
        self._recording = False
        self._voice_state = 'idle'
        self._wave_index = 0
        self.conversation_id = None
        self.full_text = ''
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(QFont('Microsoft YaHei UI', 10))
        self.setFixedWidth(326)
        self.setObjectName('SpeechBubble')
        self.setStyleSheet('''
            QFrame#BubbleCard { background:#fffdf8; border:1px solid #d9d7e7; border-radius:16px; }
            QLabel { color:#30334b; background:transparent; border:none; }
            QLabel#BubbleTitle { color:#70628d; font-size:12px; font-weight:600; }
            QLabel#BubbleHint { color:#9793a5; font-size:10px; }
            QPushButton { color:#927b84; background:transparent; border:none; padding:2px 5px; font-size:11px; }
            QPushButton:hover { color:#b6415c; background:#fbeef0; border-radius:6px; }
            QFrame#TalkDock { background:#f3f6ff; border:1px solid #dce5ff; border-radius:19px; }
            QFrame#TalkDock[recording="true"] { background:#5b86ff; border:1px solid #4b79f5; }
            QLabel#TalkWave { color:#7692d8; font-size:15px; letter-spacing:2px; }
            QFrame#TalkDock[recording="true"] QLabel#TalkWave { color:#ffffff; }
            QLabel#TalkHint { color:#7180a0; font-size:11px; }
            QFrame#TalkDock[recording="true"] QLabel#TalkHint { color:#ffffff; }
            QPushButton#TalkCancel { min-width:30px; max-width:30px; min-height:30px; max-height:30px;
                background:#e3e9f8; color:#526689; border-radius:15px; font-size:16px; padding:0; }
            QPushButton#TalkSend { min-width:34px; max-width:34px; min-height:34px; max-height:34px;
                background:#dce7ff; color:#315fd0; border-radius:17px; font-size:18px; padding:0; }
            QFrame#TalkDock[recording="true"] QPushButton#TalkCancel,
            QFrame#TalkDock[recording="true"] QPushButton#TalkSend { background:#ffffff; color:#416def; }
        ''')
        outer = QVBoxLayout(self); outer.setContentsMargins(3,3,3,8)
        card = QFrame(self); card.setObjectName('BubbleCard')
        layout = QVBoxLayout(card); layout.setContentsMargins(16,12,16,12); layout.setSpacing(7)
        header = QHBoxLayout()
        self.title = QLabel('咕嘎'); self.title.setObjectName('BubbleTitle')
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        header.addWidget(self.title,1)
        self.stop_button = QPushButton('结束通话')
        self.stop_button.setFocusPolicy(Qt.NoFocus)
        self.stop_button.clicked.connect(self.end_call)
        self.stop_button.hide()
        header.addWidget(self.stop_button)
        layout.addLayout(header)
        self.text = QLabel(); self.text.setWordWrap(True); self.text.setTextFormat(Qt.PlainText)
        self.text.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.text)
        hint = QLabel('点击 ↑ 录音，再点发送 · 右键展开完整对话'); hint.setObjectName('BubbleHint')
        hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(hint)
        from ui.voice_record_bar import VoiceRecordBar
        self.talk_dock=VoiceRecordBar(self)
        self.cancel_button=self.talk_dock.cancel_button
        self.wave=self.talk_dock.wave
        self.talk_hint=self.talk_dock.hint
        self.talk_button=self.talk_dock.send_button
        self.wave_timer=self.talk_dock.timer
        self.talk_dock.started.connect(self.talk_started)
        self.talk_dock.submitted.connect(lambda:self.talk_stopped.emit(False))
        self.talk_dock.cancelled.connect(self._cancel_recording)
        self.talk_dock.state_changed.connect(self._dock_state_changed)
        layout.addWidget(self.talk_dock)
        outer.addWidget(card)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self._expire)
        pet.installEventFilter(self)

    def set_call_active(self, active, conversation_id=None):
        self.call_active = bool(active)
        if conversation_id:
            self.conversation_id = conversation_id
        self.stop_button.setVisible(self.call_active)
        self.talk_dock.setVisible(True)
        self.title.setText('咕嘎 · 正在听' if active else '咕嘎')
        if active:
            self.show_text('我在听，可以直接和我说话。', self.conversation_id)
        elif self.isVisible():
            self.show_text('通话已结束。', self.conversation_id)
            self.timer.start(4000)

    def set_call_reconnecting(self, conversation_id=None):
        """Transient WS gap: never claim the call ended; the worker reconnects."""
        if conversation_id:
            self.conversation_id = conversation_id
        self.call_active = True
        self.stop_button.setVisible(True)
        self.talk_dock.setVisible(True)
        self.title.setText('咕嘎 · 正在重连')
        self.show_text('语音连接中断，正在自动重连…（点“结束通话”或说“闭嘴”可停止）', self.conversation_id)
        self.timer.stop()

    def show_text(self, text, conversation_id=None):
        self.full_text = str(text).strip()
        if not self.full_text:
            return
        if conversation_id:
            self.conversation_id = conversation_id
        # Readable on the desktop without opening the conversation window.
        screen = QApplication.screenAt(self.pet.frameGeometry().center()) or QApplication.primaryScreen()
        available = screen.availableGeometry().width() if screen else 1200
        self.setFixedWidth(max(380, min(620, available - 48)))
        preview = self.full_text if len(self.full_text)<=1600 else self.full_text[:1598].rstrip()+'…'
        self.text.setText(preview)
        self.talk_dock.setVisible(True)
        self.setToolTip('点击 ↑ 录音，再点发送；完整对话请从右键菜单展开')
        self.adjustSize()
        self.follow_pet()
        launcher = getattr(self.pet, '_floating_launcher', None)
        if getattr(self.pet, '_desktop_hidden', False) and launcher:
            launcher.notify_text(self.full_text)
        if self.pet.isVisible():
            try:
                import ctypes
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()),0)
            except Exception:pass
            self.show()
        if not self.speaking:
            self.timer.start(12000)

    def set_speaking(self, speaking):
        self.speaking = bool(speaking)
        self.title.setText('咕嘎 · 正在回答' if speaking else ('咕嘎 · 正在听' if self.call_active else '咕嘎'))
        if speaking:
            self.timer.stop()
        elif self.isVisible():
            self.timer.start(12000)

    def _dock_state_changed(self,state):
        self._voice_state=state;self._recording=state=='recording'
        if state!='idle':self.timer.stop()
        elif self.isVisible():self.timer.start(15000)
        self.adjustSize();self.follow_pet()

    def set_voice_state(self,state):
        self.talk_dock.set_state(state)

    def set_voice_level(self,value):
        self.talk_dock.set_level(value)

    def toggle_talk(self):
        self.talk_dock.primary()

    def _begin_talk(self):
        self.talk_dock.begin()

    def _finish_talk(self):
        if self._voice_state in ('recording','ready'):self.talk_dock.primary()

    def _cancel_recording(self):
        panel=getattr(self.pet,'_agent_panel',None)
        was_recording=bool(panel and panel.short_voice.active)
        self.talk_stopped.emit(True)
        if not was_recording:self.hide()

    def cancel_talk(self):
        self.talk_dock.cancel()

    def _expire(self):
        if self._recording or self._voice_state in ('preparing','transcribing','ready'):return
        if self.call_active:
            self.text.setText('正在听你说话…')
            self.adjustSize(); self.follow_pet()
        else:
            self.hide()

    def follow_pet(self):
        center = self.pet.frameGeometry().center()
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        x = center.x()-self.width()//2
        y = self.pet.frameGeometry().top()-self.height()+22
        # At the top screen edge, keep the bubble visible beside the pet.
        if y<area.top():
            y = self.pet.frameGeometry().top()
            x = self.pet.frameGeometry().right()+8
        x = max(area.left(),min(x,area.right()-self.width()+1))
        y = max(area.top(),min(y,area.bottom()-self.height()+1))
        self.move(x,y)

    def eventFilter(self, obj, event):
        if obj is self.pet:
            if event.type() in (QEvent.Move,QEvent.Resize):
                self.follow_pet()
            elif event.type()==QEvent.Hide:
                self.hide()
            elif event.type()==QEvent.Show and self.call_active:
                self.follow_pet(); self.show()
        return super().eventFilter(obj,event)

    def mouseReleaseEvent(self, event):
        if event.button()==Qt.LeftButton:
            self.clicked.emit(); event.accept()
        else:
            super().mouseReleaseEvent(event)
