# -*- coding: utf-8 -*-
"""
跟本地豆包桌面端（E:\\Doubao\\app\\Doubao.exe）对话：
通过 Chromium CDP (127.0.0.1:9229) 操控豆包聊天页 DOM 收发消息。

用法：
    python doubao_bridge.py say "你好"
    python doubao_bridge.py send "你好"
    python doubao_bridge.py read
"""
import confluxconfig as cfg
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import websocket

DOUBAO_EXE = cfg.optional_env("CONFLUX_DOUBAO_EXE", r"E:\Doubao\app\Doubao.exe")
CDP_PORT = 9229
_LOCK = threading.Lock()
_CDP_CACHE = {"port": 0, "ts": 0.0}
_CDP_FAST_PORTS = [9229, 9222, 9333, 9334, 9444, 9555, 55070, 60416, 60417]


def _probe_cdp(port, timeout=1.0):
    try:
        url = f"http://127.0.0.1:{port}/json/version"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return b"Browser" in r.read()
    except Exception:
        return False


def find_cdp():
    """先查缓存(5分钟)、再快速扫常见端口，最后才慢速查 Doubao 实际监听端口。"""
    cached = int(_CDP_CACHE.get("port") or 0)
    if cached and (time.time() - float(_CDP_CACHE.get("ts") or 0) < 300) and _probe_cdp(cached, 0.4):
        return cached
    for p in _CDP_FAST_PORTS:
        if p != cached and _probe_cdp(p, timeout=0.25):
            _CDP_CACHE["port"], _CDP_CACHE["ts"] = p, time.time()
            return p
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$p=Get-Process Doubao -EA SilentlyContinue|% Id;"
             "Get-NetTCPConnection -State Listen -EA SilentlyContinue |"
             "Where-Object {$p -contains $_.OwningProcess -and $_.LocalAddress -eq '127.0.0.1'} |"
             "Select-Object -ExpandProperty LocalPort -Unique"],
            capture_output=True, creationflags=0x08000000, timeout=8
        ).stdout.decode("utf-8", "ignore")
        for line in out.splitlines():
            p = int(line.strip() or 0)
            if p and _probe_cdp(p, timeout=0.5):
                _CDP_CACHE["port"], _CDP_CACHE["ts"] = p, time.time()
                return p
    except Exception:
        pass
    return CDP_PORT


def _cdp_targets(timeout=2.0):
    url = f"http://127.0.0.1:{find_cdp()}/json/list"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def ensure_cdp(wait_sec=15):
    """确保豆包桌面端以 --remote-debugging-port=9229 运行。"""
    try:
        targets = _cdp_targets(timeout=1.5)
        if targets:
            return True
    except Exception:
        pass

    if not os.path.exists(DOUBAO_EXE):
        return False

    # 若豆包已运行但未开 9229 端口，先关闭再以 CDP 模式在交互式桌面拉起
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process Doubao -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"],
        capture_output=True, creationflags=0x08000000
    )
    time.sleep(1.5)
    cmd = f'"{DOUBAO_EXE}" --remote-debugging-port={CDP_PORT} --remote-allow-origins=*'
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
            page_targets = [t for t in targets if t.get("type") == "page" and "background" not in (t.get("url") or "")]
            if page_targets:
                time.sleep(1.0)
                return True
        except Exception:
            pass
    return False


def _pick_chat_target():
    if not ensure_cdp():
        raise RuntimeError(f"豆包桌面端 CDP (127.0.0.1:{find_cdp()}) 未就绪")
    targets = _cdp_targets(timeout=3.0)
    for t in targets:
        if t.get("type") == "page" and "doubao-chat/chat" in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
            return t
    for t in targets:
        if t.get("type") == "page" and "background" not in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
            return t
    raise RuntimeError("未找到豆包聊天页面 target")


class _CDPSession:
    def __init__(self, ws_url, timeout=15):
        self.ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
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
        try:
            self.ws.close()
        except Exception:
            pass


def _get_state(sess):
    return sess.eval(r"""
    (() => {
        function rawMarkdownFromNode(el) {
            if (!el) return null;
            for (const k of Object.keys(el)) {
                if (k.startsWith('__reactFiber')) {
                    let cur = el[k];
                    for (let d = 0; d < 16 && cur; d++) {
                        const p = cur.memoizedProps || cur.pendingProps;
                        if (p && typeof p === 'object') {
                            if (typeof p.markDown === 'string' && p.markDown.trim()) {
                                return {text: p.markDown.trim(), streaming: !!p.streaming};
                            }
                            if (typeof p.text === 'string' && p.text.trim()) {
                                return {text: p.text.trim(), streaming: !!p.streaming};
                            }
                        }
                        cur = cur.return;
                    }
                }
            }
            return null;
        }
        const recvs = Array.from(document.querySelectorAll('[data-testid="receive_message"]'));
        const last = recvs.length ? recvs[recvs.length - 1] : null;
        let lastText = '';
        let doneActions = false;
        let fiberStreaming = false;
        if (last) {
            const contentNodes = Array.from(last.querySelectorAll('[data-testid="message_text_content"], [data-testid="message_content"]'));
            const parts = [];
            for (const cn of contentNodes) {
                if (cn.getAttribute('data-testid') === 'message_text_content') {
                    const rf = rawMarkdownFromNode(cn);
                    if (rf && rf.text) {
                        parts.push(rf.text);
                        if (rf.streaming) fiberStreaming = true;
                        continue;
                    }
                }
            }
            if (parts.length) {
                lastText = parts.join('\n\n').trim();
            } else if (contentNodes.length) {
                lastText = (contentNodes[contentNodes.length - 1].innerText || '').trim();
            } else {
                lastText = (last.innerText || '').trim();
            }
            doneActions = !!(last.querySelector('[data-testid="message_action_copy"]') || last.querySelector('[data-testid="message_action_regenerate"]'));
        }
        const breakBtn = document.querySelector('[data-testid="chat_input_local_break_button"]');
        const isGenerating = !!(breakBtn && breakBtn.offsetParent !== null) || fiberStreaming;
        return {
            count: recvs.length,
            lastText,
            doneActions,
            isGenerating
        };
    })()
    """) or {"count": 0, "lastText": "", "doneActions": False, "isGenerating": False}


