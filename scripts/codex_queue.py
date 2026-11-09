# -*- coding: utf-8 -*-
"""往 Codex 桌面 App 正在跑的会话里推消息（App 里看得见），并把回复捞回来。"""
import confluxconfig as cfg
import glob
import json
import os
import re
import subprocess
import threading
import time

BASE = os.path.join(os.environ["LOCALAPPDATA"], "OpenAI", "Codex", "bin")
SESSIONS = os.path.join(os.environ["USERPROFILE"], ".codex", "sessions")
BINDING = os.path.join(os.environ.get("FEIYU_ROOT", cfg.root_path('')), "data", "hub", "codex-session.json")
_QUEUE_LOCK = threading.Lock()


def find_codex():
    cands = glob.glob(os.path.join(BASE, "*", "codex.exe"))
    return max(cands, key=os.path.getmtime) if cands else "codex"


def latest_rollout():
    files = glob.glob(os.path.join(SESSIONS, "**", "rollout-*.jsonl"), recursive=True)
    if not files:
        return None, None
    p = max(files, key=lambda x: os.path.basename(x))
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", os.path.basename(p))
    thread = m.group(1) if m else None
    if p and thread:
        bind(p, thread)
    return p, thread


def bind(path, thread):
    os.makedirs(os.path.dirname(BINDING), exist_ok=True)
    tmp = BINDING + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"path": path, "thread": thread}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BINDING)


def _tlog(stage, t0):
    try:
        import json as _j
        p = os.path.join(cfg.root_path('data/hub'), "hub-timing.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(_j.dumps({"stage": stage, "ms": int((time.time() - t0) * 1000),
                              "ts": time.time()}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def ask_via_queue(text, timeout=90):
    """推进 App 的会话；能读到回复就返回，读不到返回空串。使用全局锁保证串行排队不串台。"""
    with _QUEUE_LOCK:
        return _ask_via_queue_locked(text, timeout=timeout)


def _ask_via_queue_locked(text, timeout=90):
    t0 = time.time()
    path, thread = latest_rollout()
    if not path or not thread:
        return ""
    start_idx = 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            start_idx = max(0, len(f.readlines()) - 2)
    except OSError:
        start_idx = 0
    try:
        result = subprocess.run([find_codex(), "queue", "--thread", thread, "--message", text],
                                capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            return ""
        bind(path, thread)
    except Exception:
        return ""

    _tlog("queued", t0)
    first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    marker = first_line[:60]
    seen_start = False
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(1)
        idx = -1
        lines = []
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            continue
        for i in range(start_idx, len(lines)):
            if marker and marker in lines[i]:
                idx = i
        if idx < 0:
            continue
        if not seen_start:
            _tlog("started", t0)
            seen_start = True
        for line in lines[idx + 1:]:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") == "response_item":
                p = o.get("payload") or {}
                if p.get("type") == "message" and p.get("role") == "assistant":
                    txt = "".join((c.get("text") or "") for c in p.get("content", []))
                    if txt.strip():
                        _tlog("generated", t0)
                        return txt.strip()
    return ""


if __name__ == "__main__":
    import sys
    print(ask_via_queue(sys.argv[1] if len(sys.argv) > 1 else "你好"))
