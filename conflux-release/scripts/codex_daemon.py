# -*- coding: utf-8 -*-
"""常驻的 Codex 服务：起一次 app-server，之后用 HTTP 接活。
监听 127.0.0.1:8778，POST /ask {"text": "..."} -> {"reply": "..."}"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import codex_appserver as ca

LOCK = threading.Lock()
SERVER = None


def ensure():
    global SERVER
    if SERVER is None:
        SERVER = ca.CodexServer()
        SERVER.start()
    return SERVER


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            self._json({"error": "bad json"}, 400); return
        text = (body.get("text") or "").strip()
        if not text:
            self._json({"error": "empty"}, 400); return
        try:
            with LOCK:
                srv = ensure()
                reply = srv.ask(text, timeout=300)
            self._json({"reply": reply})
        except Exception as e:
            global SERVER
            try:
                if SERVER:
                    SERVER.stop()
            except Exception:
                pass
            SERVER = None
            self._json({"error": str(e)}, 500)


if __name__ == "__main__":
    threading.Thread(target=lambda: ensure(), daemon=True).start()
    print("codex daemon on 8778")
    ThreadingHTTPServer(("127.0.0.1", 8778), H).serve_forever()
