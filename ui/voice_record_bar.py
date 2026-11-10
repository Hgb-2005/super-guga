"""One click-record/send/cancel surface, shared by chat and desktop bubble."""
import time
from PyQt5.QtCore import Qt,QTimer,pyqtSignal
from PyQt5.QtWidgets import QFrame,QHBoxLayout,QPushButton,QLabel


class VoiceRecordBar(QFrame):
    started=pyqtSignal()
    submitted=pyqtSignal()
    cancelled=pyqtSignal()
    state_changed=pyqtSignal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.phase='idle';self.level=0.;self.started_at=0.
        self.setFixedHeight(52)
        self.setObjectName('VoiceRecordBar')
        self.setStyleSheet('''
            QFrame#VoiceRecordBar {background:#f3f6ff;border:1px solid #dce5ff;border-radius:20px;}
            QFrame#VoiceRecordBar[recording="true"] {background:#edf2ff;border-color:#b8ccff;}
            QPushButton {background:transparent;border:0;color:#5574b6;font:12px "Microsoft YaHei UI";padding:4px;}
            QPushButton#RecordCancel {background:#e3e9f8;border-radius:16px;font-size:22px;min-width:32px;max-width:32px;min-height:32px;}
            QPushButton#RecordSend {background:#dce7ff;color:#315fd0;border-radius:18px;font-size:20px;min-width:36px;max-width:36px;min-height:36px;}
            QPushButton#RecordSend:disabled {color:#a8b7d9;}
            QLabel {background:transparent;border:0;color:#7c96db;font:16px "Microsoft YaHei UI";}
        ''')
        row=QHBoxLayout(self);row.setContentsMargins(5,3,5,3);row.setSpacing(8)
        self.cancel_button=QPushButton('×');self.cancel_button.setObjectName('RecordCancel')
        self.cancel_button.clicked.connect(self.cancel);row.addWidget(self.cancel_button)
        self.wave=QLabel('▁ ▁ ▁ ▁ ▁ ▁ ▁');row.addWidget(self.wave,1)
        self.hint=QPushButton('点击说话');self.hint.clicked.connect(self.begin);row.addWidget(self.hint)
        self.send_button=QPushButton('↑');self.send_button.setObjectName('RecordSend')
        self.send_button.clicked.connect(self.primary);row.addWidget(self.send_button)
        for b in self.findChildren(QPushButton):b.setFocusPolicy(Qt.NoFocus);b.setAutoDefault(False)
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.refresh)
        self.set_state('idle')

    def begin(self):
        if self.phase!='idle':return
        self.set_state('preparing');self.started.emit()

    def primary(self):
        if self.phase=='idle':self.begin()
        elif self.phase in ('recording','ready'):
            self.set_state('transcribing');self.submitted.emit()

    def cancel(self):
        self.set_state('idle');self.cancelled.emit()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton:
            if self.phase=='idle':self.begin()
            event.accept();return
        super().mouseReleaseEvent(event)

    def set_state(self,state):
        previous=self.phase;self.phase=str(state)
        self.setProperty('recording',state!='idle')
        self.style().unpolish(self);self.style().polish(self)
        self.send_button.setEnabled(state in ('idle','recording','ready'))
        self.send_button.setToolTip('开始录音' if state=='idle' else '发送这段录音')
        self.hint.setEnabled(state=='idle')
        self.cancel_button.setToolTip('取消录音，不发送')
        if state=='recording':
            if previous!='recording':self.started_at=time.monotonic();self.level=0.
            self.timer.start();self.refresh()
        else:
            self.timer.stop();self.wave.setText('▁ ▁ ▁ ▁ ▁ ▁ ▁')
            self.hint.setText({'idle':'点击说话','preparing':'准备麦克风…','transcribing':'正在识别…','ready':'录音已结束 · 点击 ↑ 发送'}.get(state,state))
        self.state_changed.emit(self.phase)

    def set_level(self,value):
        self.level=max(0.,min(1.,float(value)))
        if self.phase=='recording':self.refresh()

    def refresh(self):
        bars='▁▂▃▄▅▆▇█';level=int(self.level*7)
        self.wave.setText(' '.join(bars[max(0,min(7,int(level*scale)))] for scale in (.3,.55,.85,1.,.85,.55,.3)))
        elapsed=min(30,int(time.monotonic()-self.started_at))
        self.hint.setText(f'录音中 {elapsed:02d}s · ↑ 发送')
