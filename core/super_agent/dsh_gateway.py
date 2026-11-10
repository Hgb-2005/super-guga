# -*- coding: utf-8 -*-
"""个人超级智能体 · DSH 网关客户端（咕嘎 → 运行中的 DSH web 会话）。

为什么需要它
------------
咕嘎原本只能通过 `core/dsh_worker.py` 起一个**新的** headless DSH 任务；
要指挥「已经打开的 DeepSeek Harness 会话」，官方没有对外 MCP/CLI，但本机
DSH web 网关（默认 http://127.0.0.1:3080）暴露了 Typert RPC：

    POST /api/<method>    带 operator cookie（由 GET /connect?token=<launchToken> 换得）
    信封：{"type":"client-request","rpcId":"<uuid>","method":"<method>",
           "payload":{"args":{...}}}

已实测可用（只读）：`session/list`、`session/projections`。
写操作 `session/prompt`（mode=queue）可向会话投递一条用户消息。

安全约束（重要）
----------------
- launch token / operator cookie = 本机 owner 级凭据：**只驻留内存，绝不写日志、不写记忆、不落盘**；
- 本模块默认只读；`prompt()` 是显式写操作，必须由调用方给出目标 session_id；
- 不作为后台自动行为：只在用户/工具明确要求「指挥这个会话」时调用。
"""
from __future__ import annotations

import http.client
import json
import re
import time
import urllib.parse
import uuid
from pathlib import Path

HOST = '127.0.0.1'
PORT = 3080
COOKIE_TTL_SECONDS = 600.0


class GatewayError(RuntimeError):
    pass


def _no_secret(text: str) -> str:
    """错误信息里永远不带 cookie/token 原文。"""
    cleaned = re.sub(r'(dsh-auth[^=]*=)[^;,\s]+', r'\1<redacted>', str(text))
    cleaned = re.sub(r'(token=)[^\s&,;]+', r'\1<redacted>', cleaned, flags=re.I)
    return cleaned


