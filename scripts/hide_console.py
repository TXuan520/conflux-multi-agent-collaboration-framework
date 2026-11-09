# -*- coding: utf-8 -*-
"""隐藏控制台包装器：给 codex / agent_loop 之类的控制台程序一个「隐藏的父控制台」。

为什么需要它：
    hub 用 pythonw 启动，CREATE_NO_WINDOW 能让被启动的进程没有可见控制台，
    但 codex.exe 之类的控制台程序内部再起 powershell / cmd 时，子进程会各自
    申请一个新的控制台窗口 → 屏幕上就闪一下。

做法：
    1) AllocConsole() 给自己分配一个控制台；
    2) 立刻 ShowWindow(SW_HIDE) 把它藏起来；
    3) 再启动目标程序（它继承这个隐藏控制台，内部子进程也继承，不再新开窗口）。

用法：
    python hide_console.py <exe> [args...]      # stdin 透传，stdout/stderr 透传
退出码与目标程序一致。
"""
import ctypes
import subprocess
import sys

SW_HIDE = 0


def _hide_own_console():
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.AllocConsole()
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        print("usage: hide_console.py <exe> [args...]", file=sys.stderr)
        return 2
    _hide_own_console()
    data = None
    try:
        if not sys.stdin.isatty():
            data = sys.stdin.buffer.read()
    except Exception:
        data = None
    proc = subprocess.run(sys.argv[1:], input=data, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        sys.stdout.buffer.write(proc.stdout or b"")
        sys.stdout.buffer.flush()
    except Exception:
        pass
    try:
        sys.stderr.buffer.write(proc.stderr or b"")
        sys.stderr.buffer.flush()
    except Exception:
        pass
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
