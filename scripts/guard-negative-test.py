# -*- coding: utf-8 -*-
"""负向验证：受保护文件必须读不到、写不进（Argon 09:41 复检要求）。"""
import confluxconfig as cfg
import sys

sys.path.insert(0, cfg.root_path('scripts'))
from agent_loop import Runner

r = Runner("mimo", "guard test", cfg.root_path(''), 1)
try:
    TOKEN = open(cfg.root_path('data/hub/admin.token'), encoding="utf-8").read().strip()
except OSError:
    TOKEN = ""
cases = [
    ("read admin.token", lambda: r.run_tool("read_file", {"path": cfg.root_path('data/hub/admin.token')})),
    ("grep 不含令牌值", lambda: ("__LEAK__" if TOKEN and TOKEN in str(
        r.run_tool("grep", {"path": cfg.root_path('data/hub'), "pattern": "[0-9a-f]{64}"})) else "ok")),
    ("write providers.json", lambda: r.run_tool("write_file", {"path": cfg.root_path('data/hub/providers.json'), "content": "{}"})),
    ("patch providers.json", lambda: r.run_tool("patch_file", {"path": cfg.root_path('data/hub/providers.json'), "old": "a", "new": "b"})),
    ("run_cmd read token", lambda: r.run_tool("run_cmd", {"cmd": cfg.expand('Get-Content ${CONFLUX_ROOT}\\data\\hub\\admin.token')})),
]
ok = True
for name, fn in cases:
    try:
        out = fn()
        blocked = "拒绝" in str(out) or "黑名单" in str(out) or out == "ok"
        print(("PASS " if blocked else "FAIL ") + name + " -> " + str(out)[:80].replace("\n", " "))
        ok &= blocked
    except Exception as exc:
        msg = str(exc)
        blocked = "拒绝" in msg
        print(("PASS " if blocked else "FAIL ") + name + " -> 异常：" + msg[:80])
        ok &= blocked

# 正向：正常文件仍然可读可写
try:
    out = r.run_tool("read_file", {"path": cfg.root_path('data/hub/proxy.txt')})
    print(("PASS " if out and "拒绝" not in str(out) else "FAIL ") + "read proxy.txt 正常")
    ok &= bool(out) and "拒绝" not in str(out)
except Exception as exc:
    print("FAIL read proxy.txt 异常：" + str(exc)[:80])
    ok = False

print("\nRESULT:", "ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
