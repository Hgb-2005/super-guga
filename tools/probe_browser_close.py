"""Bounded native test. Only a new, uniquely titled Edge test window is closed."""
import json,os,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def act(hwnd):
    import win32gui
    from unittest.mock import patch
    from core.browser_actions import close_current_tab
    if not win32gui.GetWindowText(hwnd).startswith('GugaCloseProbe-'):
        raise RuntimeError('Refusing to act on a non-test window')
    with patch('core.window_inventory.list_windows',return_value=[dict(hwnd=hwnd,foreground=True)]):
        return close_current_tab()


def probe():
    import psutil,win32gui
    from core.fast_commands import _browser_executable
    from core.browser_launch import default_executable
    from core.window_inventory import list_windows
    from core.computer_control import _attach_focus
    folder=ROOT/'runtime/voice-assistant-qa'/('browser-probe-default' if '--default' in sys.argv else 'browser-probe');folder.mkdir(parents=True,exist_ok=True)
    profile=(folder/'test-profile').resolve();browser=default_executable() if '--default' in sys.argv else _browser_executable()
    if not browser:raise RuntimeError('No separate test browser found')
    pages=[]
    for name in ('Keep','Close'):
        path=folder/(name+'.html')
        path.write_text('<html><head><title>GugaCloseProbe-'+name+'</title></head><body>'+name+'</body></html>',encoding='utf-8')
        pages.append(path.as_uri())
    original=win32gui.GetForegroundWindow();out={}
    try:
        proc=subprocess.Popen([str(browser),'--user-data-dir='+str(profile),'--no-first-run','--no-default-browser-check',
            '--new-window',*pages],creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+12;window=None
        while time.monotonic()<deadline:
            candidates=[w for w in list_windows(browser.name,200) if str(w.get('title','')).startswith('GugaCloseProbe-')]
            if len(candidates)==1:window=candidates[0];break
            time.sleep(.2)
        if not window:raise RuntimeError('Unique test window did not appear within 12 seconds')
        expected='GugaCloseProbe-Close' if window['title'].startswith('GugaCloseProbe-Keep') else 'GugaCloseProbe-Keep'
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--act',str(window['hwnd'])],cwd=str(ROOT),
            capture_output=True,text=True,encoding='utf-8',env=dict(os.environ,PYTHONIOENCODING='utf-8'),timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise RuntimeError('Probe worker failed: '+result.stderr[-200:])
        receipt=json.loads(result.stdout)
        survivor=win32gui.GetWindowText(window['hwnd']) if win32gui.IsWindow(window['hwnd']) else ''
        out=dict(ok=bool(receipt.get('verified') and survivor.startswith(expected)),
                 receipt=receipt,kept_test_page=survivor.startswith(expected),user_pages_touched=False)
    except Exception as exc:out=dict(ok=False,error=type(exc).__name__+': '+str(exc),user_pages_touched=False)
    finally:
        # Stop only processes whose command line contains this exact test profile.
        stopped=[]
        for process in psutil.process_iter(['pid','name','cmdline']):
            try:
                if (process.info['name'] or '').lower()!=browser.name.lower():continue
                if '--user-data-dir='+str(profile) in (process.info['cmdline'] or []):
                    children=process.children(recursive=True)
                    for child in children:
                        try:child.terminate()
                        except psutil.Error:pass
                    process.terminate();stopped.extend([process,*children])
            except psutil.Error:pass
        _,alive=psutil.wait_procs(stopped,timeout=3)
        for process in alive:
            try:process.kill()
            except psutil.Error:pass
        if win32gui.IsWindow(original):_attach_focus(original)
        out['test_processes_remaining']=len(psutil.wait_procs(alive,timeout=2)[1])
        (folder/'result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False))


if __name__=='__main__':
    if '--act' in sys.argv:print(json.dumps(act(int(sys.argv[-1])),ensure_ascii=False))
    else:probe()
