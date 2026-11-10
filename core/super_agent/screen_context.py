# -*- coding: utf-8 -*-
"""个人超级智能体 · 实时屏幕感知（内存 only）。

设计原则
--------
- 只在 DSH 任务运行时采样（由 core/dsh_link.py 负责启停），空闲不采集；
- 2fps 低分辨率缩略图 + 前台窗口元数据，用平均绝对差检测画面变化；
- 不落盘、不上传、不长期保存原始画面；摘要只使用文字（应用 / 窗口标题）；
- 采样线程异常静默降级，绝不阻塞桌宠主线程。

对外接口
--------
    start_watching(reason) / stop_watching() / get_watcher()
    context(max_age=90) -> dict | None
    summary_line() -> str              # 供 30s 汇报拼接的大白话
    snapshot_jpeg() -> bytes | None    # 显式请求视觉时的当前帧
"""
from __future__ import annotations

import threading
import time
from collections import deque

# 进程名 → 中文应用名（用于大白话汇报）
_APP_LABELS = (
    ('doubao', '豆包浏览器'),
    ('msedge', '浏览器'),
    ('chrome', '浏览器'),
    ('firefox', '浏览器'),
    ('brave', '浏览器'),
    ('weixin', '微信'),
    ('wechat', '微信'),
    ('wxwork', '企业微信'),
    ('wecom', '企业微信'),
    ('wemeet', '腾讯会议'),
    ('txmeeting', '腾讯会议'),
    ('explorer', '资源管理器'),
    ('code.exe', 'VS Code'),
    ('typora', 'Typora'),
    ('notepad', '记事本'),
    ('calculator', '计算器'),
    ('calc', '计算器'),
    ('mspaint', '画图'),
    ('wt.exe', '终端'),
    ('windowsterminal', '终端'),
    ('powershell', '终端'),
    ('pwsh', '终端'),
    ('cmd', '命令提示符'),
    ('pythonw', '咕嘎自己'),
    ('python', 'Python'),
    ('hearthstone', '炉石传说'),
    ('webcast_mate', '直播伴侣'),
    ('wps', 'WPS'),
    ('et.exe', 'WPS 表格'),
    ('wpp', 'WPS 演示'),
    ('trae', 'Trae'),
    ('codebuddy', 'CodeBuddy'),
    ('workbuddy', 'WorkBuddy'),
    ('chatgpt', 'ChatGPT'),
    ('acrobat', 'PDF'),
    ('chrome_proxy', '浏览器'),
)

# 系统浮层 / 输入法 / 桌宠自身：不作为「你现在在用什么」的叙事主体
_SKIP_PROCESSES = ('shellhost', 'textinputhost', 'startmenuexperiencehost',
                   'applicationframehost', 'searchhost', 'lockapp',
                   'pythonw', 'python.exe', 'python3')
_SKIP_TITLES = ('快速设置', 'Windows 输入体验', 'Program Manager', '任务栏', '')

_TITLE_STRIP = (' - 豆包浏览器', ' - Microsoft Edge', ' - Google Chrome', ' - 文件资源管理器',
                ' - 记事本', ' - Typora', ' — DeepSeek Harness')


def _process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ''


def foreground() -> dict | None:
    """读取当前前台窗口（标题/进程/矩形），失败返回 None。"""
    try:
        import win32gui
        import win32process
        hwnd = int(win32gui.GetForegroundWindow())
        if not hwnd:
            return None
        title = str(win32gui.GetWindowText(hwnd))[:240]
        if not title:
            return None
        pid = int(win32process.GetWindowThreadProcessId(hwnd)[1])
        rect = [int(v) for v in win32gui.GetWindowRect(hwnd)]
        return dict(hwnd=hwnd, pid=pid, process=_process_name(pid), title=title, rect=rect)
    except Exception:
        return None


def _app_label(process: str, title: str) -> str:
    name = (process or '').lower()
    for key, label in _APP_LABELS:
        if key in name:
            return label
    lowered = (title or '').lower()
    if 'deepseek harness' in lowered or 'dsh' in lowered:
        return 'DeepSeek Harness 控制台'
    return (process or '某个程序').rsplit('.', 1)[0]


def _usable(title: str, process: str) -> bool:
    text = str(title or '').strip()
    if text in _SKIP_TITLES or len(text) < 2:
        return False
    lowered = (process or '').lower()
    return not any(key in lowered for key in _SKIP_PROCESSES)


def _best_window() -> dict | None:
    """当前最值得描述的前台窗口：跳过系统浮层与桌宠自身。"""
    window = foreground()
    if window and _usable(window.get('title', ''), window.get('process', '')):
        return window
    try:
        from core.window_inventory import list_windows
        for candidate in list_windows('', 60):
            if candidate.get('minimized'):
                continue
            title = str(candidate.get('title') or '')
            process = str(candidate.get('process_name') or '')
            if not _usable(title, process):
                continue
            return dict(hwnd=candidate.get('hwnd'), pid=candidate.get('pid'),
                        process=process, title=title, rect=list(candidate.get('rect') or []))
    except Exception:
        pass
    return window


def _short_title(title: str) -> str:
    text = str(title or '').strip()
    for suffix in _TITLE_STRIP:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    return text if len(text) <= 46 else text[:44] + '…'


