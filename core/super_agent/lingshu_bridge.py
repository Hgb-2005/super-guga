# -*- coding: utf-8 -*-
"""灵枢（@furongjun1999/dsh-memory）MCP 桥 —— 零第三方依赖的 Python 版客户端。

为什么需要它
------------
灵枢对外的正式接口是 stdio MCP server（`python -m md_cg.mcp_server`，协议
2024-11-05，零第三方依赖）。任何 MCP 宿主都可以直挂同一份「唯一记忆真源」。
DSH 插件用的是 Node 版 `lib/bridge.js`；本文件是等价的 **Python 版**，让咕嘎桌宠 /
超级智能体调度器 / 演示脚本都能直接读写同一份 md_cg 认知图，不引入第二套记忆库。

模式
----
* 默认 **guest 只读**：不注入令牌 → md_cg 官方设计下降级为只读 guest（读/召回/时间线可用）；
* `--write`：注入 `~/.mdcg/token`（或环境变量 MDCG_TOKEN），走官方写入闸门 + 审计。

用法
----
    python lingshu_bridge.py --selftest
    python lingshu_bridge.py --recent 5
    python lingshu_bridge.py --recall "屏幕共享 记忆桥" --k 5
    python lingshu_bridge.py --remember "测试节点" --tags guqa,super-agent --write

环境变量
--------
    LINGSHU_PKG   灵枢插件包根（默认 ~/.dsh/profiles/web/node_modules/@furongjun1999/dsh-memory）
    MDCG_ROOT     认知图真源（默认 ~/.dsh/.dsh-memory/data/mdcg）
    MDCG_TOKEN    写入令牌（可替代 ~/.mdcg/token）
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

PROTOCOL = '2024-11-05'
CLIENT = {'name': 'guga-lingshu-bridge', 'version': '0.1.0'}


def default_pkg_root() -> Path:
    env = os.environ.get('LINGSHU_PKG')
    if env:
        return Path(env)
    return Path.home() / '.dsh' / 'profiles' / 'web' / 'node_modules' / '@furongjun1999' / 'dsh-memory'


def default_mdcg_root() -> Path:
    env = os.environ.get('MDCG_ROOT')
    if env:
        return Path(env)
    return Path.home() / '.dsh' / '.dsh-memory' / 'data' / 'mdcg'


def default_token() -> str:
    env = os.environ.get('MDCG_TOKEN', '').strip()
    if env:
        return env
    candidates = [os.environ.get('MDCG_TOKEN_FILE'), str(Path.home() / '.mdcg' / 'token')]
    for cand in candidates:
        if not cand:
            continue
        try:
            text = Path(cand).read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if text:
            return text
    return ''


class LingshuError(RuntimeError):
    pass


def ccg_text(title, precondition='暂无前置条件', sub='单点写入', invocation='调用 mdcg 写入接口',
             postcondition='写入后按 id 读回且内容一致', rejection='未验证的推测、凭据、与用户无关的临时噪声',
             body=''):
    """把任意自然事实编译成灵枢写入闸门要求的 CCG 六要素格式。

    灵枢写入纪律（0.9.22 实测）：content 必须以「# 要素名：」六行起首，
    缺任一要素会被 REJECT 并记入负记忆。DSH / 咕嘎产生的都是自然事实，
    所以由桥统一编译，调用方不必背格式。
    """
    lines = [
        f'# 功能名：{title}',
        f'# 生效条件：{precondition}',
        f'# 子功能：{sub}',
        f'# 执行：{invocation}',
        f'# 验证方式：{postcondition}',
        f'# 不适用条件：{rejection}',
    ]
    body = (body or '').strip()
    if body:
        lines.extend(['', body])
    return '\n'.join(lines)


class LingshuBridge:
    """最小 MCP stdio 客户端：initialize / tools/list / tools/call。"""

    def __init__(self, python=None, pkg_root=None, root=None, token='', actor=None,
                 timeout=60.0, surface='full', startup_timeout=None):
        self.python = python or sys.executable
        self.pkg_root = Path(pkg_root or default_pkg_root())
        self.root = Path(root or default_mdcg_root())
        self.token = token or ''
        self.actor = actor or ''
        self.timeout = float(timeout)
        self.startup_timeout = startup_timeout
        self.surface = surface
        self._proc = None
        self._rq: 'queue.Queue[str]' = queue.Queue()
        self._stderr: list[str] = []
        self._id = 0
        self._tools = None

    # ------------------------------------------------------------------ 进程
    def _env(self) -> dict:
        env = {**os.environ}
        env['PYTHONPATH'] = str(self.pkg_root) + os.pathsep + env.get('PYTHONPATH', '')
        env['MDCG_ROOT'] = str(self.root)
        env['MDCG_MCP_SURFACE'] = self.surface
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        if self.token:
            env['MDCG_TOKEN'] = self.token
        else:
            env.pop('MDCG_TOKEN', None)
        if self.actor:
            env['MDCG_ACTOR'] = self.actor
        return env

    def start(self):
        if self._proc is not None and self._proc.poll() is None:
            return self
        if not self.pkg_root.is_dir():
            raise LingshuError(f'灵枢插件包不存在：{self.pkg_root}')
        if not self.root.is_dir():
            raise LingshuError(f'认知图根不存在：{self.root}')
        self._proc = subprocess.Popen(
            [self.python, '-m', 'md_cg.mcp_server'],
            cwd=str(self.pkg_root), env=self._env(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        old = self.timeout
        self.timeout = float(self.startup_timeout) if self.startup_timeout is not None else max(self.timeout, 120.0)
        try:
            self._request('initialize', {
                'protocolVersion': PROTOCOL,
                'capabilities': {},
                'clientInfo': CLIENT,
            })
        finally:
            self.timeout = old
        self._notify('notifications/initialized', {})
        self._tools = self._request('tools/list', {}).get('tools', [])
        return self

    def _pump_stdout(self):
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            line = line.strip()
            if line:
                self._rq.put(line)

    def _pump_stderr(self):
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for line in proc.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 200:
                del self._stderr[:100]

    def _send(self, payload: dict):
        proc = self._proc
        if not proc or proc.poll() is not None:
            raise LingshuError('灵枢进程未运行：' + self.tail_stderr())
        if not proc.stdin:
            raise LingshuError('灵枢 stdin 不可用')
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
        proc.stdin.flush()

    def _notify(self, method, params):
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def _request(self, method, params):
        self._id += 1
        rid = self._id
        self._send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        deadline = time.monotonic() + self.timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise LingshuError(f'灵枢响应超时（{method}）；stderr: ' + self.tail_stderr())
            try:
                line = self._rq.get(timeout=min(remain, 1.0))
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise LingshuError('灵枢进程已退出：' + self.tail_stderr())
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get('id') != rid:
                continue
            if 'error' in msg:
                raise LingshuError(f"灵枢返回错误（{method}）：{msg['error']}")
            return msg.get('result') or {}

    def tail_stderr(self, n=6):
        lines = self._stderr[-n:]
        return ' | '.join(lines) if lines else '(无 stderr)'

    def stderr_lines(self):
        return list(self._stderr)

    # ------------------------------------------------------------------ 对外
    def tools(self):
        if self._tools is None:
            self._tools = self._request('tools/list', {}).get('tools', [])
        return self._tools

    def call(self, name, arguments=None, timeout=None):
        old = self.timeout
        if timeout:
            self.timeout = float(timeout)
        try:
            result = self._request('tools/call', {'name': name, 'arguments': arguments or {}})
        finally:
            self.timeout = old
        is_error = bool(result.get('isError'))
        payload = ''.join(b.get('text', '') for b in result.get('content', []) if b.get('type') == 'text')
        if not payload:
            return result
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {'text': payload}
        if is_error:
            raise LingshuError(f'灵枢工具 {name} 报错：{json.dumps(data, ensure_ascii=False)[:600]}')
        return data

    # 常用能力 -----------------------------------------------------------
    def health(self):
        return self.call('mdcg_health')

    def info(self):
        return self.call('cg', {'op': 'info'})

    def recent(self, k=8):
        return self.call('cg', {'op': 'recent', 'limit': int(k)})

    def recall(self, query, k=5):
        return self.call('cg', {'op': 'read', 'query': str(query), 'budget_tokens': 1200})

    def remember(self, text, layer='contextual', tags=None, importance=0.5, ccg=None):
        """写入一条记忆。

        ccg：可选字典 {title, precondition, sub, invocation, postcondition,
        rejection, body}；给出时自动编译成 CCG 六要素，避免被写入闸门 REJECT。
        """
        if not self.token:
            raise LingshuError('写入需要令牌：加 --write 或设置 MDCG_TOKEN / ~/.mdcg/token')
        content = ccg_text(**ccg) if ccg else str(text)
        args = {
            'op': 'write',
            'content': content,
            'content_kind': 'text',
            'layer': layer,
            'importance': float(importance),
        }
        if tags:
            args['tags'] = list(tags)
        return self.call('cg', args)

    def close(self):
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _truncate(value, limit=3000):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + '\n…(truncated)'


def main(argv=None):
    ap = argparse.ArgumentParser(description='灵枢 MCP 桥（咕嘎 / 超级智能体用）')
    ap.add_argument('--pkg-root', default='')
    ap.add_argument('--root', default='')
    ap.add_argument('--write', action='store_true', help='注入写入令牌（默认只读 guest）')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--recent', type=int, default=0)
    ap.add_argument('--recall', default='')
    ap.add_argument('--k', type=int, default=5)
    ap.add_argument('--remember', default='')
    ap.add_argument('--ccg-title', default='', help='给出时自动编译成 CCG 六要素再写入')
    ap.add_argument('--tags', default='')
    ap.add_argument('--layer', default='contextual')
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)

    bridge = LingshuBridge(
        pkg_root=ns.pkg_root or None, root=ns.root or None,
        token=default_token() if ns.write else '',
        actor=os.environ.get('MDCG_ACTOR', ''),
    )
    try:
        bridge.start()
        out = {}
        if ns.selftest:
            names = [t.get('name') for t in bridge.tools()]
            out['tool_count'] = len(names)
            out['tools'] = names
            for probe in ('mdcg_health', 'mdcg_whoami'):
                if probe in names:
                    try:
                        out[probe] = bridge.call(probe)
                    except LingshuError as exc:
                        out[probe] = {'error': str(exc)}
                    break
            try:
                out['info'] = bridge.info()
            except LingshuError as exc:
                out['info'] = {'error': str(exc)}
        if ns.recent:
            out['recent'] = bridge.recent(ns.recent)
        if ns.recall:
            out['recall'] = bridge.recall(ns.recall, ns.k)
        if ns.remember:
            tags = [t for t in ns.tags.split(',') if t] or None
            if ns.ccg_title:
                out['remember'] = bridge.remember(
                    ns.remember, layer=ns.layer, tags=tags,
                    ccg=dict(title=ns.ccg_title, body=ns.remember))
            else:
                out['remember'] = bridge.remember(ns.remember, layer=ns.layer, tags=tags)
        if not out:
            ap.print_help()
            return 2
        print(_truncate(out, 6000) if not ns.json else json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except LingshuError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        bridge.close()


if __name__ == '__main__':
    sys.exit(main())