def discover_launch_token() -> str:
    """从 DSH 启动日志末行读取 launch token；只读，不缓存到磁盘。"""
    logs = Path.home() / '.dsh' / 'logs'
    if not logs.is_dir():
        return ''
    files = sorted(logs.glob('launch-*.log'), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[:4]:
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for line in reversed(text.splitlines()):
            match = re.search(r'[?&]token=([A-Za-z0-9._~%-]{10,})', line)
            if match:
                return urllib.parse.unquote(match.group(1))
    return ''


class DshGateway:
    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 10.0):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._cookie = ''
        self._cookie_at = 0.0
        self._token = ''

    # ------------------------------------------------------------------ 鉴权
    def _connect(self, force: bool = False):
        if force:
            self._token = ''
            self._cookie = ''
        token = self._token or discover_launch_token()
        if not token:
            raise GatewayError('找不到本机 DSH launch token：请确认 `dsh web` 正在运行且 ~/.dsh/logs 有启动日志')
        self._token = token
        path = '/connect?token=' + urllib.parse.quote(token, safe='') + '&interactions=none'
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request('GET', path)
            response = conn.getresponse()
            response.read()
            cookie = ''
            for key, value in response.getheaders():
                if key.lower() != 'set-cookie':
                    continue
                pair = value.split(';', 1)[0].strip()
                if pair.lower().startswith('dsh-auth'):
                    cookie = pair
            if response.status not in (200, 301, 302, 303, 307, 308) or not cookie:
                raise GatewayError('DSH /connect 未返回 operator cookie（HTTP %s）' % response.status)
            self._cookie = cookie
            self._cookie_at = time.monotonic()
        finally:
            conn.close()

    def _ensure_cookie(self):
        if not self._cookie or time.monotonic() - self._cookie_at > COOKIE_TTL_SECONDS:
            self._connect()

    # ------------------------------------------------------------------ RPC
    def _post_once(self, method: str, args: dict, timeout: float | None) -> tuple:
        """发一次请求，返回 (HTTP 状态, 响应字节)。"""
        rpc_id = str(uuid.uuid4())
        body = json.dumps({
            'type': 'client-request',
            'rpcId': rpc_id,
            'method': method,
            'payload': {'args': args},
        }, ensure_ascii=False).encode('utf-8')
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)
        try:
            conn.request('POST', '/api/' + method, body=body,
                         headers={'content-type': 'application/json', 'cookie': self._cookie})
            response = conn.getresponse()
            return rpc_id, response.status, response.read()
        finally:
            conn.close()

    def rpc(self, method: str, args: dict, timeout: float | None = None, _retry: bool = True) -> dict:
        self._ensure_cookie()
        rpc_id, status, raw = self._post_once(method, args, timeout)
        if status == 401 and _retry:
            # DSH 重启 / cookie 过期 / launch token 轮换：强制重新发现 token 并重试一次。
            self._connect(force=True)
            return self.rpc(method, args, timeout=timeout, _retry=False)
        if status == 401:
            raise GatewayError('DSH 网关拒绝了请求（401）：launch token 与 operator cookie 均已失效，且重试失败')
        try:
            data = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GatewayError('DSH 网关返回非 JSON（HTTP %s）' % status)
        if data.get('type') != 'server-response':
            raise GatewayError('DSH 网关响应类型异常：%s' % _no_secret(data.get('type')))
        echoed = data.get('rpcId')
        if echoed and echoed != rpc_id:
            raise GatewayError('DSH 网关 rpcId 回显不一致，拒绝使用该响应')
        result = data.get('result') or {}
        if not result.get('ok'):
            error = result.get('error') or {}
            raise GatewayError('DSH 网关调用失败（%s）：%s' % (error.get('code'), _no_secret(error.get('message'))))
        return result.get('value') or {}

    # ------------------------------------------------------------------ 只读
    def current_session_id(self) -> str:
        """最值得当作「当前会话」的一个：优先正在跑的、最近更新的。"""
        items = self.session_summaries()
        if not items:
            return ''
        running = [item for item in items if item.get('running')]
        pool = running or [item for item in items if not item.get('blank') and item.get('agentAvailable')]
        pool = pool or items
        pool = sorted(pool, key=lambda item: float(item.get('updatedAt') or 0), reverse=True)
        return str(pool[0].get('sessionId') or '')

    def list_sessions(self) -> dict:
        """列出会话（含 running/title/cwd 等投影摘要）。"""
        return self.rpc('session/list', {'_request': {}})

    def session_summaries(self) -> list:
        value = self.list_sessions()
        return value.get('items') or value.get('sessions') or []

    def find_session(self, session_id: str = '') -> dict | None:
        wanted = str(session_id or '').strip()
        items = self.session_summaries()
        if wanted:
            for item in items:
                candidate = str(item.get('sessionId') or item.get('id') or '')
                if candidate == wanted or candidate.endswith(wanted) or wanted.endswith(candidate):
                    return item
            return None
        return items[0] if items else None

    def projections(self, session_id: str) -> dict:
        return self.rpc('session/projections', {'request': {'sessionId': session_id}})

    def session_view(self, session_id: str) -> dict:
        """把 projections 归一成咕嘎可用的一小份视图（running 取自 session/list 顶层）。"""
        raw = self.projections(session_id)
        item = self.find_session(session_id) or {}
        values = raw.get('values') or {}
        todos = values.get('todos') or {}
        if isinstance(todos, dict):
            todo_items = todos.get('items') or []
        else:
            todo_items = todos
        plan = values.get('plan') or {}
        turn_outline = values.get('turnOutline') or []
        if isinstance(turn_outline, dict):
            turn_outline = turn_outline.get('items') or []
        return dict(
            session_id=session_id,
            kind=raw.get('kind'),
            as_of_seq=raw.get('asOfSeq'),
            title=values.get('title') or session_title(item),
            running=bool(item.get('running')),
            agent_available=bool(item.get('agentAvailable')),
            working_directory=values.get('workingDirectory') or str(item.get('cwd') or ''),
            token_usage=values.get('tokenUsage') or {},
            context_pressure=values.get('contextPressure') or {},
            todos=todo_items,
            plan=plan,
            turn_outline=turn_outline,
        )

    # ------------------------------------------------------------------ 写
    def prompt(self, session_id: str, text: str, mode: str = 'queue', request_id: str = '') -> dict:
        """向在线会话投递一条用户消息（写操作）。requestId 幂等，重试安全。"""
        session_id = str(session_id or '').strip()
        text = str(text or '').strip()
        if not session_id or not text:
            raise ValueError('prompt 需要明确的 session_id 与 text（不允许猜目标会话）')
        mode = str(mode or 'queue').strip()
        if mode not in ('queue', 'steer'):
            raise ValueError('mode 只支持 queue（下一轮）或 steer（插话），拒绝未知取值')
        if len(text) > 8000:
            raise ValueError('text 超过 8000 字符；拒绝静默截断，请拆成多条投递')
        resolved = self.resolve_session_id(session_id)
        request = {
            'requestId': request_id or str(uuid.uuid4()),
            'sessionId': resolved,
            'mode': mode,
            'content': [{'type': 'text', 'text': text}],
            'clientTimeZone': 'Asia/Shanghai',
        }
        return self.rpc('session/prompt', {'request': request}, timeout=20.0)

    def resolve_session_id(self, session_id: str) -> str:
        """把任意形态（裸 uuid / session- 前缀 / 后缀）归一到列表里的原样 id。"""
        wanted = str(session_id or '').strip()
        if not wanted:
            return ''
        items = self.session_summaries()
        exact = session_id_from_focused(wanted, items)
        if exact:
            return exact
        if not exact and wanted in {str(item.get('sessionId') or '') for item in items}:
            return wanted
        raise GatewayError('目标会话不在 DSH 会话列表里，拒绝投递：' + wanted[:80])

    # ------------------------------------------------------------------ 健康
    def health(self) -> dict:
        try:
            items = self.session_summaries()
        except Exception as exc:  # noqa: BLE001
            return dict(ok=False, error=str(exc)[:200], host=self.host, port=self.port)
        return dict(ok=True, host=self.host, port=self.port, sessions=len(items),
                    cookie_cached=bool(self._cookie), token=bool(self._token))


def session_title(item: dict) -> str:
    """从 session/list 条目里取标题（标题在 projections.values.title）。"""
    projections = item.get('projections') if isinstance(item.get('projections'), dict) else {}
    values = projections.get('values') if isinstance(projections.get('values'), dict) else {}
    return str(values.get('title') or item.get('title') or '')[:100]


def session_id_from_focused(focused: str, summaries: list) -> str:
    """把 dsh_link 的 focused_session（可能是 session-<uuid>）映回网关 sessionId。"""
    focused = str(focused or '').strip()
    if not focused:
        return ''
    bare = focused[len('session-'):] if focused.startswith('session-') else focused
    for item in summaries:
        candidate = str(item.get('sessionId') or item.get('id') or '')
        if candidate == focused or candidate == bare or candidate.endswith(bare):
            return candidate
    return ''