def _grab_thumb():
    """整屏灰度缩略图（64x36），仅内存。"""
    try:
        from PIL import ImageGrab
        import numpy as np
        image = ImageGrab.grab().convert('L').resize((64, 36))
        return np.asarray(image, dtype=np.float32)
    except Exception:
        return None


class ScreenWatcher:
    """前台屏幕采样器：2fps 采集，变化阈值 4.0（0-255 灰度平均绝对差）。"""

    def __init__(self, interval: float = 2.0, max_samples: int = 600):
        self.interval = max(1.0, min(5.0, float(interval)))
        self._samples: deque = deque(maxlen=max_samples)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._prev_thumb = None
        self._latest: dict | None = None
        self._total = 0
        self._changes = 0
        self._started_at = 0.0
        self._reason = ''

    # ------------------------------------------------------------------ 生命周期
    @property
    def active(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, reason: str = 'dsh-task') -> dict:
        with self._lock:
            if self.active:
                return dict(already_active=True, **self.status())
            self._stop.clear()
            self._prev_thumb = None
            self._started_at = time.time()
            self._reason = str(reason)[:60]
            self._thread = threading.Thread(target=self._loop, name='guga-screen-context', daemon=True)
            self._thread.start()
        return dict(already_active=False, **self.status())

    def stop(self) -> dict:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        with self._lock:
            self._thread = None
            self._prev_thumb = None
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return dict(active=self.active, interval_seconds=self.interval, reason=self._reason,
                        samples=self._total, changes=self._changes,
                        started_at=round(self._started_at, 3) if self._started_at else None,
                        latest=dict(self._latest) if self._latest else None)

    # ------------------------------------------------------------------ 采样
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._sample_once()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _sample_once(self) -> dict | None:
        window = _best_window()
        if window is None:
            return None
        thumb = _grab_thumb()
        changed = False
        diff = 0.0
        with self._lock:
            prev = self._prev_thumb
        if thumb is not None and prev is not None and getattr(thumb, 'shape', None) == getattr(prev, 'shape', None):
            try:
                import numpy as np
                diff = float(np.abs(thumb - prev).mean())
                changed = diff >= 4.0
            except Exception:
                changed = False
        app = _app_label(window.get('process', ''), window.get('title', ''))
        record = dict(at=time.time(), app=app, title=window.get('title', ''),
                      hwnd=window.get('hwnd'), rect=window.get('rect'),
                      process=window.get('process', ''), changed=bool(changed), diff=round(diff, 2))
        with self._lock:
            self._prev_thumb = thumb
            self._latest = record
            self._samples.append(record)
            self._total += 1
            if changed:
                self._changes += 1
        return record

    # ------------------------------------------------------------------ 读取
    def context(self, max_age: float = 90.0) -> dict | None:
        with self._lock:
            rec = dict(self._latest) if self._latest else None
            if not rec:
                return None
            age = time.time() - rec['at']
            if age > max_age:
                return None
            rec.update(age_seconds=round(age, 1), samples=self._total, changes=self._changes,
                       active=self.active)
            return rec

    def summary_line(self) -> str:
        """一句大白话屏幕摘要，供 30 秒汇报拼接。"""
        ctx = self.context()
        if not ctx:
            return ''
        app = ctx.get('app') or '某个程序'
        title = _short_title(ctx.get('title', ''))
        line = '另外我瞄了眼屏幕：你现在在用' + app
        if title:
            line += '，窗口是「' + title + '」'
        if ctx.get('changed'):
            line += '，画面刚刚有变化'
        return line + '。'

    def snapshot_jpeg(self, quality: int = 78):
        """当前整屏 JPEG（显式请求视觉时才调用，仅内存）。"""
        try:
            import io as _io
            from PIL import ImageGrab
            image = ImageGrab.grab().convert('RGB')
            buffer = _io.BytesIO()
            image.save(buffer, format='JPEG', quality=int(quality), optimize=False)
            return buffer.getvalue()
        except Exception:
            return None


_WATCHER: ScreenWatcher | None = None
_WATCHER_LOCK = threading.Lock()


def get_watcher() -> ScreenWatcher:
    global _WATCHER
    with _WATCHER_LOCK:
        if _WATCHER is None:
            _WATCHER = ScreenWatcher(interval=2.0)
        return _WATCHER


def _enabled() -> bool:
    import os
    return os.environ.get('GUGA_SUPER_AGENT', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def start_watching(reason: str = 'dsh-task') -> dict:
    if not _enabled():
        return dict(active=False, disabled=True, reason='super-agent-disabled')
    return get_watcher().start(reason)


def stop_watching() -> dict:
    return get_watcher().stop()


def context(max_age: float = 90.0) -> dict | None:
    return get_watcher().context(max_age)


def summary_line() -> str:
    return get_watcher().summary_line()


def probe() -> dict | None:
    """同步采样一次（汇报前首帧兜底），返回 context。"""
    watcher = get_watcher()
    try:
        watcher._sample_once()
    except Exception:
        pass
    return watcher.context(15.0)


def snapshot_jpeg():
    return get_watcher().snapshot_jpeg()
