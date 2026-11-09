# -*- coding: utf-8 -*-
"""
跟本地 WorkBuddy 桌面端（E:\\workbuddy\\WorkBuddyAI\\WorkBuddyAI.exe）对话：
通过 Chromium CDP (127.0.0.1:9230) 操控 WorkBuddy Slate 编辑器与消息流收发消息。

用法：
    python workbuddy_bridge.py say "你好"
    python workbuddy_bridge.py send "你好"
    python workbuddy_bridge.py read
"""
import confluxconfig as cfg
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

WORKBUDDY_EXE = cfg.optional_env("CONFLUX_WORKBUDDY_EXE", r"E:\workbuddy\WorkBuddyAI\WorkBuddyAI.exe")
CDP_PORT = 9230
_LOCK = threading.Lock()


class SimpleWS:
    def __init__(self, ws_url, timeout=10.0):
        u = urllib.parse.urlparse(ws_url)
        host, port = u.hostname, u.port or 80
        path = u.path + ("?" + u.query if u.query else "")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        self.leftover = buf.split(b"\r\n\r\n", 1)[1]

    def settimeout(self, t):
        self.sock.settimeout(t)

    def _recv_exact(self, n):
        while len(self.leftover) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError()
            self.leftover += chunk
        res, self.leftover = self.leftover[:n], self.leftover[n:]
        return res

    def send(self, text):
        data = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        l = len(data)
        if l < 126:
            header.append(0x80 | l)
        elif l < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", l))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", l))
        header.extend(mask)
        masked = bytearray(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(header + masked)

    def recv(self):
        while True:
            b1, b2 = self._recv_exact(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x1:
                return payload.decode("utf-8", "ignore")
            elif opcode == 0x8:
                raise EOFError()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class _CDPSession:
    def __init__(self, ws_url, timeout=15):
        self.ws = SimpleWS(ws_url, timeout=timeout)
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})

    def eval(self, expr):
        res = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return (res.get("result") or {}).get("value")

    def close(self):
        self.ws.close()


_CDP_CACHE = {"port": 0, "ts": 0.0}
_CDP_FAST_PORTS = (9230, 9229, 9222, 9333, 9334, 9444, 9555)


def _probe_cdp(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=0.4) as response:
            targets = json.load(response)
        return any(t.get("type") == "page" and
                   ("workbuddy" in str(t.get("url", "")).lower() or
                    "workbuddy" in str(t.get("title", "")).lower()) for t in targets)
    except Exception:
        return False


def find_cdp():
    cached = int(_CDP_CACHE["port"])
    if cached and time.time() - _CDP_CACHE["ts"] < 300 and _probe_cdp(cached):
        return cached
    for port in dict.fromkeys((CDP_PORT, *_CDP_FAST_PORTS)):
        if _probe_cdp(port):
            _CDP_CACHE.update(port=port, ts=time.time())
            return port
    # Discover additional listeners owned by WorkBuddy after the quick scan.
    candidates = []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$wbIds=@(Get-Process WorkBuddyAI -ErrorAction SilentlyContinue | ForEach-Object Id); "
             "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
             "Where-Object {$wbIds -contains $_.OwningProcess -and $_.LocalAddress -eq '127.0.0.1'} | "
             "Select-Object -ExpandProperty LocalPort -Unique"],
            capture_output=True, encoding="utf-8", errors="ignore", timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        candidates.extend(int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit())
    except Exception:
        pass
    for port in dict.fromkeys(candidates):
        if _probe_cdp(port):
            _CDP_CACHE.update(port=port, ts=time.time())
            return port
    return CDP_PORT


def _cdp_targets(timeout=2.0):
    url = f"http://127.0.0.1:{find_cdp()}/json/list"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def ensure_cdp(wait_sec=15):
    """确保 WorkBuddy 桌面端以 --remote-debugging-port=9230 运行。"""
    try:
        targets = _cdp_targets(timeout=1.5)
        if targets:
            return True
    except Exception:
        pass

    if not os.path.exists(WORKBUDDY_EXE):
        return False

    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process WorkBuddyAI -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"],
        capture_output=True, creationflags=0x08000000
    )
    time.sleep(1.5)
    cmd = f'"{WORKBUDDY_EXE}" --remote-debugging-port={CDP_PORT} --remote-allow-origins=*'
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{cmd}'}} | Out-Null"],
        capture_output=True, creationflags=0x08000000
    )
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            targets = _cdp_targets(timeout=1.5)
            if targets:
                time.sleep(1.0)
                return True
        except Exception:
            pass
    return False


