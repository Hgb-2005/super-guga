"""Bounded, offline, real Win32/UIA desktop-step acceptance probe.

Run after reviewing the delivered modules:
    python tools/probe_desktop_steps.py [--visible-hold 0..30]

Only this process's two newly created windows are fixtures. The executor runs
in a child process because DesktopInteraction correctly rejects its own PID.
The fixture owner reads GetWindowText(edit), EM_GETSEL and button callbacks;
UIA receipts alone never establish success. No mocks, browser or WeChat.

The 43-second budget includes startup, optional screenshot hold and cleanup.
Each scenario calls match once and execute at most once. A failed prerequisite
is recorded, never retried. Focus stealing by the user is a test failure.
--visible-hold reserves time from testing, so 30 seconds can truncate slow UIA.
Requires Windows, pywin32, and the delivered UIAService's normal dependencies.
The Python socket audit guard applies to the executor; the delivered local
stdio UIA worker is contained in the same Windows Job.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'runtime/desktop-step-qa/native.json'
TOTAL_SECONDS = 43.0
CASE_SECONDS = 6.0
CASE_IDS = ('focus_input', 'fill_input', 'click_button', 'list_targets',
            'click_number', 'ctrl_a', 'tab', 'escape', 'scroll_down', 'stale_hwnd')
PAYLOAD = '通用测试文字 QA 123'
sys.path.insert(0, str(ROOT))


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def plain_error(exc):
    # Do not collect exception reprs, environment, settings or tracebacks.
    return type(exc).__name__ + ': ' + str(exc)[:400]


def create_job():
    """Use an empty name: this pywin32 build rejects None for job names."""
    import win32api
    import win32job
    job = win32job.CreateJobObject(None, '')
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        return job
    except BaseException:
        win32api.CloseHandle(job)
        raise


def spawn_in_job(job, command):
    """Child must wait on stdin until assignment succeeds; no UIA before that."""
    import win32job
    process = subprocess.Popen(
        command, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8'),
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        win32job.AssignProcessToJobObject(job, int(process._handle))
        return process
    except BaseException:
        # Assignment can fail under a restrictive enclosing Job. The gated
        # child has not imported the executor or spawned descendants yet.
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=0.5)
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        raise


def close_job(job):
    """Allow finite exit bookkeeping before treating Job members as leaks."""
    import win32api
    import win32job
    def members():
        return list(win32job.QueryInformationJobObject(job, win32job.JobObjectBasicProcessIdList))
    try:
        before = active = members()
        deadline = time.monotonic() + 0.4
        while active and time.monotonic() < deadline:
            time.sleep(0.01)
            active = members()
        forced = bool(active)
        if forced:
            win32job.TerminateJobObject(job, 1)
            deadline = time.monotonic() + 0.5
            while active and time.monotonic() < deadline:
                time.sleep(0.01)
                active = members()
        return dict(forced_job_termination=forced, job_empty=not active,
                    job_members_before=before, job_members_after=active)
    finally:
        win32api.CloseHandle(job)


class FixturePrerequisiteFailed(RuntimeError):
    """Abort dispatch and later fixture mutations after one setup failure."""


class Fixture:
    """GUI-thread-owned native windows; all observations are local Win32 reads."""

    def __init__(self, visible=True):
        import win32api
        import win32con
        import win32gui
        self.api, self.con, self.gui = win32api, win32con, win32gui
        # These APIs are absent from some win32gui builds. Explicit HWND types
        # also prevent 64-bit handle truncation when calling User32 directly.
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.SetScrollRange.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, wintypes.BOOL)
        self.user.SetScrollRange.restype = wintypes.BOOL
        self.user.SetScrollPos.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_int, wintypes.BOOL)
        self.user.SetScrollPos.restype = ctypes.c_int
        self.user.GetScrollPos.argtypes = (wintypes.HWND, ctypes.c_int)
        self.user.GetScrollPos.restype = ctypes.c_int
        self.instance = win32api.GetModuleHandle(None)
        self.class_name = 'GugaDesktopQA_' + uuid.uuid4().hex
        self.windows = []
        self.forms = {}
        self.old_procs = {}
        self.registered = False
        self.setup_failed = False
        self.callback = self.wndproc  # Keep Python callback references alive.
        wc = win32gui.WNDCLASS()
        wc.hInstance = self.instance
        wc.lpszClassName = self.class_name
        wc.lpfnWndProc = self.callback
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        win32gui.RegisterClass(wc)
        self.registered = True
        try:
            for role, x in (('primary', 60), ('secondary', 160)):
                hwnd = win32gui.CreateWindowEx(
                    0, self.class_name, '本机通用步骤验收 ' + role,
                    win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VSCROLL,
                    x, 80, 620, 380, 0, 0, self.instance, None)
                self.windows.append(hwnd)
                state = dict(hwnd=hwnd, clicks=0, keys=[], wheel=0, scroll=0)
                self.forms[role] = state
                # Unnamed unique Edit: input_target must resolve a real control.
                state['edit'] = win32gui.CreateWindowEx(
                    win32con.WS_EX_CLIENTEDGE, 'Edit', '',
                    win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP |
                    win32con.ES_AUTOHSCROLL, 24, 40, 530, 34,
                    hwnd, 101, self.instance, None)
                state['button'] = win32gui.CreateWindowEx(
                    0, 'Button', '执行测试',
                    win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP |
                    win32con.BS_PUSHBUTTON, 24, 96, 180, 40,
                    hwnd, 102, self.instance, None)
                win32gui.CreateWindowEx(
                    0, 'Static', '仅含通用测试文字；本机原生窗口',
                    win32con.WS_CHILD | win32con.WS_VISIBLE,
                    24, 158, 530, 28, hwnd, 103, self.instance, None)
                for child in (state['edit'], state['button']):
                    self.old_procs[child] = win32gui.SetWindowLong(
                        child, win32con.GWL_WNDPROC, self.callback)
                if not self.user.SetScrollRange(hwnd, win32con.SB_VERT, 0, 100, False):
                    raise ctypes.WinError(ctypes.get_last_error())
                if visible:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
                    win32gui.UpdateWindow(hwnd)
        except BaseException as exc:
            try:
                self.close()
            except Exception as cleanup_exc:
                exc.add_note('Fixture cleanup: ' + plain_error(cleanup_exc))
            raise

    def state_for(self, hwnd):
        return next((s for s in self.forms.values()
                     if hwnd in (s['hwnd'], s.get('edit'), s.get('button'))), None)

    def wndproc(self, hwnd, msg, wp, lp):
        c, g = self.con, self.gui
        state = self.state_for(hwnd)
        if state is not None:
            if msg == c.WM_COMMAND and (wp & 0xffff) == 102 and (wp >> 16) == c.BN_CLICKED:
                if lp == state['button']:
                    state['clicks'] += 1
                    return 0
            if msg == c.WM_KEYDOWN:
                ctrl = self.api.GetKeyState(c.VK_CONTROL) < 0
                state['keys'].append(dict(vk=int(wp), ctrl=ctrl))
                state['keys'] = state['keys'][-64:]
                # A plain WNDCLASS has no dialog manager. Supply conventional
                # Tab navigation and Ctrl+A, triggered ONLY by real key input.
                if wp == c.VK_TAB:
                    g.SetFocus(state['button'] if hwnd == state['edit'] else state['edit'])
                    return 0
                if wp == ord('A') and ctrl and hwnd == state['edit']:
                    g.SendMessage(hwnd, c.EM_SETSEL, 0, -1)
                    return 0
                if wp == c.VK_ESCAPE:
                    return 0
            if msg == c.WM_MOUSEWHEEL:
                delta = (wp >> 16) & 0xffff
                if delta >= 32768:
                    delta -= 65536
                state['wheel'] += 1
                state['scroll'] = max(0, min(100, state['scroll'] - delta // 12))
                self.user.SetScrollPos(state['hwnd'], c.SB_VERT, state['scroll'], True)
                return 0
            if msg == c.WM_CLOSE:
                # A user closing the fixture invalidates later prerequisites.
                g.DestroyWindow(hwnd)
                return 0
        if hwnd in self.old_procs:
            return g.CallWindowProc(self.old_procs[hwnd], hwnd, msg, wp, lp)
        return g.DefWindowProc(hwnd, msg, wp, lp)

    def pump(self):
        self.gui.PumpWaitingMessages()

    def snapshot(self):
        g, c = self.gui, self.con
        result = {}
        for role, s in self.forms.items():
            if not all(g.IsWindow(s[k]) for k in ('hwnd', 'edit', 'button')):
                raise RuntimeError('A fixture window was closed')
            selection = int(g.SendMessage(s['edit'], c.EM_GETSEL, 0, 0))
            result[role] = dict(
                hwnd=s['hwnd'], edit=s['edit'], button=s['button'],
                text=g.GetWindowText(s['edit']),  # same-process native read
                clicks=s['clicks'], keys=list(s['keys']), wheel=s['wheel'],
                scroll=self.user.GetScrollPos(s['hwnd'], c.SB_VERT),
                selection=[selection & 0xffff, (selection >> 16) & 0xffff],
                edit_class=g.GetClassName(s['edit']),
                button_class=g.GetClassName(s['button']),
                button_label=g.GetWindowText(s['button']))
        result['foreground'] = int(g.GetForegroundWindow() or 0)
        # GetFocus is meaningful here because this is the fixture's GUI thread.
        result['focus'] = int(g.GetFocus() or 0)
        return result

    def setup(self, role, reset=False, focus_button=False, clear=False):
        if self.setup_failed:
            raise FixturePrerequisiteFailed('Previous fixture setup failed; no further mutations')
        try:
            import win32process
            from core.computer_control import _attach_focus
            s = self.forms[role]
            if (s['hwnd'] not in self.windows or not self.gui.IsWindow(s['hwnd']) or
                    self.gui.GetClassName(s['hwnd']) != self.class_name or
                    win32process.GetWindowThreadProcessId(s['hwnd'])[1] != os.getpid()):
                raise RuntimeError('Fixture identity changed; no foreground request')
            # Existing helper attaches input threads temporarily and detaches in
            # finally. Its only requested target is one of our two exact HWNDs.
            if not _attach_focus(s['hwnd']):
                raise RuntimeError('Windows denied fixture foreground/focus')
            if reset:
                self.gui.SetWindowText(s['edit'], '' if clear else PAYLOAD)
                self.gui.SendMessage(s['edit'], self.con.EM_SETSEL, 0, 0)
            target = s['button'] if focus_button else s['edit']
            self.gui.SetFocus(target)
            self.pump()
            observed = self.snapshot()
            if observed['foreground'] != s['hwnd'] or observed['focus'] != target:
                raise RuntimeError('Windows denied fixture foreground/focus')
            return observed
        except Exception as exc:
            self.setup_failed = True
            raise FixturePrerequisiteFailed(plain_error(exc)) from exc

    def close(self):
        for hwnd in reversed(self.windows):
            if self.gui.IsWindow(hwnd):
                self.gui.DestroyWindow(hwnd)
        self.pump()
        if self.registered:
            self.gui.UnregisterClass(self.class_name, self.instance)
            self.registered = False


def run_executor():
    """Private subprocess entry; stdin/stdout is a bounded fixture-only RPC."""
    denied_network = []
    resolver_calls = []

    def audit(event, args):
        if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
                     'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.sendto'):
            denied_network.append(event)
            raise PermissionError('Probe forbids networking')

    sys.addaudithook(audit)

    def rpc(operation, **fields):
        emit(dict(event='rpc', operation=operation, **fields))
        line = sys.stdin.readline()  # Supervisor kills this process on deadline.
        if not line:
            raise RuntimeError('Fixture supervisor closed')
        reply = json.loads(line)
        if not reply.get('ok'):
            if reply.get('fatal_setup'):
                raise FixturePrerequisiteFailed(reply.get('error', 'Fixture setup failed'))
            raise RuntimeError(reply.get('error', 'Fixture RPC failed'))
        return reply['value']

    interaction = None
    active = None
    try:
        config = json.loads(sys.stdin.readline())  # Gate: assigned to Job first.
        import win32gui
        import win32process
        from core.desktop_interaction import DesktopInteraction
        from core.fast_commands import match

        hwnd = config['primary']
        allowed = {hwnd, config['secondary']}
        for target in allowed:
            if (not win32gui.IsWindow(target) or
                    win32process.GetWindowThreadProcessId(target)[1] != config['owner_pid'] or
                    win32gui.GetClassName(target) != config['class_name']):
                raise RuntimeError('Fixture identity mismatch; no desktop dispatch allowed')

        def deny_resolver(target_hwnd, target):
            resolver_calls.append(dict(hwnd=target_hwnd, target=str(target)[:120]))
            raise RuntimeError('Probe disables visual/network target resolution')

        # Callable injection is required: current None means visual fallback,
        # while False is not a documented disable switch in this module.
        interaction = DesktopInteraction(target_resolver=deny_resolver)
        emit(dict(event='ready', fixture_hwnd=hwnd, resolver='deny_callable',
                  real_uia_service=type(interaction.uia).__name__))
        options = []

        def scenario(case_id, text, verifier, setup=False, role='primary', prerequisite=True):
            nonlocal active
            active = dict(id=case_id, text=text, attempts=0, dispatch_ms=0.0,
                          independent_verified=False, independent=dict(observed=False), status='running')
            emit(dict(event='case_start', case=active))
            before = None
            started = None
            receipt = None
            command = None
            try:
                if not prerequisite:
                    active.update(status='skipped', reason='Required numbered button was not listed')
                    return None
                before = (rpc('setup', role=role, reset=True,
                              focus_button=setup == 'button', clear=setup == 'empty')
                          if setup else rpc('snapshot'))
                if role == 'primary' and before['foreground'] != hwnd:
                    raise FixturePrerequisiteFailed('Fixture lost foreground; no dispatch')
                command = match(text)
                active['command'] = command
                if (not isinstance(command, dict) or not command.get('matched') or
                        command.get('action') not in ('desktop_step', 'browser_step')):
                    raise RuntimeError('fast_commands.match did not return a desktop/browser step')
                # All real UIA/input dispatches have this exact explicit hwnd.
                active['attempts'] = 1
                emit(dict(event='dispatch_start', case=active))
                started = time.perf_counter()
                receipt = interaction.execute(command, hwnd=hwnd)
                active['dispatch_ms'] = round((time.perf_counter() - started) * 1000, 2)
                active['receipt'] = receipt
                # A brief delivery drain, not a retry/polling loop.
                time.sleep(0.08)
                after = rpc('snapshot')
                independently_ok, details = verifier(before, after, receipt)
                active.update(independent_verified=bool(independently_ok),
                              independent=details, before=before, after=after)
                receipt_ok = receipt.get('ok') is (case_id != 'stale_hwnd')
                active['status'] = 'passed' if receipt_ok and independently_ok else 'failed'
                return receipt
            except Exception as exc:
                if started is not None and receipt is None:
                    active['dispatch_ms'] = round((time.perf_counter() - started) * 1000, 2)
                active.update(status='failed', error=plain_error(exc))
                try:
                    after = rpc('snapshot')
                    active.update(before=before, after=after,
                                  independent=dict(observed=True, dispatch_completed=receipt is not None))
                except Exception as observe_exc:
                    active['independent'] = dict(observed=False, error=plain_error(observe_exc))
                if isinstance(exc, FixturePrerequisiteFailed):
                    raise
                return receipt
            finally:
                emit(dict(event='case_result', case=active))
                active = None

        def unchanged(a, b, role='primary'):
            # Focus/selection can legitimately change for successful actions.
            fields = ('text', 'clicks', 'wheel', 'scroll', 'keys')
            return all(a[role][k] == b[role][k] for k in fields)

        def focus_check(a, b, r):
            ok = (a['focus'] == a['primary']['button'] and
                  b['focus'] == b['primary']['edit'] and unchanged(a, b))
            return ok, dict(native_focus=b['focus'], expected=b['primary']['edit'])

        scenario('focus_input', '点击输入框', focus_check, setup='button')

        def fill_check(a, b, r):
            got = b['primary']['text']
            return (a['primary']['text'] == '' and got == PAYLOAD and
                    b['primary']['clicks'] == a['primary']['clicks'],
                    dict(method='GetWindowText(edit)', actual=got, expected=PAYLOAD))

        # Reset to empty so a failed/no-op fill cannot pass the native readback.
        scenario('fill_input', '输入“' + PAYLOAD + '”', fill_check, setup='empty')

        def click_check(a, b, r):
            delta = b['primary']['clicks'] - a['primary']['clicks']
            return (delta == 1 and a['primary']['text'] == b['primary']['text'],
                    dict(method='WM_COMMAND/BN_CLICKED callback', click_delta=delta))

        scenario('click_button', '点击执行测试', click_check, setup=True)

        def list_check(a, b, r):
            nonlocal options
            options = r.get('options', [])
            button = [o for o in options if o.get('name') == b['primary']['button_label']
                      and o.get('type') == b['primary']['button_class']]
            edit = [o for o in options if o.get('type') == b['primary']['edit_class']]
            indices = [o.get('index') for o in options]
            valid_indices = (all(type(i) is int and i > 0 for i in indices) and
                             len(set(indices)) == len(indices))
            return (len(button) == 1 and len(edit) == 1 and valid_indices and
                    unchanged(a, b) and r.get('source') == 'uia',
                    dict(method='native control classes/label and unchanged callback/text',
                         button_matches=len(button), edit_matches=len(edit),
                         unique_indices=valid_indices))

        listed = scenario('list_targets', '列出可点选项', list_check, setup=True)
        buttons = [o for o in options if o.get('name') == '执行测试' and o.get('type') == 'Button']
        index = buttons[0].get('index') if len(buttons) == 1 else None
        scenario('click_number', '点' + str(index) + '号', click_check,
                 prerequisite=bool(listed and listed.get('ok') and type(index) is int))

        def key_check(vk, extra):
            def check(a, b, r):
                new = b['primary']['keys'][len(a['primary']['keys']):]
                seen = any(k['vk'] == vk for k in new)
                safe = (a['primary']['text'] == b['primary']['text'] and
                        a['primary']['clicks'] == b['primary']['clicks'])
                return seen and safe and extra(a, b, new), dict(
                    method='native WM_KEYDOWN plus focus/EM_GETSEL',
                    key_events=new, focus=b['focus'], selection=b['primary']['selection'])
            return check

        scenario('ctrl_a', '按Ctrl+a', key_check(65, lambda a, b, keys:
                 b['primary']['selection'] == [0, len(PAYLOAD)] and
                 any(k['vk'] == 65 and k['ctrl'] for k in keys)), setup=True)
        scenario('tab', '按Tab', key_check(9, lambda a, b, keys:
                 b['focus'] == b['primary']['button']), setup=True)
        scenario('escape', '按Esc', key_check(27, lambda a, b, keys:
                 b['focus'] == a['focus']), setup=True)

        def scroll_check(a, b, r):
            return (b['primary']['wheel'] > a['primary']['wheel'] and
                    b['primary']['scroll'] > a['primary']['scroll'] and
                    b['primary']['text'] == a['primary']['text'] and
                    b['primary']['clicks'] == a['primary']['clicks'],
                    dict(method='native WM_MOUSEWHEEL and GetScrollPos',
                         wheel_delta=b['primary']['wheel'] - a['primary']['wheel'],
                         scroll_before=a['primary']['scroll'], scroll_after=b['primary']['scroll']))

        scenario('scroll_down', '向下滚动', scroll_check, setup=True)

        def stale_check(a, b, r):
            refused = r.get('ok') is False and r.get('action_sent') is False
            guard_reason = any(s in str(r.get('error', '')) for s in ('前台', '窗口已变化'))
            return (refused and guard_reason and a == b and
                    b['foreground'] == config['secondary'],
                    dict(method='both native snapshots equal after changing foreground',
                         no_side_effects=a == b, foreground_refusal=guard_reason,
                         action_sent=r.get('action_sent')))

        scenario('stale_hwnd', '点击执行测试', stale_check, setup=True, role='secondary')
    except Exception as exc:
        emit(dict(event='executor_error', error=plain_error(exc)))
    finally:
        closed = False
        error = None
        worker = None
        try:
            if interaction is not None:
                worker = interaction.uia.process
                interaction.close()
            if worker is not None:
                # Retain the Popen even though UIAService.close clears .process.
                # Its known child must be reaped before reporting uia_closed.
                if worker.poll() is None:
                    worker.terminate()
                worker.wait(timeout=0.5)
            # UIAService.close closes stdout; its finite receiver should exit.
            for thread in threading.enumerate():
                if thread.name == 'guga-uia-replies':
                    thread.join(0.3)
            closed = not any(t.name == 'guga-uia-replies' and t.is_alive()
                             for t in threading.enumerate())
        except Exception as exc:
            error = plain_error(exc)
        emit(dict(event='executor_closed', uia_closed=closed, error=error,
                  uia_exit_code=worker.returncode if worker is not None else None,
                  resolver_calls=resolver_calls, denied_network=denied_network))
    return 0


def hold_seconds(value):
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('visible hold must be a number') from None
    if not math.isfinite(seconds) or not 0 <= seconds <= 30:
        raise argparse.ArgumentTypeError('visible hold must be between 0 and 30 seconds')
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--visible-hold', type=hold_seconds, default=0,
                        help='reserve 0..30 seconds for a final screenshot, within the total budget')
    args = parser.parse_args()
    started = time.monotonic()
    if os.name != 'nt':
        parser.error('This native probe requires Windows')
    report = dict(schema_version=1, ok=False, budget_seconds=TOTAL_SECONDS,
                  visible_hold_requested=args.visible_hold, max_attempts_per_case=1,
                  cases=[], errors=[], fixtures_only=True, mocked=False,
                  network_policy='deny resolver and Python sockets; UIA uses local stdio')
    fixture = process = job = receiver = None
    pending = None
    case_started = None
    dispatch_started = None
    messages = queue.Queue(maxsize=128)
    forced = False
    deadline = started + TOTAL_SECONDS
    # Reserve cleanup headroom and the entire requested hold from dispatch time.
    test_deadline = deadline - 3.0 - args.visible_hold
    try:
        import win32api
        import win32job
        job = create_job()
        fixture = Fixture()
        report['fixtures'] = fixture.snapshot()
        process = spawn_in_job(job, [sys.executable, '-B', '-u',
                                    str(Path(__file__).resolve()), '--_executor'])

        def receive():
            try:
                for line in process.stdout:
                    if len(line) > 100_000:
                        break
                    try:
                        messages.put_nowait(json.loads(line))
                    except (ValueError, queue.Full):
                        break
            except (OSError, ValueError):
                pass
            finally:
                try:
                    messages.put_nowait(dict(event='eof'))
                except queue.Full:
                    pass

        receiver = threading.Thread(target=receive, name='desktop-qa-stdio', daemon=True)
        receiver.start()
        config = dict(primary=fixture.forms['primary']['hwnd'],
                      secondary=fixture.forms['secondary']['hwnd'],
                      class_name=fixture.class_name, owner_pid=os.getpid())
        process.stdin.write(json.dumps(config) + '\n')
        process.stdin.flush()
        eof = False
        while not eof:
            fixture.pump()
            now = time.monotonic()
            if now >= test_deadline or (case_started is not None and now - case_started >= CASE_SECONDS):
                report['errors'].append('total_test_budget' if now >= test_deadline else 'case_timeout')
                forced = True
                break
            try:
                message = messages.get(timeout=0.01)
            except queue.Empty:
                continue
            event = message.get('event')
            if event == 'rpc':
                try:
                    op = message['operation']
                    if op == 'snapshot':
                        value = fixture.snapshot()
                    elif op == 'setup' and message['role'] in fixture.forms:
                        value = fixture.setup(message['role'], reset=message.get('reset') is True,
                                              focus_button=message.get('focus_button') is True,
                                              clear=message.get('clear') is True)
                    else:
                        raise ValueError('Unknown fixture RPC')
                    reply = dict(ok=True, value=value)
                except Exception as exc:
                    reply = dict(ok=False, error=plain_error(exc), fatal_setup=message.get('operation') == 'setup')
                    if reply['fatal_setup']:
                        report['fixture_setup_failed'] = True
                process.stdin.write(json.dumps(reply, ensure_ascii=True) + '\n')
                process.stdin.flush()
            elif event == 'case_start':
                pending = message['case']
                case_started = time.monotonic()
                dispatch_started = None
            elif event == 'dispatch_start':
                pending = message['case']
                dispatch_started = time.monotonic()
            elif event == 'case_result':
                report['cases'].append(message['case'])
                pending = case_started = dispatch_started = None
            elif event == 'ready':
                report['executor'] = message
            elif event == 'executor_error':
                report['errors'].append(message['error'])
            elif event == 'executor_closed':
                report['executor_cleanup'] = message
            elif event == 'eof':
                eof = True
        if not forced:
            process.wait(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
            report['executor_exit_code'] = process.returncode
        if forced:
            # Freeze pending input before observing effects of an uncertain call.
            win32job.TerminateJobObject(job, 1)
            process.wait(timeout=0.5)
        if pending is not None:
            pending.update(status='timeout', independent_verified=False,
                           dispatch_ms=round((time.monotonic() - dispatch_started) * 1000, 2)
                           if dispatch_started is not None else 0.0,
                           dispatch_ms_is_timeout_upper_bound=dispatch_started is not None,
                           independent=dict(observed=True, after=fixture.snapshot(),
                                            outcome='uncertain; no retry'))
            report['cases'].append(pending)
            pending = None
        report['final_native'] = fixture.snapshot()
        if args.visible_hold and not fixture.setup_failed and not report['errors']:
            fixture.setup('primary')
            hold_started = time.monotonic()
            hold_until = min(hold_started + args.visible_hold, deadline - 1.5)
            emit(dict(screenshot_ready=True, fixture_hwnd=config['primary'],
                      hold_seconds=round(max(0, hold_until - hold_started), 2)))
            while time.monotonic() < hold_until:
                fixture.pump()
                time.sleep(0.01)
            report['visible_hold_actual'] = round(time.monotonic() - hold_started, 3)
    except BaseException as exc:
        report['errors'].append(plain_error(exc))
    finally:
        cleanup = dict(forced_job_termination=forced, windows_closed=False,
                       executor_stopped=process is None, receiver_stopped=receiver is None,
                       job_empty=job is None)
        # No enumeration/termination of unrelated programs, only this Job.
        if job is not None:
            try:
                job_result = close_job(job)
                job_result['forced_job_termination'] |= cleanup['forced_job_termination']
                cleanup.update(job_result)
            except Exception as exc:
                report['errors'].append('job_cleanup: ' + plain_error(exc))
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()  # Also covers a failed Job assignment.
                process.wait(timeout=0.5)
                cleanup['executor_stopped'] = True
                if receiver is not None:
                    receiver.join(0.3)
                    cleanup['receiver_stopped'] = not receiver.is_alive()
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()
            except Exception as exc:
                report['errors'].append('executor_cleanup: ' + plain_error(exc))
        if fixture is not None:
            try:
                handles = list(fixture.windows)
                fixture.close()
                cleanup['windows_closed'] = not any(fixture.gui.IsWindow(h) for h in handles)
            except Exception as exc:
                report['errors'].append('fixture_cleanup: ' + plain_error(exc))
        if pending is not None:
            pending.update(status='aborted', independent_verified=False,
                           error='Supervisor aborted; no retry')
            report['cases'].append(pending)
        recorded = {c['id'] for c in report['cases']}
        for case_id in CASE_IDS:
            if case_id not in recorded:
                report['cases'].append(dict(id=case_id, status='skipped', attempts=0,
                                            dispatch_ms=0.0, independent_verified=False,
                                            independent=dict(observed=False),
                                            reason='Probe ended before this scenario'))
        report['cleanup'] = cleanup
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['actions_ok'] = (len(report['cases']) == len(CASE_IDS) and
                                all(c['status'] == 'passed' for c in report['cases']))
        report['cleanup_ok'] = bool(
            all(cleanup[k] for k in ('windows_closed', 'executor_stopped', 'receiver_stopped', 'job_empty')) and
            not cleanup['forced_job_termination'] and
            report.get('executor_cleanup', {}).get('uia_closed') is True)
        report['ok'] = bool(
            not report['errors'] and report['actions_ok'] and report['cleanup_ok'] and
            not report.get('executor_cleanup', {}).get('denied_network') and
            report['elapsed_seconds'] < TOTAL_SECONDS)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        emit(dict(ok=report['ok'], output=str(OUTPUT), elapsed_seconds=report['elapsed_seconds'],
                  cases={c['id']: c['status'] for c in report['cases']}, cleanup=cleanup))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['--_executor']:
        raise SystemExit(run_executor())
    raise SystemExit(main())
