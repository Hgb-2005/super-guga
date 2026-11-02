#! python
# -*- coding: utf-8 -*-
"""黑猫桌宠 — 入口文件"""
import sys
import os

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from launcher import _prepare_qt
_prepare_qt()

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QTimer
from ui.pet_window import PetWindow


def main():
    # 高 DPI 支持
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("咕嘎企鹅桌宠")
    app.setOrganizationName("BlackCatPet")
    # This is a tray companion. Closing a transient menu/dialog must not end
    # the desktop process; the existing explicit Quit action still does that.
    app.setQuitOnLastWindowClosed(False)

    # 创建并显示窗口
    window = PetWindow()
    if '--summon-fish' in sys.argv:
        QTimer.singleShot(400, lambda:window._multi_agent.summon('002'))
    # Only an explicit launch flag starts capture; normal startup stays quiet.
    if '--voice' in sys.argv:
        QTimer.singleShot(300, window._open_agent_voice)
    if '--bg-live' in sys.argv:
        QTimer.singleShot(300, window._open_bg_live)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