def _pick_chat_target():
    if not ensure_cdp():
        raise RuntimeError("WorkBuddy 桌面端 CDP (127.0.0.1:9230) 未就绪")
    targets = _cdp_targets(timeout=3.0)
    for t in targets:
        if t.get("type") == "page" and "app.asar" in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
            return t
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t
    raise RuntimeError("未找到 WorkBuddy 页面 target")


def _get_state(sess):
    return sess.eval(r"""
    (() => {
        const mds = Array.from(document.querySelectorAll('.cr-markdown'));
        const lastMd = mds.length ? mds[mds.length - 1] : null;
        let lastText = lastMd ? lastMd.innerText.trim() : "";
        
        // If not found in cr-markdown, inspect cr-agent processing text
        if (!lastText) {
            const agentProc = document.querySelector('.cr-agent--processing, .cr-agent');
            if (agentProc) {
                lastText = agentProc.innerText.trim();
            }
        }
        
        const hasFinished = document.querySelector('.conversation-finished-footer') !== null;
        const isGenerating = document.querySelector('.cr-agent--processing') !== null;
        
        return {
            count: mds.length,
            lastText: lastText,
            hasFinished: hasFinished,
            isGenerating: isGenerating
        };
    })()
    """) or {}


def session_key():
    target = _pick_chat_target()
    sess = _CDPSession(target["webSocketDebuggerUrl"])
    try:
        identity = sess.eval(r"""(() => {
            const first = document.querySelector('.conversation-history-navigation__item') ||
                          document.querySelector('[data-testid="message-list"] > *') ||
                          document.querySelector('main');
            if (first) {
                if (!first.dataset.confluxSession) first.dataset.confluxSession = crypto.randomUUID();
                return location.pathname + ':' + first.dataset.confluxSession;
            }
            return location.pathname;
        })()""")
        return target['id'] + ':' + str(identity)
    finally:
        sess.close()


def read():
    """读取 WorkBuddy 当前页面上最新的 AI 回复。"""
    try:
        target = _pick_chat_target()
        sess = _CDPSession(target["webSocketDebuggerUrl"])
        try:
            st = _get_state(sess)
            return st.get("lastText", "")
        finally:
            sess.close()
    except Exception as e:
        return f"读取失败: {e}"


def send(text, check_premise=True):
    """向 WorkBuddy 填充文本并触发发送（不等待回复）。"""
    if check_premise:
        ensure_premise()
    import re as _r
    text = (text or "").strip()
    text = _r.sub(r"^(【[^】]+】\n*)>>>\s*[^\n]+\n*", r"\1", text)
    text = _r.sub(r"^>>>\s*[^\n]+\n*", "", text).strip()
    with _LOCK:
        target = _pick_chat_target()
        sess = _CDPSession(target["webSocketDebuggerUrl"])
        try:
            ok_insert = sess.eval(f"""
            (() => {{
                const editor = document.querySelector('.cr-input-container div[contenteditable="true"]');
                if (!editor) return false;
                editor.focus();
                
                let fiberKey = Object.keys(editor).find(k => k.startsWith('__reactFiber'));
                let curr = editor[fiberKey];
                let slate = null;
                while (curr) {{
                    if (curr.memoizedProps && curr.memoizedProps.value && curr.memoizedProps.value.insertText) {{
                        slate = curr.memoizedProps.value;
                        break;
                    }}
                    curr = curr.return;
                }}
                if (!slate) return false;
                
                try {{
                    slate.children = [{{ type: 'paragraph', children: [{{ text: '' }}] }}];
                    slate.selection = {{ anchor: {{ path: [0, 0], offset: 0 }}, focus: {{ path: [0, 0], offset: 0 }} }};
                    slate.insertText({json.dumps(text)});
                    slate.onChange();
                    return true;
                }} catch (e) {{
                    return false;
                }}
            }})()
            """)
            if not ok_insert:
                return False, "未能写入 WorkBuddy 编辑器"

            time.sleep(0.3)
            sent = sess.eval(r"""
            (() => {
                const btn = document.querySelector('.cr-input-container button.cr-send-button');
                if (btn && !btn.classList.contains('wb-button--disabled')) {
                    btn.click();
                    return "click";
                }
                const ed = document.querySelector('.cr-input-container div[contenteditable="true"]');
                if (ed) {
                    ed.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
                    return "enter";
                }
                return "";
            })()
            """)
            return bool(sent), sent or "发送按钮未触发"
        finally:
            sess.close()


