# -*- coding: utf-8 -*-
"""Google Vertex AI 客户端（走本地 gcloud ADC 凭据，代理出网）。

用法：
  python vertex_client.py "你好"
  python vertex_client.py --model gemini-4-argon --max 512 "把这段话总结成一句"
  from vertex_client import chat
"""
import confluxconfig as cfg
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ADC = os.path.join(os.environ["APPDATA"], "gcloud", "application_default_credentials.json")
TOKEN_CACHE = cfg.root_path('data/secrets/vertex.token')
TOKEN_TS = cfg.root_path('data/secrets/vertex.token.ts')
PROJECT = "gen-lang-client-0339253383"
# Vertex Express API Key（AQ. 开头）只对 publishers/google 生效，且必须配它自己的项目；
# 默认走 ADC/Bearer，需要时用环境变量 VERTEX_USE_KEY=1 打开。
API_KEY_FILE = cfg.root_path('data/secrets/vertex.key')
API_PROJECT = os.environ.get("VERTEX_API_PROJECT", PROJECT)
USE_API_KEY = os.environ.get("VERTEX_USE_KEY", "").strip().lower() in ("1", "true", "yes")
LOCATIONS = ("global",)   # 依次尝试：新加坡通常 6s，global 经常超时
DEFAULT_MODEL = "gemini-4-argon"
DEFAULT_THINKING = "low"      # 架构师档位：low 省钱，需要深想再调 high
MODELS = {"3.8": "gemini-3.8-flash", "argon": "gemini-4-argon",
          "4": "gemini-4-argon", "flash": "gemini-3.8-flash"}
# 代理改为跟随系统环境变量（不再硬编码 v2rayN 的 10808 端口）
def _alive(host, port, timeout=0.3):
    import socket as _sk
    try:
        with _sk.socket() as _s:
            _s.settimeout(timeout)
            return _s.connect_ex((host, port)) == 0
    except Exception:
        return False


def _proxy_url():
    """按可用性选代理：环境变量 → 系统代理 → 常见端口探测；全部要求端口能连通。"""
    import os as _os
    cands = []
    # 0) 显式配置优先（data/hub/proxy.txt，格式 host:port）
    try:
        _explicit = open(cfg.root_path('data/hub/proxy.txt'), encoding="utf-8").read().strip()
        if _explicit:
            cands.append(_explicit if _explicit.startswith("http") else ("http://" + _explicit))
    except Exception:
        pass
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = _os.environ.get(k)
        if v:
            cands.append(v)
    try:
        import winreg
        kk = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings")
        if winreg.QueryValueEx(kk, "ProxyEnable")[0]:
            srv = str(winreg.QueryValueEx(kk, "ProxyServer")[0] or "")
            if "=" in srv and "http=" in srv:
                srv = srv.split("http=")[1].split(";")[0]
            if srv:
                cands.append("http://" + srv)
    except Exception:
        pass
    for port in (7897, 7890, 10809, 10808, 2080):
        cands.append("http://127.0.0.1:%d" % port)
    for c in cands:
        try:
            hp = c.split("//")[-1].split("/")[0]
            host, port = hp.split(":")[0], int(hp.split(":")[1])
        except Exception:
            continue
        if _alive(host, port):
            return c
    return ""

def _opener():
    import urllib.request as _ur
    p = _proxy_url()
    if p:
        return _ur.build_opener(_ur.ProxyHandler({"https": p, "http": p}))
    return _ur.build_opener()
