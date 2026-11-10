# -*- coding: utf-8 -*-
"""个人超级智能体 · 桌面软件命令总线。

把「拉起 / 切换桌面软件」收敛成受控、可核验的动作：
- 复用咕嘎既有的 AuthorizedApps / fast_commands / window_inventory / ComputerControl，
  不另造一套启动逻辑；
- 只接受软件名或已授权路径，拒绝相对路径与目录；
- 返回可核验结果：窗口句柄、是否复用已有窗口、是否需要二次选择。
"""
from __future__ import annotations

import re
import time

BUILTIN_APPS = ('记事本', '计算器', '画图', '终端', '命令提示符', '资源管理器', '任务管理器', '设置')
BROWSER_ALIASES = ('浏览器', '默认浏览器', '网页浏览器', 'b站', '哔哩哔哩', 'bilibili', '上网')
SITE_HINT = {
    'b站': 'https://www.bilibili.com', '哔哩哔哩': 'https://www.bilibili.com',
    'bilibili': 'https://www.bilibili.com', '知乎': 'https://www.zhihu.com',
    '百度': 'https://www.baidu.com', 'github': 'https://github.com',
}


def _safe_name(target: str) -> str:
    name = str(target or '').strip().strip('“”"\' ')
    if not name:
        raise ValueError('缺少要操作的软件名')
    if re.search(r'[\\/]', name):
        # 只允许完整的盘符路径（已授权软件本体），拒绝相对路径与目录。
        if not re.match(r'^[A-Za-z]:\\', name):
            raise ValueError('不接受相对路径；请使用软件名或完整 exe 路径')
    return name[:120]


def list_apps(query: str = '') -> dict:
    """列出可启动的软件（已授权 + 内置安全别名）。"""
    try:
        from core.authorized_apps import AuthorizedApps
        entries = AuthorizedApps().available(str(query or ''))
    except Exception:
        entries = {}
    items = [dict(name=name, path=path, builtin=False) for name, path in list(entries.items())[:200]]
    for name in BUILTIN_APPS:
        items.append(dict(name=name, path='builtin', builtin=True))
    for name in BROWSER_ALIASES:
        items.append(dict(name=name, path='browser', builtin=True))
    return dict(ok=True, query=str(query or ''), total=len(items), apps=items)


def open_app(target: str) -> dict:
    """拉起或复用桌面软件，并尽量在 3 秒内核验到窗口。"""
    name = _safe_name(target)
    site = SITE_HINT.get(name) or SITE_HINT.get(name.lower())
    if site:
        from core.fast_commands import _open_url
        result = _open_url(site)
        hwnd = None
        verified = False
        try:
            from core.window_inventory import list_windows
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                windows = list_windows('浏览器', 10)
                if windows:
                    hwnd = windows[0]['hwnd']
                    verified = True
                    break
                time.sleep(0.2)
        except Exception:
            pass
        return dict(ok=True, **result, url=site, hwnd=hwnd, verified_window=bool(verified))
    from core.fast_commands import _open_app
    result = _open_app(name)
    if result.get('selection_required'):
        return dict(ok=True, selection_required=True, windows=result.get('windows', [])[:8],
                    message='找到多个运行中的窗口，需要指定要切换哪个')
    hwnd = None
    verified = False
    try:
        from core.window_inventory import list_windows
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            windows = list_windows(name, 10)
            if windows:
                hwnd = windows[0]['hwnd']
                verified = True
                break
            time.sleep(0.15)
    except Exception:
        pass
    return dict(ok=True, **result, hwnd=hwnd, verified_window=bool(verified))


def focus_app(target: str) -> dict:
    """把已在运行的目标软件窗口切到前台。"""
    name = _safe_name(target)
    from core.window_inventory import list_windows
    windows = list_windows(name, 10)
    if not windows:
        return dict(ok=False, code='target_missing',
                    error='没有找到正在运行的「' + name + '」窗口；可以先用 open 启动')
    if len(windows) > 1:
        return dict(ok=True, selection_required=True, windows=windows[:8],
                    message='找到多个窗口，请指定 hwnd 再 focus')
    from core.computer_control import ComputerControl
    from core.desktop_access import DesktopAccess
    value = ComputerControl(DesktopAccess()).execute(dict(action='focus', hwnd=windows[0]['hwnd']))
    return dict(ok=True, **value)


def handle(action: str = 'list', target: str = '', query: str = '') -> dict:
    """MCP 工具入口：list / open / focus / aliases。"""
    action = str(action or 'list').strip().lower()
    try:
        if action == 'list':
            return list_apps(query or target)
        if action in ('open', 'launch', 'start'):
            return open_app(target)
        if action in ('focus', 'switch', 'activate'):
            return focus_app(target)
        if action == 'aliases':
            return dict(ok=True, builtin=list(BUILTIN_APPS), browsers=list(BROWSER_ALIASES),
                        sites=dict(SITE_HINT), hint='直接传软件中文名即可，例如 打开 微信 / 打开 b站')
        return dict(ok=False, error='支持的 action：list / open / focus / aliases')
    except Exception as exc:
        return dict(ok=False, error=type(exc).__name__ + ': ' + str(exc))