PREMISE_TEXT = """【系统设定】
你是 Conflux 多智能体协作台的协作助手 WorkBuddy（混元驱动）。
团队成员包括：炫炫（管理员）、Argon（架构师）、DeepSeek（CEO/后端）、Gemini（COO/运维）、GPT（QA/验收）、MiMo（前端/文档）、Seedance（气氛组）。

协同纪律：
1. 你收到的协作消息格式为：【发件人】消息原文；
2. 直接针对消息内容做简明、高效的专业回复，绝不复述规则或格式指令；
3. 称呼炫炫为「炫炫」。

收到请用一句话确认已就绪。"""

_PREMISE_CHECKED = False
_PREMISE_LOCK = threading.Lock()


_BOOTSTRAP_STATE = {}


def ensure_premise():
    import routing_config as bootstrap
    bootstrap.ensure_session("workbuddy", session_key(),
                             lambda prompt: ask(prompt, timeout=180, check_premise=False)[0], _BOOTSTRAP_STATE)
    return True


def ask(text, timeout=180, check_premise=True, on_beat=None):
    """向 WorkBuddy 发送消息并等待完整回复返回。"""
    if check_premise:
        ensure_premise()
    with _LOCK:
        target = _pick_chat_target()
        sess = _CDPSession(target["webSocketDebuggerUrl"])
        try:
            before = _get_state(sess)
            before_count = before.get("count", 0)
            before_text = before.get("lastText", "")

            ok_insert = sess.eval(f"""
            (() => {{
                const editor = document.querySelector('.cr-input-container div[contenteditable="true"]');
                if (!editor) return false;
                editor.focus();
                
                let fiberKey = Object.keys(editor).find(k => k.startsWith('__reactFiber'));
                let curr = editor[fiberKey];
                let slate = null;
                while (curr) {{
                    if (curr.memoizedProps && curr.memoizedProps.value && curr.memoizedProps.value.insertText) {{
                        slate = curr.memoizedProps.value;
                        break;
                    }}
                    curr = curr.return;
                }}
                if (!slate) return false;
                
                try {{
                    slate.children = [{{ type: 'paragraph', children: [{{ text: '' }}] }}];
                    slate.selection = {{ anchor: {{ path: [0, 0], offset: 0 }}, focus: {{ path: [0, 0], offset: 0 }} }};
                    slate.insertText({json.dumps(text)});
                    slate.onChange();
                    return true;
                }} catch (e) {{
                    return false;
                }}
            }})()
            """)
            if not ok_insert:
                return False, "未能写入 WorkBuddy 编辑器"

            time.sleep(0.4)
            sent = sess.eval(r"""
            (() => {
                const btn = document.querySelector('.cr-input-container button.cr-send-button');
                if (btn && !btn.classList.contains('wb-button--disabled')) {
                    btn.click();
                    return "click";
                }
                const ed = document.querySelector('.cr-input-container div[contenteditable="true"]');
                if (ed) {
                    ed.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
                    return "enter";
                }
                return "";
            })()
            """)
            if not sent:
                return False, "WorkBuddy 发送动作未触发"

            deadline = time.time() + timeout
            last_txt = ""
            stable_since = None
            started_new = False

            while time.time() < deadline:
                time.sleep(1.0)
                st = _get_state(sess)
                cnt = st.get("count", 0)
                cur_txt = (st.get("lastText") or "").strip()
                is_gen = st.get("isGenerating", False)
                has_fin = st.get("hasFinished", False)

                if on_beat:
                    on_beat('等待 WorkBuddy 回复' if not is_gen else 'WorkBuddy 正在生成')
                # A new placeholder/footer may appear before its markdown arrives.
                # Never accept the previous answer merely because the count increased.
                if cur_txt and cur_txt != before_text:
                    started_new = True

                if not started_new or not cur_txt or cur_txt == before_text:
                    continue

                if cur_txt and cur_txt != last_txt:
                    last_txt = cur_txt
                    stable_since = time.time()
                    continue

                if last_txt:
                    if has_fin and not is_gen and (time.time() - (stable_since or time.time()) >= 1.0):
                        return True, last_txt
                    if not is_gen and stable_since and (time.time() - stable_since >= 3.0):
                        return True, last_txt

            if last_txt:
                return True, last_txt
            return False, "等待 WorkBuddy 回复超时"
        finally:
            sess.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    arg = " ".join(sys.argv[2:])
    if cmd == "send":
        ok, info = send(arg)
        print(json.dumps({"ok": ok, "info": info}, ensure_ascii=False))
    elif cmd == "read":
        print(read())
    elif cmd == "say":
        ok, reply = ask(arg)
        if ok:
            print(reply)
        else:
            print(f"（WorkBuddy 未回复：{reply}）", file=sys.stderr)
            sys.exit(1)
