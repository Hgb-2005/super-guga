"""Finite native window using the installed renderer; no models or microphone."""
import argparse
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication,QWidget
from PyQt5.QtGui import QPainter,QColor,QFont
from PyQt5.QtCore import QTimer
from ui.sprite_animator import SpriteAnimator


class Review(QWidget):
    def __init__(self,seconds):
        super().__init__();self.setWindowTitle('咕嘎动作对比 · 本地检查')
        self.setFixedSize(1380,530);self.started=time.monotonic()
        self.names=('doze','heart','pout','laugh');self.labels=('打盹','比心','嘟嘴','大笑')
        self.pets=[]
        for name in self.names:
            pet=SpriteAnimator(str(ROOT/'resources'),245)
            pet._idle_action_countdown=9999;pet._idle_expr_countdown=9999
            pet.on_update=self.update;pet.start();self.pets.append(pet)
        QTimer.singleShot(900,self.play)
        QTimer.singleShot(2500,lambda:self.capture('live-motion.png'))
        QTimer.singleShot(round((seconds-1)*1000),lambda:self.capture('live-settled.png'))
        QTimer.singleShot(round(seconds*1000),self.close)
    def play(self):
        for name,pet in zip(self.names,self.pets):pet.play_idle_motion(name)
    def capture(self,name):
        folder=ROOT/'runtime/resident-motion-20261108';folder.mkdir(parents=True,exist_ok=True)
        self.grab().save(str(folder/name))
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#edf0f3'))
        painter.setFont(QFont('Microsoft YaHei UI',14));painter.setPen(QColor('#24354b'))
        painter.drawText(20,30,'四段常驻动作 · 同一身体大小 / 原画大小保持 / 收尾后继续待机')
        for index,(label,pet) in enumerate(zip(self.labels,self.pets)):
            x=index*345;painter.drawText(x+135,60,label)
            pet.draw(painter,x+(345-pet.width)//2,78)
        painter.end()
    def closeEvent(self,event):
        for pet in self.pets:pet.stop()
        result=dict(seconds=round(time.monotonic()-self.started,2),visible=self.isVisible(),
            stopped=all(not p._timer.isActive() for p in self.pets),
            animations=[p.task_animation_status() for p in self.pets],microphone=False,network=False)
        (ROOT/'runtime/resident-motion-20261108/live.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        super().closeEvent(event)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=float,default=24)
    args=parser.parse_args();app=QApplication([]);window=Review(min(30,max(15,args.seconds)))
    window.show();sys.exit(app.exec_())
