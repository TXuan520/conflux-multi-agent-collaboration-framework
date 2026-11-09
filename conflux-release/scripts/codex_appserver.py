# -*- coding: utf-8 -*-
"""
跟 Codex 的 app-server 常驻进程说话：一次启动，之后每条消息一个 turn。
用来替代 codex exec 的冷启动，把每轮耗时压到几秒。

用法（单独测）：
    python codex_appserver.py "你好"
"""
import confluxconfig as cfg
import routing_config as bootstrap
import glob
import json
import os
import subprocess
import sys
import threading
import queue
import time

BASE = os.path.join(os.environ["LOCALAPPDATA"], "OpenAI", "Codex", "bin")


def find_codex():
    cands = glob.glob(os.path.join(BASE, "*", "codex.exe"))
    if not cands:
        raise RuntimeError("找不到 codex.exe")
    return max(cands, key=os.path.getmtime)






def _should_rotate(thread_id, limit=14 * 1024 * 1024):
    try:
        import glob as _g
        pat = os.path.expanduser("~/.codex/sessions/**/rollout-*" + thread_id + "*.jsonl")
        files = _g.glob(pat, recursive=True)
        return bool(files) and os.path.getsize(files[0]) > limit
    except Exception:
        return False


def _brief():
    try:
        with open(cfg.expand('${CONFLUX_WORKSPACE}/CONTEXT.md'), encoding="utf-8") as f:
            return "【项目速览·事实来源，别翻文件】\n" + f.read()[:3000] + "\n\n"
    except Exception:
        return ""


from agent_activity import reported

