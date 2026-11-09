# -*- coding: utf-8 -*-
"""电脑端：主动连云端中继，把请求转给本机 hub。

用法：python relay_host.py ws://中继地址:8788/host
"""
import confluxconfig as cfg
import argparse
import asyncio
import hashlib
import hmac
import json
import os
import urllib.request

import hashlib as _hl
import hmac as _hm
import aiohttp
import hashlib as _h
import socket as _sk
import ssl as _ssl

# 本机请求一律绕过系统代理（系统代理会把 127.0.0.1 也转给 v2rayN）
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

HUB = "http://127.0.0.1:8777"
END_POINTS = {
    "messages": "/api/messages",
    "send": "/api/send",
    "status": "/api/status",
    "upload": "/api/upload",
    "stt": "/api/stt",
}


def verify_pin(host, port, pin):
    """自签证书阶段：用整张证书的 SHA256 指纹做钉扎。"""
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    with _sk.create_connection((host, port), timeout=20) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as ss:
            der = ss.getpeercert(binary_form=True)
    got = _h.sha256(der).hexdigest()
    if pin and got.lower() != pin.lower():
        raise SystemExit("证书指纹不匹配：期望 %s 实际 %s" % (pin, got))
    return got



ALLOW_DIRS = ("avatars", "img")


def music_list():
    import os as _os
    d = cfg.WORKSPACE
    out = []
    try:
        for e in _os.scandir(d):
            if e.is_file() and e.name.lower().endswith((".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac")):
                out.append({"name": e.name, "size": e.stat().st_size})
    except Exception:
        pass
    out.sort(key=lambda x: x["name"].lower())
    return {"songs": out}


def read_blob(path):
    """只允许 avatars / img 两个目录，返回 base64。"""
    import base64, os as _os
    name = _os.path.basename(str(path))
    # /avatar/x -> avatars/x ; /img/x -> img/x
    if str(path).startswith("/avatar/"):
        sub = "avatars"
        stem, ext = _os.path.splitext(name)
        if not ext:
            name = stem + ".jpg"
    elif str(path).startswith("/img/"):
        sub = "img"
    else:
        return {"ok": False, "error": "path_not_allowed"}
    fp = _os.path.join(cfg.root_path('data/hub'), sub, name)
    if not _os.path.exists(fp):
        stem, _ext = _os.path.splitext(fp)
        for alt in (".jpg", ".png", ".jpeg", ".webp"):
            if _os.path.exists(stem + alt):
                fp = stem + alt
                break
    try:
        with open(fp, "rb") as f:
            raw = f.read(8 * 1024 * 1024)
        return {"ok": True, "b64": base64.b64encode(raw).decode(), "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)[:60]}



MUSIC_DIR = cfg.WORKSPACE

def transcode(name):
    """压成 96k 的 m4a，体积降到三分之一左右，手机好放。"""
    import os as _os, subprocess, tempfile
    src = _os.path.join(MUSIC_DIR, name)
    out = _os.path.join(tempfile.gettempdir(), _os.path.splitext(name)[0] + "-96k.m4a")
    try:
        # 已经压过且比源文件新就直接复用，避免反复跑 ffmpeg
        if _os.path.exists(out) and _os.path.getmtime(out) >= _os.path.getmtime(src) and _os.path.getsize(out) > 1024:
            return out
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", src, "-b:a", "96k", "-vn", out],
                       capture_output=True, timeout=180,
                       stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if _os.path.exists(out) and _os.path.getsize(out) > 1024:
            return out
    except Exception:
        pass
    return None


def read_music(name):
    """读取音频文件，返回 base64（上限 40MB）。"""
    import base64, os as _os, re
    name = _os.path.basename(str(name))
    if not re.match(r"^[\w\-. ]{1,120}\.(mp3|m4a|flac|wav|ogg|aac)$", name, re.I):
        return {"ok": False, "error": "bad_name"}
    fp = _os.path.join(MUSIC_DIR, name)
    try:
        if _os.path.getsize(fp) > 40 * 1024 * 1024:
            return {"ok": False, "error": "too_large"}
        with open(fp, "rb") as f:
            raw = f.read()
        return {"ok": True, "name": name, "b64": base64.b64encode(raw).decode()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:60]}