def token(force=False):
    """缓存 token（50 分钟），过期自动刷新。"""
    try:
        if not force:
            ts = float(open(TOKEN_TS, encoding="utf-8").read().strip())
            if time.time() - ts < 3000:
                return open(TOKEN_CACHE, encoding="utf-8").read().strip()
    except Exception:
        pass
    d = json.load(open(ADC, encoding="utf-8"))
    data = urllib.parse.urlencode({"client_id": d["client_id"], "client_secret": d["client_secret"],
                                   "refresh_token": d["refresh_token"],
                                   "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    r = json.loads(_opener().open(req, timeout=30).read())
    tok = r["access_token"]
    os.makedirs(os.path.dirname(TOKEN_CACHE), exist_ok=True)
    open(TOKEN_CACHE, "w", encoding="utf-8").write(tok)
    open(TOKEN_TS, "w", encoding="utf-8").write(str(time.time()))
    return tok


def chat(prompt, model=DEFAULT_MODEL, max_tokens=1024, system=None, timeout=120,
         thinking=DEFAULT_THINKING, search=False):
    name = MODELS.get(model, model)
    api_key = ""
    if USE_API_KEY:
        try:
            with open(API_KEY_FILE, encoding="utf-8") as f:
                api_key = f.read().strip()
        except OSError:
            api_key = ""
    gen = {"maxOutputTokens": max_tokens}
    if thinking:
        gen["thinkingConfig"] = {"thinkingLevel": thinking}
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
               "generationConfig": gen}
    if search:
        payload["tools"] = [{"google_search": {}}]
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    body = json.dumps(payload).encode()
    last = None
    r = None
    cand = {}
    for attempt_round in range(2):
        for loc in LOCATIONS:
            host = "aiplatform.googleapis.com" if loc == "global" else "%s-aiplatform.googleapis.com" % loc
            if api_key:
                url = ("https://%s/v1/projects/%s/locations/%s/publishers/google/models/%s:generateContent?key=%s"
                       % (host, API_PROJECT, loc, name, api_key))
                headers = {"Content-Type": "application/json"}
            else:
                url = ("https://%s/v1/projects/%s/locations/%s/publishers/google/models/%s:generateContent"
                       % (host, PROJECT, loc, name))
                headers = {"Authorization": "Bearer " + token(), "Content-Type": "application/json"}
            try:
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                r = json.loads(_opener().open(req, timeout=min(timeout, 90)).read())
                break
            except Exception as e:
                last = e
                r = None
                if "429" in str(e):
                    # DSQ 瞬时拥堵：按 2/4/8 秒退避重试，最多 3 次（Google 官方建议）
                    for _wait in (2, 4, 8):
                        time.sleep(_wait)
                        try:
                            req2 = urllib.request.Request(url, data=body, method="POST", headers=headers)
                            r = json.loads(_opener().open(req2, timeout=min(timeout, 90)).read())
                            break
                        except Exception as e2:
                            last = e2
                            r = None
                            if "429" not in str(e2):
                                break
                    if r is not None:
                        break
                    continue
                if "handshake" in str(e) or "timed out" in str(e):
                    time.sleep(1.5)
        if r is not None:
            break
    if r is None:
        raise last
    cand = (r.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return {"text": text, "model": name, "grounded": bool(cand.get("groundingMetadata")),
            "finish": cand.get("finishReason"),
            "usage": r.get("usageMetadata") or {}}




def chat_contents(contents, tools=None, model=DEFAULT_MODEL, max_tokens=1500,
                  system=None, timeout=180, thinking=DEFAULT_THINKING):
    """低层调用：支持 function calling 的多轮 contents。"""
    name = MODELS.get(model, model)
    api_key = ""
    if USE_API_KEY:
        try:
            with open(API_KEY_FILE, encoding="utf-8") as f:
                api_key = f.read().strip()
        except OSError:
            pass
    gen = {"maxOutputTokens": max_tokens}
    if thinking:
        gen["thinkingConfig"] = {"thinkingLevel": thinking}
    payload = {"contents": contents, "generationConfig": gen}
    if tools:
        payload["tools"] = [{"functionDeclarations": tools}]
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    body = json.dumps(payload).encode()
    last = None
    r = None
    for _round in range(2):
        for loc in LOCATIONS:
            host = "aiplatform.googleapis.com" if loc == "global" else "%s-aiplatform.googleapis.com" % loc
            if api_key:
                url = ("https://%s/v1/projects/%s/locations/%s/publishers/google/models/%s:generateContent?key=%s"
                       % (host, API_PROJECT, loc, name, api_key))
                headers = {"Content-Type": "application/json"}
            else:
                url = ("https://%s/v1/projects/%s/locations/%s/publishers/google/models/%s:generateContent"
                       % (host, PROJECT, loc, name))
                headers = {"Authorization": "Bearer " + token(), "Content-Type": "application/json"}
            try:
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                r = json.loads(_opener().open(req, timeout=min(timeout, 120)).read())
                break
            except Exception as e:
                last = e
                r = None
                if "429" in str(e):
                    for _wait in (2, 4, 8):
                        time.sleep(_wait)
                        try:
                            req2 = urllib.request.Request(url, data=body, method="POST", headers=headers)
                            r = json.loads(_opener().open(req2, timeout=min(timeout, 120)).read())
                            break
                        except Exception as e2:
                            last = e2
                            r = None
                            if "429" not in str(e2):
                                break
                    if r is not None:
                        break
                    continue
        if r is not None:
            break
    if r is None:
        raise last
    return (r.get("candidates") or [{}])[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--thinking", default=DEFAULT_THINKING)
    ap.add_argument("--max", type=int, default=1024)
    a = ap.parse_args()
    out = chat(a.prompt, a.model, a.max, thinking=a.thinking)
    print(out["text"] or "(空回复)")
    u = out["usage"]
    print("\n[%s | in %s / out %s | %s]" % (out["model"], u.get("promptTokenCount"),
                                            u.get("candidatesTokenCount"), out["finish"]),
          file=sys.stderr)


if __name__ == "__main__":
    main()
