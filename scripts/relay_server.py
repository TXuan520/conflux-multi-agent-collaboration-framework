# -*- coding: utf-8 -*-
"""云端中继（跑在 VPS 上）。

- /host   WebSocket：电脑主机连进来，用 HMAC 挑战应答认证，之后收转发请求
- /api    HTTP：手机带着设备令牌来，只放行白名单操作，转发给主机并等结果
- /health 给手机看主机在不在线

只做转发，不落盘正文。
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid

from aiohttp import web, WSMsgType

HOST_SECRET = os.environ.get("RELAY_HOST_SECRET", "change-me")
DEVICE_TOKENS = set(filter(None, os.environ.get("RELAY_DEVICES", "").split(",")))
ALLOWED = ("messages", "send", "upload", "status", "blob", "push", "musiclist", "stt")
MAX_BODY = 22 * 1024 * 1024

host_ws = None
host_last_seen = 0.0
pending = {}
chunk_buf = {}
MUSIC_DIR = "/var/www/hub/music"
MAX_PUSH = 40 * 1024 * 1024          # request_id -> Future


def now():
    return time.time()


def host_alive():
    # 只看连接本身是否还活着，别用心跳时间戳判死（空闲时本来就没有消息）
    return host_ws is not None and not host_ws.closed


async def ws_handler(request):
    global host_ws, host_last_seen
    ws = web.WebSocketResponse(heartbeat=15, max_msg_size=32 * 1024 * 1024)
    await ws.prepare(request)

    # 握手：服务端先发挑战，客户端回 hmac(secret, challenge)
    challenge = uuid.uuid4().hex
    await ws.send_json({"type": "challenge", "nonce": challenge})
    msg = await ws.receive()
    if msg.type != WSMsgType.TEXT:
        await ws.close()
        return ws
    try:
        data = json.loads(msg.data)
    except Exception:
        await ws.close()
        return ws
    expect = hmac.new(HOST_SECRET.encode(), challenge.encode(), hashlib.sha256).hexdigest()
    if data.get("type") != "auth" or not hmac.compare_digest(str(data.get("answer", "")), expect):
        await ws.send_json({"type": "auth_failed"})
        await ws.close()
        return ws

    host_ws = ws
    host_last_seen = now()
    await ws.send_json({"type": "auth_ok"})

    async for m in ws:
        host_last_seen = now()   # 任何一条消息都算心跳
        if m.type == WSMsgType.TEXT:
            try:
                o = json.loads(m.data)
            except Exception:
                continue
            if o.get("type") == "pong":
                host_last_seen = now()
            elif o.get("type") == "chunk":
                rid = o.get("request_id")
                chunk_buf.setdefault(rid, []).append(o.get("data") or "")
            elif o.get("type") == "result":
                fut = pending.pop(o.get("request_id"), None)
                if fut and not fut.done():
                    fut.set_result(o.get("payload"))
        elif m.type == WSMsgType.ERROR:
            break

    # 只有当前这条连接才能把 host_ws 清掉，避免旧连接退出时踩掉新连接
    if host_ws is ws:
        host_ws = None
    return ws


def auth_device(request):
    tok = request.headers.get("X-Device-Token", "")
    return tok in DEVICE_TOKENS



def save_music(name, b64):
    import base64, os, re
    if not re.match(r"^[\w\-. ]{1,120}\.(mp3|m4a|flac|wav|ogg|aac)$", name, re.I):
        return {"ok": False, "error": "bad_name"}
    raw = base64.b64decode(b64)
    if len(raw) > MAX_PUSH:
        return {"ok": False, "error": "too_large"}
    os.makedirs(MUSIC_DIR, exist_ok=True)
    fp = os.path.join(MUSIC_DIR, name)
    with open(fp, "wb") as f:
        f.write(raw)
    try:
        os.chmod(fp, 0o644)
    except Exception:
        pass
    return {"ok": True, "url": "/music/" + name, "size": len(raw)}


async def api_relay(request):
    if not auth_device(request):
        return web.json_response({"ok": False, "error": "unauthenticated"}, status=401)
    if not host_alive():
        return web.json_response({"ok": False, "error": "host_offline"}, status=503)
    body = await request.json()
    op = body.get("op")
    if op not in ALLOWED:
        return web.json_response({"ok": False, "error": "op_not_allowed"}, status=400)
    rid = body.get("requestId") or uuid.uuid4().hex
    payload = body.get("payload") or {}

    fut = asyncio.get_event_loop().create_future()
    pending[rid] = fut
    try:
        await host_ws.send_json({"type": "call", "request_id": rid, "op": op, "payload": payload})
        timeout = 210 if op == "stt" else (120 if op == "push" else 15)
        result = await asyncio.wait_for(fut, timeout=timeout)
        if op == "push" and isinstance(result, dict) and result.get("ok"):
            parts = chunk_buf.pop(rid, [])
            b64 = "".join(parts) if parts else result.get("b64", "")
            result = save_music(result.get("name", ""), b64)
        return web.json_response({"ok": True, "result": result})
    except asyncio.TimeoutError:
        pending.pop(rid, None)
        return web.json_response({"ok": False, "error": "timeout"}, status=504)
    except Exception as e:
        pending.pop(rid, None)
        return web.json_response({"ok": False, "error": str(e)[:80]}, status=500)


async def health(request):
    return web.json_response({"host_online": host_alive(), "ts": now()})


def make_app():
    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_get("/host", ws_handler)
    app.router.add_post("/relay", api_relay)
    app.router.add_get("/health", health)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=int(os.environ.get("RELAY_PORT", "8788")))
