"""Resolve the actual Windows HTTP default instead of guessing Edge/Chrome."""
import os,re,subprocess
from pathlib import Path

def default_executable():
    if os.name!='nt':return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice') as key:
            progid=winreg.QueryValueEx(key,'ProgId')[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,progid+r'\shell\open\command') as key:
            command=os.path.expandvars(winreg.QueryValue(key,None))
        match=re.match(r'\s*(?:"([^"]+\.exe)"|(.+?\.exe))(?:\s|$)',command,re.I)
        if match:
            path=Path(match.group(1) or match.group(2))
            if path.is_file():return path
    except OSError:pass
    return None

def launch(url):
    executable=default_executable()
    if executable:
        subprocess.Popen([str(executable),url],creationflags=0x08000000)
        return dict(kind='url',target=url,browser=executable.stem,launch_requested=True,
                    verified_running=False,message='已向默认浏览器提交网址；尚未验证页面加载')
    import webbrowser
    if not webbrowser.open(url,new=2):raise RuntimeError('Windows 未接受默认浏览器启动请求')
    return dict(kind='url',target=url,launch_requested=True,verified_running=False,
                message='已向默认浏览器提交网址；尚未验证页面加载')
