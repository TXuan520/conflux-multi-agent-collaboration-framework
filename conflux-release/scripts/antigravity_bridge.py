# -*- coding: utf-8 -*-
"""
跟 Antigravity 对话：发消息走 language_server 的 agentapi，读回复读它的页面文字。

用法：
    python antigravity-bridge.py say "你好"
    python antigravity-bridge.py send "你好"
    python antigravity-bridge.py read
"""
import confluxconfig as cfg
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import websocket

LS = cfg.home_path('AppData/Local/Programs/Antigravity/resources/bin/language_server.exe')
LS = LS.replace("\\Antigravity\\", "\\antigravity\\")
CDP_PORT = 60416
CONV = cfg.optional_env("CONFLUX_AG_CONV", "")

FOOTER_MARKS = ["Ask anything, @ to mention, / for actions", "Gemini", "Default", "Working", "Thinking", "Quote", "Ctrl+L", "Files", "Terminal", "Extensions", "Subagents", "Overview", "Review", "Turbo"]


def discover():
    """现找 Antigravity 的端口和 csrf：每次重启都会变。"""
    import subprocess as _sp
    out = ""
    try:
        out = _sp.run(["powershell", "-NoProfile", "-Command",
                       "Get-CimInstance Win32_Process -Filter 'Name=\"language_server.exe\"' | "
                       "Select-Object -First 1 -ExpandProperty CommandLine"],
                      capture_output=True, timeout=25, encoding="utf-8", errors="ignore", creationflags=0x08000000).stdout or ""
    except Exception:
        out = ""
    csrf = ""
    m = re.search(r"--csrf_token\s+([0-9a-f-]{36})", out)
    if m:
        csrf = m.group(1)

    ports = []
    try:
        out2 = _sp.run(["powershell", "-NoProfile", "-Command",
                        "$p=(Get-CimInstance Win32_Process -Filter 'Name=\"language_server.exe\"').ProcessId; "
                        "Get-NetTCPConnection -State Listen | Where-Object { $_.OwningProcess -in $p } | "
                        "Select-Object -ExpandProperty LocalPort"],
                       capture_output=True, timeout=25, encoding="utf-8", errors="ignore", creationflags=0x08000000).stdout or ""
        ports = [int(x) for x in out2.split() if x.strip().isdigit()]
    except Exception:
        ports = []
    return ports, csrf


def ls_env():
    ports, csrf = discover()
    env = dict(os.environ)
    if ports:
        env["ANTIGRAVITY_LS_ADDRESS"] = "127.0.0.1:%d" % ports[0]
    if csrf:
        env["ANTIGRAVITY_CSRF_TOKEN"] = csrf
    return env


CONV_FILE = os.path.join(cfg.root_path('data/hub'), "ag-conv.txt")


def set_conv(conv_id):
    """原子写入会话 id：先写临时文件再 replace，避免读到半个文件。"""
    tmp = CONV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(conv_id)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONV_FILE)
    return conv_id


def latest_conv_id():
    """优先用固定的会话 ID（data/hub/ag-conv.txt），没有再退回最新 transcript。"""
    if CONV:
        return CONV
    try:
        if os.path.exists(CONV_FILE):
            fixed = open(CONV_FILE, encoding="utf-8").read().strip()
            if fixed:
                return fixed
    except Exception:
        pass
    try:
        tp = transcript_path()
        if tp and os.path.exists(tp):
            norm = os.path.normpath(tp).replace("\\", "/")
            parts = norm.split("/")
            if len(parts) >= 4 and parts[-3] == ".system_generated":
                return parts[-4]
    except Exception:
        pass
    return CONV


MEMORY_FILE = cfg.expand('${CONFLUX_WORKSPACE}/gemini-memory.md')
MEMORY_STATE = os.path.join(cfg.root_path('data/hub'), "ag-memory-state.json")


def memory_bootstrap(conv, query=""):
    # Bootstrap is now a separate hub initialization turn.
    return "", {}


def send(text, conv=None):
    if not conv:
        conv = latest_conv_id()
    if not conv:
        return False, "未发现会话，请配置 CONFLUX_AG_CONV 或先创建会话"
    p = subprocess.run([LS, "agentapi", "send-message", conv, text],
                       capture_output=True, timeout=None, env=ls_env(),
                       encoding="utf-8", errors="ignore", creationflags=0x08000000)
    out = (p.stdout or "").strip()
    try:
        o = json.loads(out)
    except Exception:
        return False, out[:300]
    ok = "error" not in o
    return ok, json.dumps(o.get("response") or o.get("error"), ensure_ascii=False)[:300]


_CDP_CACHE = {"port": 0, "ts": 0.0}


def _probe_cdp(port, timeout=1.2):
    import urllib.request as _u
    try:
        with _u.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=timeout) as r:
            return b"webSocketDebuggerUrl" in r.read(4000)
    except Exception:
        return False