def read():
    target = _pick_chat_target()
    sess = _CDPSession(target["webSocketDebuggerUrl"])
    try:
        st = _get_state(sess)
        return st.get("lastText") or ""
    finally:
        sess.close()


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


def send(text):
    target = _pick_chat_target()
    sess = _CDPSession(target["webSocketDebuggerUrl"])
    try:
        ok_focus = sess.eval(r"""
        (() => {
            const ed = document.querySelector('[data-testid="chat_input"] .tiptap.ProseMirror') || document.querySelector('.tiptap.ProseMirror');
            if (!ed) return false;
            ed.focus();
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(ed);
            sel.removeAllRanges();
            sel.addRange(range);
            document.execCommand('delete', false, null);
            return true;
        })()
        """)
        if not ok_focus:
            return False, "未找到豆包输入框 (.tiptap.ProseMirror)"

        sess.call("Input.insertText", {"text": text})
        time.sleep(0.25)

        sent = sess.eval(r"""
        (() => {
            const btn = document.querySelector('[data-testid="chat_input_send_button"]');
            if (btn && !btn.disabled) {
                btn.click();
                return "click";
            }
            const ed = document.querySelector('[data-testid="chat_input"] .tiptap.ProseMirror') || document.querySelector('.tiptap.ProseMirror');
            if (ed) {
                ed.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
                return "enter";
            }
            return "";
        })()
        """)
        return bool(sent), sent or "发送按钮不可用"
    finally:
        sess.close()


def ask(text, timeout=180, on_beat=None):
    """向豆包发送消息并等待完整回复返回；on_beat 用于生成期实时回调心跳。"""
    with _LOCK:
        target = _pick_chat_target()
        sess = _CDPSession(target["webSocketDebuggerUrl"])
        try:
            before = _get_state(sess)
            before_count = before.get("count", 0)
            before_text = before.get("lastText", "")

            ok_focus = sess.eval(r"""
            (() => {
                const ed = document.querySelector('[data-testid="chat_input"] .tiptap.ProseMirror') || document.querySelector('.tiptap.ProseMirror');
                if (!ed) return false;
                ed.focus();
                const sel = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(ed);
                sel.removeAllRanges();
                sel.addRange(range);
                document.execCommand('delete', false, null);
                return true;
            })()
            """)
            if not ok_focus:
                return False, "未找到豆包输入框"

            sess.call("Input.insertText", {"text": text})
            time.sleep(0.25)
            sent = sess.eval(r"""
            (() => {
                const btn = document.querySelector('[data-testid="chat_input_send_button"]');
                if (btn && !btn.disabled) {
                    btn.click();
                    return "click";
                }
                const ed = document.querySelector('[data-testid="chat_input"] .tiptap.ProseMirror') || document.querySelector('.tiptap.ProseMirror');
                if (ed) {
                    ed.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
                    return "enter";
                }
                return "";
            })()
            """)
            if not sent:
                return False, "豆包发送按钮未激活"

            deadline = time.time() + timeout
            last_txt = ""
            stable_since = None
            started_new = False
            start_wait = time.time() + 45        # 45s 内没有任何生成迹象 = 未响应（长指令起笔慢，15s 太紧）
            last_beat = 0.0
            last_growth = time.time()
            _beat = on_beat or (lambda msg: None)

            while time.time() < deadline:
                time.sleep(0.8)
                st = _get_state(sess)
                cnt = st.get("count", 0)
                cur_txt = (st.get("lastText") or "").strip()
                is_gen = st.get("isGenerating", False)
                done_act = st.get("doneActions", False)

                if cnt > before_count or (cur_txt and cur_txt != before_text):
                    started_new = True

                if not started_new:
                    if time.time() > start_wait:
                        return False, "未响应超时（45s 内未进入生成态）"
                    continue

                if cur_txt and cur_txt != last_txt:
                    last_txt = cur_txt
                    stable_since = time.time()
                    last_growth = time.time()
                    if time.time() - last_beat > 2:
                        last_beat = time.time()
                        _beat("生成中（%d 字）" % len(cur_txt))
                    continue

                # 有生成态却长时间没有新字数：判生成中断，别挂到总超时
                if is_gen and time.time() - last_growth > 90:
                    return False, "生成中断超时（停滞 90s）"

                if last_txt:
                    if done_act and not is_gen and (time.time() - (stable_since or time.time()) >= 0.8):
                        return True, last_txt
                    if not is_gen and stable_since and (time.time() - stable_since >= 2.5):
                        return True, last_txt

            if last_txt:
                return True, last_txt
            return False, "等待豆包回复超时"
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
            print(f"（豆包未回复：{reply}）", file=sys.stderr)
            sys.exit(1)
