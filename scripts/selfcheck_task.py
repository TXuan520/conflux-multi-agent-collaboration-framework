# -*- coding: utf-8 -*-
"""每小时跑一次协作台自检（pythonw 版，替代 wscript + selfcheck-task.ps1，避免闪窗）。"""
import confluxconfig as cfg
import json
import os
import subprocess
import time
import urllib.request

PY = cfg.python_exe()
SCRIPT = cfg.root_path('scripts/selfcheck.py')
LOG = cfg.root_path('data/hub/selfcheck.log')
HUB_SEND = "http://127.0.0.1:8777/api/send"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main():
    try:
        r = subprocess.run([PY, SCRIPT], capture_output=True, timeout=90,
                           encoding="utf-8", errors="ignore", creationflags=FLAGS)
        out = (r.stdout or "") + (r.stderr or "")
        code = r.returncode
    except Exception as exc:
        out = "selfcheck exception: %s" % str(exc)[:200]
        code = 1

    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] exit=%d\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), code, out.strip()))
    except OSError:
        pass

    if code != 0:
        bangs = [ln.strip() for ln in out.splitlines() if "!" in ln]
        summary = "; ".join(bangs) if bangs else "self-check failed"
        body = json.dumps({"from": "watch", "text": "自检未通过：" + summary}, ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(HUB_SEND, data=body, method="POST",
                                         headers={"Content-Type": "application/json; charset=utf-8"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass


if __name__ == "__main__":
    main()
