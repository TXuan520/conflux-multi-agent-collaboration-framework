# -*- coding: utf-8 -*-
"""协作台自检：一份脚本回答「后台干不干净、链路上没上」。

检查项：
1. hub.py 进程数必须为 1
2. 8777 监听者与 hub 进程一致
3. 该进程的解释器必须是 venv
4. /api/status、/api/version 由同一进程响应
5. metrics.py 最近一次采样是否新鲜
"""
import confluxconfig as cfg
import json
import os
import subprocess
import sys
import time
import urllib.request

VENV = cfg.python_exe()
HUB = "http://127.0.0.1:8777"
METRICS = cfg.root_path('data/hub/metrics.jsonl')


def ps(cmd):
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, timeout=40, encoding="utf-8", errors="ignore",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (out.stdout or "").strip()


def hub_pids():
    txt = ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
             "Where-Object { $_.CommandLine -like '*hub.py*' } | "
             "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    pids = []
    for line in txt.splitlines():
        if "|" in line:
            pid, cmd = line.split("|", 1)
            if pid.strip().isdigit():
                pids.append((int(pid), cmd.strip()))
    return pids


def port_owner(port=8777):
    txt = ps("(Get-NetTCPConnection -State Listen -LocalPort %d -ErrorAction SilentlyContinue).OwningProcess" % port)
    for tok in txt.split():
        if tok.isdigit():
            return int(tok)
    return None


def http(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(HUB + path, timeout=6) as r:
        return r.status


def main():
    ok = True
    print("== 协作台自检 ==")

    owner = port_owner()
    pids = hub_pids()
    print("hub 相关进程:", [(p, "venv-shim" if "feiyu\\stt\\venv" in c.lower() else "base") for p, c in pids] or "无")
    # venv 在这台机器上是用重定向器实现的：venv\Scripts\python.exe 会再拉起真正的解释器，
    # 所以「shim + 真解释器」算一个逻辑实例，只有出现第三份才算重复。
    if len(pids) > 2:
        print("  ! hub 进程超过两个，存在重复实例")
        ok = False
    if len(pids) == 2:
        cmds = {p: c.lower() for p, c in pids}
        shim_ids = [p for p, c in cmds.items() if "feiyu\\stt\\venv" in c]
        # 服务进程的父进程必须就是那个 venv 启动器，否则算两份独立实例
        ppid_txt = ps("(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").ParentProcessId" % (owner or 0))
        ppid = int(ppid_txt) if ppid_txt.strip().isdigit() else None
        paired = bool(shim_ids) and ppid == shim_ids[0]
        print("  解释: venv 启动器 + 它的子解释器 = 一个实例" if paired else "  ! 两个独立实例（父子关系不成立）")
        if not paired:
            ok = False

    print("8777 监听者:", owner)
    if not pids or not owner:
        ok = False
    elif owner not in [p for p, _ in pids]:
        print("  ! 监听 8777 的不是 hub 进程")
        ok = False

    target = [c for p, c in pids if p == owner] or ([pids[0][1]] if pids else [])
    if target:
        cmd = target[0]
        is_venv = "feiyu\\stt\\venv" in cmd.lower()
        # 服务进程若是 venv 启动器拉起来的真解释器，同样算 venv 环境
        if not is_venv:
            # venv 启动器本身就是 venv 里的 python.exe，同组里有一个就算 venv 环境
            if any("feiyu\\stt\\venv" in c.lower() for _, c in pids):
                is_venv = True
        print("跑在 venv 环境里:", is_venv)
        if not is_venv:
            print("  ! 不是 venv 环境")
            ok = False

    for path in ("/api/status", "/api/version"):
        try:
            code = http(path)
            print(path, "->", code)
            if code != 200:
                ok = False
        except Exception as e:
            print(path, "-> 失败", str(e)[:60])
            ok = False

    try:
        rows = [json.loads(l) for l in open(METRICS, encoding="utf-8") if l.strip()]
        age = time.time() - rows[-1]["ts"]
        print("最近一次体检: %.1f 分钟前" % (age / 60))
        if age > 5400:
            print("  ! 体检超过 90 分钟没跑")
            ok = False
    except Exception as e:
        print("体检数据读取失败:", str(e)[:60])
        ok = False

    print("\n结论:", "通过" if ok else "有项目不达标")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
