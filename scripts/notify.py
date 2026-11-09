# -*- coding: utf-8 -*-
"""完工通知：桌面弹窗 + QQ 简报（给炫炫）。

用法：
    from notify import notify_completion
    notify_completion("消息发送提速", ticket_id="T123")
    # 或命令行：python notify.py "消息发送提速"

设计：
- 弹窗用 ctypes MessageBoxW，放在后台线程，不阻塞调用方；
- QQ 简报走本机 hub 的 /api/send（from=dsh, to=human），失败不抛异常。
"""
import ctypes
import json
import sys
import threading
import urllib.request

HUB = "http://127.0.0.1:8777/api/send"
MB_OK = 0x00000000
MB_ICONINFORMATION = 0x00000040
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000


def popup(title, text, timeout_ms=0, wait=0):
    """后台线程弹窗，不抢主流程。timeout_ms=0 表示等人点确定。"""
    def _run():
        try:
            ctypes.windll.user32.MessageBoxW(
                0, text, title,
                MB_OK | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND)
        except Exception:
            pass
    t = threading.Thread(target=_run, daemon=not wait)
    t.start()
    if wait:
        t.join(wait)
    return t


def qq_brief(text):
    """往协作台（QQ）发一条简报。失败静默。"""
    try:
        body = json.dumps({"from": "dsh", "text": text, "to": ["human"]},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(HUB, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False


def notify_completion(project, ticket_id="", extra="", wait=0):
    """完工钩子：只保留桌面弹窗；群内完工汇报按 15-1 关闭。"""
    name = (project or "未命名项目").strip()
    tip = "%s完成了，需要验收" % name
    popup("Conflux 完工提醒", tip, wait=wait)
    return True


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--keep" in sys.argv          # 加 --keep 会等用户点掉弹窗再退出
    arg = args[0] if args else "测试项目"
    tid = args[1] if len(args) > 1 else ""
    if keep:
        ok = notify_completion(arg, tid, wait=1.0)
    else:
        import time as _t
        ok = notify_completion(arg, tid)
        _t.sleep(1.5)                    # 留出建窗时间，然后退出
    print("qq_brief:", ok)