class CodexServer:
    def __init__(self, cwd=cfg.WORKSPACE, model="gpt-6.1-sol"):
        self.cwd = cwd
        self.model = model
        self.proc = None
        self.thread_id = None
        self._id = 0
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._replies = []
        self._started = False

    # ---------- 基础 ----------
    def _send(self, obj):
        if not self.proc or self.proc.poll() is not None:
            self.start()
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _req(self, method, params):
        with self._lock:
            self._id += 1
            rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid

    def _reader(self):
        for raw in iter(self.proc.stdout.readline, b""):
            try:
                o = json.loads(raw)
            except Exception:
                continue
            self._q.put(o)

    def _pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                o = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            yield o

    def _wait_result(self, rid, timeout=60):
        for o in self._pump(timeout):
            if o.get("id") == rid:
                if "error" in o:
                    raise RuntimeError(json.dumps(o["error"], ensure_ascii=False)[:300])
                return o.get("result")
        raise TimeoutError("等 %s 回包超时" % rid)

    # ---------- 生命周期 ----------
    def start(self):
        bootstrap.sync_workspace_rules()
        env = dict(os.environ)
        try:
            pxy = open(cfg.root_path('data/hub/proxy.txt'), encoding="utf-8").read().strip()
        except Exception:
            pxy = "127.0.0.1:7897"
        if pxy:
            if not pxy.startswith("http"):
                pxy = "http://" + pxy
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[k] = pxy
            env["NO_PROXY"] = "localhost,127.0.0.1,::1,api.deepseek.com,platform.xiaomimimo.com"
            env.pop("ALL_PROXY", None)
        self.proc = subprocess.Popen(
            [find_codex(), "app-server", "-c", 'model="%s"' % self.model, "-c", "features.respect_system_proxy=true"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._reader, daemon=True).start()

        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"clientInfo": {"name": "feiyu-hub", "title": "Feiyu Hub", "version": "0.1.0"}}})
        self._id = 1
        self._wait_result(1, 20)
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

        THREAD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "hub", "codex-thread.txt")
        saved = ""
        try:
            saved = open(THREAD_FILE, encoding="utf-8").read().strip()
        except OSError:
            saved = ""

        snap = bootstrap.snapshot("codex")
        version = snap["version"]
        self._bootstrap_version = version
        if saved and _should_rotate(saved):
            saved = ""
        if saved:
            try:
                rid = self._req("thread/resume", {
                    "threadId": saved,
                    "cwd": self.cwd,
                    "model": self.model,
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                    "developerInstructions": snap["prompt"],
                })
                res = self._wait_result(rid, 30)
                th = (res or {}).get("thread") or {}
                self.thread_id = th.get("id") or saved
                self._started = True
                try:
                    open(THREAD_FILE + ".bootstrap", "w", encoding="utf-8").write(version)
                except OSError:
                    pass
                return self.thread_id
            except Exception as error:
                # A transient resume failure must not silently fork the conversation.
                self.stop()
                raise RuntimeError('Codex 原会话恢复失败，保留指针供重试') from error

        rid = self._req("thread/start", {
            "cwd": self.cwd,
            "model": self.model,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "developerInstructions": snap["prompt"],
        })
        res = self._wait_result(rid, 30)
        thread = (res or {}).get("thread") or {}
        self.thread_id = thread.get("id")
        if not self.thread_id:
            raise RuntimeError("thread/start 没返回 thread id: " + json.dumps(res, ensure_ascii=False)[:300])
        self._started = True
        try:
            os.makedirs(os.path.dirname(THREAD_FILE), exist_ok=True)
            open(THREAD_FILE, "w", encoding="utf-8").write(self.thread_id)
            open(THREAD_FILE + ".bootstrap", "w", encoding="utf-8").write(version)
        except OSError:
            pass
        return self.thread_id

    def stop(self):
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
        self.proc = None
        self._started = False

    # ---------- 一轮对话 ----------
    @reported("codex")
    def ask(self, text, timeout=180):
        with self._turn_lock:
            return self._ask_locked(text, timeout=timeout)

    def _ask_locked(self, text, timeout=180):
        bootstrap.sync_workspace_rules()
        snap = bootstrap.snapshot("codex")
        if not self._started or not self.proc or self.proc.poll() is not None:
            self.start()
        elif getattr(self, "_bootstrap_version", None) != snap["version"]:
            # Refresh real rule changes on the SAME thread, without restarting the server.
            rid = self._req("thread/resume", {
                "threadId": self.thread_id, "cwd": self.cwd, "model": self.model,
                "approvalPolicy": "never", "sandbox": "danger-full-access",
                "developerInstructions": snap["prompt"],
            })
            self._wait_result(rid, 30)
            self._bootstrap_version = snap["version"]
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        rid = self._req("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text}],
        })
        # 等 turn/start 回执
        self._wait_result(rid, 30)

        chunks = []
        end = time.time() + timeout
        while time.time() < end:
            try:
                o = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            m = o.get("method") or ""
            p = o.get("params") or {}
            if m == "item/agentMessage/delta":
                t = p.get("delta")
                if isinstance(t, str):
                    chunks.append(t)
            if m.endswith("turn/completed") or m == "turn/completed":
                turn = p.get("turn") or {}
                items = turn.get("items") or []
                final_ans = ""
                for it in items:
                    if it.get("type") == "agentMessage" and it.get("phase") == "final_answer":
                        final_ans = it.get("text") or ""
                if not final_ans:
                    for it in reversed(items):
                        if it.get("type") == "agentMessage" and it.get("text"):
                            final_ans = it.get("text")
                            break
                return (final_ans or "".join(chunks)).strip()
            if m == "turn/error":
                raise RuntimeError(json.dumps(p, ensure_ascii=False)[:300])
        return ("".join(chunks)).strip() or "（超时没有回复）"


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "用一句话说你好"
    s = CodexServer()
    t0 = time.time()
    print("thread:", s.start(), " 启动耗时 %.1fs" % (time.time() - t0))
    t1 = time.time()
    print("回复:", s.ask(text))
    print("一轮耗时 %.1fs" % (time.time() - t1))
    s.stop()


if __name__ == "__main__":
    main()