def relay_sig(body_bytes):
    try:
        key = open(cfg.root_path('data/hub/relay-inject.key'), encoding="utf-8").read().strip()
        return _hm.new(key.encode(), body_bytes, _hl.sha256).hexdigest()
    except Exception:
        return ""

def call_hub(op, payload):
    if op not in END_POINTS or not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_operation_or_payload"}
    path = END_POINTS[op]
    url = HUB + path
    if op == "messages":
        since = payload.get("since", 0)
        url += "?since=%s" % since
        req = urllib.request.Request(url, method="GET")
    elif op == "status":
        req = urllib.request.Request(url, method="GET")
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8",
                                              "X-Trusted-Human": "1",
                                              "X-Relay-Sig": relay_sig(body)})
    try:
        with OPENER.open(req, timeout=210 if op == "stt" else 15) as r:
            return json.loads(r.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as e:
        if op == "status":
            # 老版本 hub 可能没有 /api/status，退回 /api/version 至少证明活着
            alt = urllib.request.Request(HUB + "/api/version", method="GET")
            with OPENER.open(alt, timeout=10) as r2:
                v = json.loads(r2.read().decode("utf-8", "ignore") or "{}")
            return {"ok": True, "fallback": "version", "version": v}
        try:
            result = json.loads(e.read().decode("utf-8"))
        except (ValueError, UnicodeError):
            result = {"error": "hub_http_error"}
        return dict(result, ok=False, status=e.code)


async def run(url, secret, pin=None):
    from urllib.parse import urlparse
    u = urlparse(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "wss" else 80)
    got = verify_pin(host, port, pin)
    print("证书指纹:", got)
    ssl_ctx = _ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = _ssl.CERT_NONE
    conn = aiohttp.TCPConnector(ssl=aiohttp.Fingerprint(bytes.fromhex(pin)) if pin else ssl_ctx)
    async with aiohttp.ClientSession(connector=conn) as s:
        async with s.ws_connect(url, heartbeat=15, max_msg_size=32 * 1024 * 1024) as ws:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                t = data.get("type")
                if t == "challenge":
                    ans = hmac.new(secret.encode(), str(data["nonce"]).encode(), hashlib.sha256).hexdigest()
                    await ws.send_json({"type": "auth", "answer": ans})
                elif t == "auth_failed":
                    print("主机认证失败，退出")
                    return
                elif t == "auth_ok":
                    print("已连上中继")
                elif t == "call":
                    op = data.get("op")
                    try:
                        payload = data.get("payload") or {}
                        if op == "musiclist":
                            result = await asyncio.to_thread(music_list)
                        elif op == "push":
                            pname = payload.get("name") or ""
                            small = await asyncio.to_thread(transcode, pname) if pname else None
                            if small:
                                import base64 as _b64
                                with open(small, "rb") as f:
                                    raw = f.read()
                                result = {"ok": True,
                                          "name": os.path.splitext(os.path.basename(pname))[0] + ".m4a",
                                          "b64": _b64.b64encode(raw).decode()}
                            else:
                                result = await asyncio.to_thread(read_music, pname)
                            if isinstance(result, dict) and result.get("ok"):
                                b64 = result.pop("b64", "")
                                step = 512 * 1024
                                for i in range(0, len(b64), step):
                                    await ws.send_json({"type": "chunk",
                                                        "request_id": data.get("request_id"),
                                                        "data": b64[i:i+step]})
                                result["chunks"] = (len(b64) + step - 1) // step
                        elif op == "blob":
                            result = await asyncio.to_thread(read_blob, payload.get("path"))
                        else:
                            result = await asyncio.to_thread(call_hub, op, payload)
                    except Exception as e:
                        result = {"ok": False, "error": str(e)[:100]}
                    await ws.send_json({"type": "result",
                                        "request_id": data.get("request_id"),
                                        "payload": result})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="ws://127.0.0.1:8788/host")
    ap.add_argument("--secret", default="change-me")
    ap.add_argument("--pin", default=None)
    a = ap.parse_args()
    if True:
        # 断线自动重连，服务端重启也不用人管
        import time as _t
        while True:
            try:
                asyncio.run(run(a.url, a.secret, a.pin))
            except KeyboardInterrupt:
                break
            except Exception as e:
                print("断开，5 秒后重连:", str(e)[:80], flush=True)
            _t.sleep(5)


if __name__ == "__main__":
    main()