def find_cdp():
    """扫 Antigravity 的监听端口，找那个能出 /json/list 的。

    改动：先查缓存，再快速扫 55400-55600 区间（Chrome 的调试端口就落在这些高位端口），
    最后才回落到慢速的 PowerShell 端口枚举——原来每发一条消息都要起一个 powershell，
    端口一变就 25 秒起步，直接把 5 分钟心跳窗口耗光。
    """
    import subprocess as _sp
    import urllib.request as _u
    try:
        cached = int(_CDP_CACHE.get("port") or 0)
        if cached and (time.time() - float(_CDP_CACHE.get("ts") or 0) < 300) and _probe_cdp(cached):
            return cached
        for _p in range(55400, 55601):
            if _probe_cdp(_p, timeout=0.25):
                _CDP_CACHE["port"], _CDP_CACHE["ts"] = _p, time.time()
                return _p
    except Exception:
        pass
    try:
        out = _sp.run(["powershell", "-NoProfile", "-Command",
                       "$p=(Get-Process Antigravity -ErrorAction SilentlyContinue).Id; "
                       "Get-NetTCPConnection -State Listen | Where-Object { $_.OwningProcess -in $p } | "
                       "Select-Object -ExpandProperty LocalPort"],
                      capture_output=True, timeout=25, encoding="utf-8", errors="ignore", creationflags=0x08000000).stdout or ""
    except Exception:
        return CDP_PORT
    for tok in out.split():
        if not tok.strip().isdigit():
            continue
        port = int(tok)
        try:
            with _u.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=1.5) as r:
                if b"webSocketDebuggerUrl" in r.read(4000):
                    _CDP_CACHE["port"], _CDP_CACHE["ts"] = port, time.time()
                    return port
        except Exception:
            continue
    return CDP_PORT


def page_text():
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % find_cdp(), timeout=5) as r:
        targets = json.loads(r.read().decode())
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        return ""
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": "document.body.innerText", "returnByValue": True}}))
    text = ""
    while True:
        o = json.loads(ws.recv())
        if o.get("id") == 1:
            text = (o.get("result", {}).get("result", {}) or {}).get("value", "") or ""
            break
    ws.close()
    return text


import re as _re


def _is_chrome(line):
    s = line.strip()
    if not s:
        return True
    if any(m in s for m in FOOTER_MARKS):
        return True
    if _re.fullmatch(r"(just now|\d+\s*(s|m|h|d)\s*ago|\d{1,2}:\d{2})", s):
        return True
    if _re.fullmatch(r"\d+ files? changed", s) or _re.fullmatch(r"[+-]\d+", s):
        return True
    if _re.fullmatch(r"\d+", s):
        return True
    return False


def clean(chunk):
    lines = [l.rstrip() for l in chunk.splitlines()]
    while lines and _is_chrome(lines[-1]):
        lines.pop()
    while lines and _is_chrome(lines[0]):
        lines.pop(0)
    # 按空行切段，去掉重复段（页面常常把最后一条在底部再显示一遍）
    text = "\n".join(lines).strip()
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    # 先截掉侧边栏（它排在网页文字最后）
    cut = text.find("\nTurbo\n")
    body = text[:cut] if cut > 0 else text
    # 找最后一个「Message from System」之后的内容
    marker = "Message from System"
    if marker in body:
        body = body.rsplit(marker, 1)[1]
    # 去掉过程提示行（Thought for / Ran / Analyzed / Working …）
    skip = ("Thought for", "Ran", "Analyzed", "Working", "Thinking")
    keep = []
    for l in body.splitlines():
        s = l.strip()
        if not s or _is_chrome(l) or s.startswith(skip):
            continue
        keep.append(s)
    keep = [l for l in keep if not l.isdigit()]
    if not keep:
        return ""
    # 取最后一段连续文字
    text_out = "\n".join(keep[-6:]).strip()
    # 同一段重复两遍就只留一遍
    paras = [p.strip() for p in text_out.split("\n") if p.strip()]
    if len(paras) >= 2 and paras[0] == paras[-1]:
        paras = paras[:-1]
    return "\n".join(paras).strip()


def common_prefix_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def last_reply_block(t):
    """只取最后一条回复：找最后一个 Message from System 之后的内容。"""
    marker = "Message from System"
    if marker not in t:
        return ""
    seg = t.rsplit(marker, 1)[1]
    return clean(seg)


def wait_reply(baseline, timeout=240):
    """等最后一条回复稳定下来；只返回这一条，不带前面的历史。"""
    end = time.time() + timeout
    last = ""
    stable = 0
    while time.time() < end:
        time.sleep(3)
        t = page_text()
        busy = "Working" in t[-300:]
        block = last_reply_block(t)
        # 回复内容变了才算新消息
        if block and block != baseline_block(baseline) and block == last and not busy:
            stable += 1
            if stable >= 2:
                return block
        else:
            stable = 0
            last = block
    return None  # Timeout is not a completed model reply.


def baseline_block(t):
    return last_reply_block(t)


