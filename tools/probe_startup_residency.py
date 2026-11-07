"""Finite offscreen probe: startup stays light until the AI workspace is requested."""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from launcher import _prepare_qt

_prepare_qt()
from ui.pet_window import PetWindow

app = QApplication([])
process = psutil.Process()
base = dict(rss_mb=round(process.memory_info().rss / 1048576, 1), threads=process.num_threads())


def settle(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)


def cpu_snapshot(seconds=2.0):
    before = process.cpu_times()
    settle(seconds)
    after = process.cpu_times()
    used = (after.user - before.user) + (after.system - before.system)
    return round(used / seconds * 100, 1)


with patch('core.keyboard_hook.KeyboardHook.start'), patch('ui.tts_engine.TTSEngine.speak'):
    started = time.perf_counter()
    pet = PetWindow()
    build_ms = round((time.perf_counter() - started) * 1000, 1)
    idle_cpu = cpu_snapshot(2.0)
    lazy = dict(build_ms=build_ms, agent_panel=pet._agent_panel is not None, idle_cpu_one_core_pct=idle_cpu,
                rss_mb=round(process.memory_info().rss / 1048576, 1), threads=process.num_threads())
    started = time.perf_counter()
    panel = pet._ensure_agent_panel()
    workspace_ms = round((time.perf_counter() - started) * 1000, 1)
    settle(.2)
    warmed_cpu = cpu_snapshot(1.0)
    warmed = dict(build_ms=workspace_ms, idle_cpu_one_core_pct=warmed_cpu,
                  rss_mb=round(process.memory_info().rss / 1048576, 1), threads=process.num_threads())
    for timer in pet.findChildren(QTimer):
        timer.stop()
    panel.shutdown()
    panel.close()
    pet._anim.stop()
    pet._status.stop()
    pet._tray.hide()

print(json.dumps(dict(ok=True, base=base, startup=lazy, ai_workspace_on_demand=warmed), ensure_ascii=False))
