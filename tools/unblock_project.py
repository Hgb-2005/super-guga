r"""Strip the Mark-of-the-Web from a project tree.

Windows shows the "无法验证发布者 / 打开文件-安全警告" dialog for any file that
carries the Zone.Identifier stream. A .bat that re-launches itself (for UAC)
therefore warns twice. This helper removes the stream so a freshly unzipped
copy starts without any security prompt.

Run it after extracting an update:
    python tools/unblock_project.py            # current project
    python tools/unblock_project.py D:\guga    # another folder
"""
import ctypes
import os
import sys
from pathlib import Path

STREAM = ':Zone.Identifier'
SUFFIXES = {'.bat', '.cmd', '.exe', '.dll', '.py', '.vbs', '.ps1', '.ico', '.png', '.mp4'}


def blocked(path):
    try:
        return os.path.exists(str(path) + STREAM)
    except OSError:
        return False


def unblock(path):
    """DeleteFileW on the alternate data stream is the native equivalent of
    Explorer's 解除锁定; no PowerShell or WScript.Shell needed."""
    try:
        return bool(ctypes.windll.kernel32.DeleteFileW(str(path) + STREAM))
    except Exception:
        return False


def main(argv):
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    if not root.is_dir():
        print('目录不存在：' + str(root))
        return 2
    removed, seen = 0, 0
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('.git', 'build', 'runtime', '__pycache__')]
        for name in names:
            path = Path(folder) / name
            if path.suffix.lower() not in SUFFIXES:
                continue
            seen += 1
            if blocked(path) and unblock(path):
                removed += 1
    print('扫描 %d 个文件，解除 %d 个网络来源标记。' % (seen, removed))
    if removed:
        print('现在双击启动脚本不会再出现“无法验证发布者”的提示。')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
