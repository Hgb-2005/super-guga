"""Owned 45-second native text fixture; user/browser data is never touched."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication,QDialog,QVBoxLayout,QTextEdit,QLabel
app=QApplication([]);window=QDialog();window.setWindowTitle('Guga Native Selection Fixture')
layout=QVBoxLayout(window);layout.addWidget(QLabel('咕嘎选词验收：合成文本，45 秒后自动关闭。'))
editor=QTextEdit('Guga selection acceptance text.\n左键选中文字，核对咕嘎工具条。')
editor.setFont(QFont('Microsoft YaHei',22));layout.addWidget(editor)
window.resize(760,260);window.move(100,100);window.show();window.raise_();window.activateWindow();editor.setFocus()
QTimer.singleShot(45000,app.quit);app.exec_()
