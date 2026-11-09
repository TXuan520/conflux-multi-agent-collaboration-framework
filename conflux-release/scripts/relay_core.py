# -*- coding: utf-8 -*-
"""手机中继 · 核心逻辑（本机可测，不依赖网络）。

只做协议里承诺的事：
- 设备令牌鉴权（可撤销）
- 请求幂等（同一 requestId 的 send 只写一次）
- 重放拒绝（5 分钟窗口）
- 上传校验（魔数 + 大小上限）
- 主机在线/离线判定

真实的网络层（WS/HTTP）以后接，这层不碰 socket，方便单测。
"""
import hashlib
import os
import struct
import time

WINDOW = 300          # 重放窗口 5 分钟
MAX_UPLOAD = 20 * 1024 * 1024
HEARTBEAT_TIMEOUT = 30
ALLOWED_OPS = ("messages", "send", "upload", "status")

MAGIC = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def sniff(raw):
    """按魔数判类型，WebP 需要看 RIFF....WEBP。"""
    for m, name in MAGIC.items():
        if raw.startswith(m):
            return name
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


class Relay:
    def __init__(self, now=None):
        self.devices = {}        # token_hash -> {"device_id":..., "revoked":False}
        self.used = {}           # request_id -> ts
        self.send_results = {}   # request_id -> result（幂等复用）
        self.last_heartbeat = 0.0
        self.now = now or time.time

    # ---------- 主机 ----------
    def heartbeat(self):
        self.last_heartbeat = self.now()

    def host_online(self):
        return (self.now() - self.last_heartbeat) <= HEARTBEAT_TIMEOUT

    # ---------- 设备 ----------
    def issue_device(self, token):
        h = hashlib.sha256(token.encode("utf-8")).hexdigest()
        dev = {"device_id": h[:16], "revoked": False}
        self.devices[h] = dev
        return dev["device_id"]

    def revoke(self, token):
        h = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if h in self.devices:
            self.devices[h]["revoked"] = True
            return True
        return False

    def _auth(self, token):
        h = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
        dev = self.devices.get(h)
        if not dev or dev["revoked"]:
            return None
        return dev

    # ---------- 请求 ----------
    def handle(self, token, op, payload=None, request_id=None, nonce=None):
        dev = self._auth(token)
        if not dev:
            return {"ok": False, "error": "unauthenticated"}
        if op not in ALLOWED_OPS:
            return {"ok": False, "error": "op_not_allowed"}
        if not request_id:
            return {"ok": False, "error": "missing_request_id"}
        # 每帧必须带 nonce，且至少 8 字节，防简单重放
        if not nonce or len(str(nonce)) < 8:
            return {"ok": False, "error": "missing_nonce"}
        now = self.now()
        # 先清理过期窗口，再查重
        for k, ts in list(self.used.items()):
            if now - ts > WINDOW:
                self.used.pop(k, None)
        for k, ts in list(self.send_results.items()):
            if isinstance(ts, dict) and now - ts.get("ts", 0) > WINDOW:
                self.send_results.pop(k, None)

        did = dev["device_id"]
        rid_key = (did, request_id)
        nonce_key = (did, "n:" + str(nonce))

        # 只有「同一 requestId 的幂等重试」可以复用 nonce，其它一律判重放
        if nonce_key in self.used:
            if not (op == "send" and rid_key in self.send_results):
                return {"ok": False, "error": "replay"}

        if op == "send" and rid_key in self.send_results:
            # 幂等：同样的 requestId 不再写第二次
            return {"ok": True, "duplicate": True, "result": self.send_results[rid_key]["result"]}
        if rid_key in self.used:
            return {"ok": False, "error": "replay"}

        if not self.host_online():
            return {"ok": False, "error": "host_offline"}

        if op == "send":
            text = (payload or {}).get("text") or ""
            if len(text) > 8000:
                return {"ok": False, "error": "text_too_long"}
            result = {"written": True, "device": dev["device_id"], "len": len(text)}
            self.send_results[rid_key] = {"ts": now, "result": result}
            self.used[rid_key] = now
            self.used[nonce_key] = now
            return {"ok": True, "duplicate": False, "result": result}

        if op == "upload":
            raw = (payload or {}).get("data") or b""
            if len(raw) > MAX_UPLOAD:
                return {"ok": False, "error": "too_large"}
            kind = sniff(raw)
            if not kind:
                return {"ok": False, "error": "bad_magic"}
            self.used[rid_key] = now
            self.used[nonce_key] = now
            return {"ok": True, "kind": kind, "size": len(raw)}

        self.used[rid_key] = now
        self.used[nonce_key] = now
        return {"ok": True, "op": op}