def transcript_path():
    if CONV:
        return os.path.join(os.path.expanduser(r"~\.gemini\antigravity\brain"),
                            CONV, ".system_generated", "logs", "transcript.jsonl")
    try:
        if os.path.exists(CONV_FILE):
            fixed = open(CONV_FILE, encoding="utf-8").read().strip()
            if fixed:
                fp = os.path.join(os.path.expanduser(r"~\.gemini\antigravity\brain"),
                                  fixed, ".system_generated", "logs", "transcript.jsonl")
                # 只要固定了会话，就认它；文件还没生成也返回路径，绝不回退到别的会话
                return fp
    except Exception:
        pass
    """取最近改动过的那个 transcript.jsonl。"""
    import glob as _g
    base = os.path.join(os.environ["USERPROFILE"], ".gemini", "antigravity", "brain")
    files = _g.glob(os.path.join(base, "*", ".system_generated", "logs", "transcript.jsonl"))
    if not files:
        return TRANSCRIPT
    return max(files, key=os.path.getmtime)


TRANSCRIPT = os.path.join(os.environ["USERPROFILE"], ".gemini", "antigravity", "brain",
                          CONV, ".system_generated", "logs", "transcript.jsonl")


def is_transcript_busy(max_age=180):
    """检查当前会话 transcript 最后一条记录是否仍在运行（工具未完成或 step 未 DONE）。"""
    try:
        tp = transcript_path()
        if not tp or not os.path.exists(tp):
            return False
        if time.time() - os.path.getmtime(tp) > max_age:
            return False
        last_obj = None
        with open(tp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if s:
                    last_obj = s
        if not last_obj:
            return False
        item = json.loads(last_obj)
        has_tools = bool(item.get("tool_calls"))
        step_type = item.get("type", "")
        step_stat = item.get("status", "")
        step_src = item.get("source", "")
        content = item.get("content", "")
        return bool(
            has_tools or
            step_type in ("GENERIC", "USER_INPUT") or
            step_stat != "DONE" or
            (step_src == "MODEL" and step_type == "PLANNER_RESPONSE" and not content and not has_tools)
        )
    except Exception:
        return False


def transcript_step():
    """当前最后一条的 step_index，用来判断有没有新消息。"""
    last = -1
    try:
        with open(transcript_path(), "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                idx = o.get("step_index", -1)
                if isinstance(idx, int) and idx > last:
                    last = idx
    except OSError:
        pass
    return last


def wait_reply2(baseline_step, timeout=180, accept_timeout=30, on_beat=None):
    """Observe new transcript records and fail when acceptance or progress stops."""
    started = last_progress = time.monotonic()
    accepted = False
    previous = None
    last_content = ""
    stable = 0
    while True:
        newest = ""
        busy = False
        records = []
        try:
            with open(transcript_path(), "r", encoding="utf-8", errors="ignore") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    idx = item.get("step_index", -1)
                    if not isinstance(idx, int) or idx <= baseline_step:
                        continue
                    records.append(item)
                    if item.get("type") == "ERROR_MESSAGE" or item.get("error"):
                        raise RuntimeError("Antigravity 上游报错：%s" % str(item.get("error") or item.get("content"))[:200])
                    if item.get("source") == "MODEL" or item.get("tool_calls"):
                        busy = (item.get("status") != "DONE" or bool(item.get("tool_calls"))
                                or item.get("type") != "PLANNER_RESPONSE")
                        if busy:
                            newest = ""
                    if (item.get("source") == "MODEL" and item.get("type") == "PLANNER_RESPONSE"
                            and item.get("status") == "DONE" and item.get("content") and not item.get("tool_calls")):
                        newest = item["content"]
        except OSError:
            pass
        if records:
            last_item = records[-1]
            if (last_item.get("tool_calls") or
                last_item.get("type") in ("GENERIC", "USER_INPUT") or
                last_item.get("status") != "DONE" or
                (last_item.get("source") == "MODEL" and not (last_item.get("content") and not last_item.get("tool_calls")))):
                busy = True
                newest = ""
        now = time.monotonic()
        signature = json.dumps(records, ensure_ascii=False, sort_keys=True)
        if records and signature != previous:
            accepted = True
            last_progress = now
            previous = signature
            if on_beat:
                latest = records[-1]
                on_beat("step %s · %s · %s字" % (latest["step_index"], latest.get("type", "生成中"),
                                                 len(str(latest.get("content") or ""))))
        if not accepted and accept_timeout is not None and now - started >= accept_timeout:
            raise TimeoutError("Gemini 客户端 %ss 未响应" % ("%g" % accept_timeout))
        if timeout is not None and now - last_progress >= timeout:
            raise TimeoutError("Gemini 客户端生成中断超时（静默 %gs）" % timeout)
        if newest and newest == last_content and not busy:
            stable += 1
            if stable >= 2:
                return newest.strip()
        else:
            stable = 0
            last_content = newest
        time.sleep(1)


def say(text):
    baseline = transcript_step()
    ok, msg = send(text)
    print("发送:", "成功" if ok else "失败", msg)
    if not ok:
        return
    print("等待回复……")
    print(wait_reply2(baseline)[-2000:])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    if cmd == "send":
        print(send(sys.argv[2]))
    elif cmd == "say":
        say(sys.argv[2])
    elif cmd == "read":
        print(page_text()[-2000:])
    else:
        sys.exit("用法: send|say|read")
