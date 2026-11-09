# -*- coding: utf-8 -*-
"""
Conflux 通用客户端探针与自适应发现引擎 (client_probe.py)

探测本机已有的 5 大 AI 客户端：
- dsh (DeepSeek / OneBot)
- codex (OpenAI Codex CLI / App-Server)
- ag (Google Antigravity IDE / Language Server)
- doubao (字节跳动 豆包 桌面端 CDP 9229)
- workbuddy (腾讯混元 WorkBuddy 桌面端 CDP 9230)

支持：
1. 静态安装检测（注册表、常见安装路径、PATH、AppData）
2. 运行态端口与 CDP 握手探测（毫秒级缓存，避免卡顿）
3. 安全自愈与非侵入式拉起（支持自适应挂载调试端口）
4. 返回三态 runtime_status: "desktop_online" | "api_online" | "offline"
"""
import confluxconfig as cfg
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

_PROBE_CACHE = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 3.0  # 通道状态最多缓存三秒


def _is_port_open(port, timeout=0.25):
    if not port or port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def _probe_http_ok(url, keyword=None, timeout=0.5):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as resp:
            data = resp.read()
            if keyword:
                return keyword.encode("utf-8") in data
            return resp.status == 200
    except Exception:
        return False


def _find_exe(names, env_var=None, fallback_paths=None):
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val and os.path.isfile(env_val):
            return env_val
    for n in names:
        p = shutil.which(n)
        if p and os.path.isfile(p):
            return p
    if fallback_paths:
        for fb in fallback_paths:
            exp = os.path.expandvars(fb)
            if os.path.isfile(exp):
                return exp
    return None


_PROCESS_CACHE = {"checked": 0, "names": None, "pids": {}}
_PROCESS_LOCK = threading.Lock()


def _client_process_running(names):
    """Read the native Windows process snapshot without spawning a shell."""
    with _PROCESS_LOCK:
        if time.monotonic() - _PROCESS_CACHE["checked"] >= CACHE_TTL:
            found = None
            pids = {}
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                class ProcessEntry(ctypes.Structure):
                    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                                ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                                ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                                ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                                ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
                kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
                kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
                kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.CreateToolhelp32Snapshot(2, 0)
                if handle != ctypes.c_void_p(-1).value:
                    try:
                        entry = ProcessEntry()
                        entry.size = ctypes.sizeof(entry)
                        if kernel.Process32FirstW(handle, ctypes.byref(entry)):
                            found = set()
                            while True:
                                found.add(entry.exe.lower())
                                pids[entry.pid] = entry.exe.lower()
                                if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                                    break
                    finally:
                        kernel.CloseHandle(handle)
            _PROCESS_CACHE.update(checked=time.monotonic(), names=found, pids=pids)
        processes = _PROCESS_CACHE["names"]
        return None if processes is None else any(name.lower() in processes for name in names)


def _login_required(port):
    if not port:
        return False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=0.3) as response:
            targets = json.load(response)
        return any(target.get("type") == "page" and any(
            part in str(target.get("url") or "").lower()
            for part in ("onboarding", "login", "signin")) for target in targets)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _ag_login_required(cached_port):
    """Inspect only listeners owned by Antigravity, including a changed CDP port."""
    if _login_required(cached_port):
        return True
    if os.name != "nt":
        return False
    import ctypes
    import struct
    from ctypes import wintypes
    _client_process_running(("Antigravity.exe",))
    with _PROCESS_LOCK:
        pids = {pid for pid, name in _PROCESS_CACHE["pids"].items() if name == "antigravity.exe"}
    if not pids:
        return False
    api = ctypes.WinDLL("iphlpapi").GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD()
    if api(None, ctypes.byref(size), False, 2, 5, 0) not in (0, 122):
        return False
    buffer = ctypes.create_string_buffer(size.value)
    if api(buffer, ctypes.byref(size), False, 2, 5, 0):
        return False
    data = buffer.raw
    count = struct.unpack_from("I", data)[0]
    ports = set()
    for index in range(count):
        state, address, port, remote_address, remote_port, pid = struct.unpack_from("6I", data, 4 + index * 24)
        if state == 2 and pid in pids:
            ports.add(socket.ntohs(port & 0xffff))
    for port in ports:
        if port != cached_port and _login_required(port):
            _AG_DISCOVERY["cdp_port"] = port
            return True
    return False


def _desktop_diagnostic(process_running, login_required, ready):
    if login_required:
        return {"readiness_state": "login_required", "process_running": True,
                "authenticated": False, "detail": "客户端待登录"}
    if ready:
        return {"readiness_state": "ready", "process_running": process_running,
                "authenticated": True, "detail": "客户端通道就绪"}
    if process_running is False:
        return {"readiness_state": "process_missing", "process_running": False,
                "authenticated": None, "detail": "客户端进程未启动"}
    return {"readiness_state": "channel_unavailable", "process_running": process_running,
            "authenticated": None, "detail": "客户端通道未就绪，登录态未知"}


def _chat_target_ready(port, client):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=0.3) as response:
            targets = json.load(response)
        for target in targets:
            url = str(target.get("url") or "").lower()
            title = str(target.get("title") or "").lower()
            if target.get("type") != "page" or not target.get("webSocketDebuggerUrl"):
                continue
            if any(part in url for part in ("login", "signin", "background")):
                continue
            if client == "doubao" and ("doubao-chat/chat" in url or "doubao.com/chat" in url):
                return True
            if client == "workbuddy" and ("workbuddy" in url or ("app.asar" in url and "workbuddy" in title)):
                return True
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return False


_AG_DISCOVERY = {"ports": [], "csrf": "", "pending": False, "checked": 0}


def _refresh_ag_discovery():
    try:
        import antigravity_bridge as ag
        ports, csrf = ag.discover()
        cdp_port = ag.find_cdp()
        _AG_DISCOVERY.update(ports=ports, csrf=csrf, cdp_port=cdp_port)
    except Exception:
        _AG_DISCOVERY.update(ports=[], csrf="")
    finally:
        _AG_DISCOVERY.update(pending=False, checked=time.time())


# ---------------- 1. WorkBuddy 探针 ----------------
def probe_workbuddy():
    exe_fallbacks = [
        r"E:\workbuddy\WorkBuddyAI\WorkBuddyAI.exe",
        r"%LOCALAPPDATA%\Programs\WorkBuddyAI\WorkBuddyAI.exe",
        r"%PROGRAMFILES%\WorkBuddyAI\WorkBuddyAI.exe"
    ]
    exe = _find_exe(["WorkBuddyAI.exe", "WorkBuddy.exe"], "CONFLUX_WORKBUDDY_EXE", exe_fallbacks)
    installed = bool(exe)
    
    bridge = sys.modules.get("workbuddy_bridge")
    cached = getattr(bridge, "_CDP_CACHE", {}).get("port")
    ports = dict.fromkeys((int(cached or 9230), 9230))
    cdp_port = next((p for p in ports if _chat_target_ready(p, "workbuddy")), 9230)
    running = _is_port_open(cdp_port, timeout=0.15)
    cdp_ready = _chat_target_ready(cdp_port, "workbuddy") if running else False

    process_running = _client_process_running(("WorkBuddyAI.exe", "WorkBuddy.exe"))
    login_required = _login_required(cdp_port)
    cdp_ready = cdp_ready and not login_required
    return {
        **_desktop_diagnostic(process_running, login_required, cdp_ready),
        "id": "workbuddy",
        "display_name": "WorkBuddy",
        "installed": installed,
        "running": running,
        "cdp_ready": cdp_ready,
        "port": cdp_port,
        "exe": exe,
        "runtime_status": "desktop_online" if cdp_ready else ("desktop_launchable" if installed else "offline")
    }


# ---------------- 2. 豆包 (Seedance) 探针 ----------------
def probe_doubao():
    exe_fallbacks = [
        r"E:\Doubao\app\Doubao.exe",
        r"%LOCALAPPDATA%\Doubao\app\Doubao.exe",
        r"%PROGRAMFILES%\Doubao\app\Doubao.exe"
    ]
    exe = _find_exe(["Doubao.exe"], "CONFLUX_DOUBAO_EXE", exe_fallbacks)
    installed = bool(exe)
    
    bridge = sys.modules.get("doubao_bridge")
    cached = getattr(bridge, "_CDP_CACHE", {}).get("port")
    ports = dict.fromkeys((int(cached or 9229), 9229))
    cdp_port = next((p for p in ports if _chat_target_ready(p, "doubao")), 9229)
    running = _is_port_open(cdp_port, timeout=0.15)
    cdp_ready = _chat_target_ready(cdp_port, "doubao") if running else False

    process_running = _client_process_running(("Doubao.exe",))
    login_required = _login_required(cdp_port)
    cdp_ready = cdp_ready and not login_required
    return {
        **_desktop_diagnostic(process_running, login_required, cdp_ready),
        "id": "doubao",
        "display_name": "Seedance",
        "installed": installed,
        "running": running,
        "cdp_ready": cdp_ready,
        "port": cdp_port,
        "exe": exe,
        "runtime_status": "desktop_online" if cdp_ready else ("desktop_launchable" if installed else "offline")
    }


# ---------------- 3. Antigravity (Gemini) 探针 ----------------
def probe_antigravity():
    exe_fallbacks = [
        r"%LOCALAPPDATA%\Programs\Antigravity\resources\bin\language_server.exe",
        r"%LOCALAPPDATA%\Programs\antigravity\resources\bin\language_server.exe",
        r"%PROGRAMFILES%\Antigravity\resources\bin\language_server.exe"
    ]
    exe = _find_exe(["language_server.exe"], "CONFLUX_AG_LS", exe_fallbacks)
    installed = bool(exe)
    
    running = False
    ready = False
    ls_port = 0
    
    ports = _AG_DISCOVERY["ports"]
    live_ports = [port for port in ports if _is_port_open(port, timeout=0.15)]
    # CSRF 拿不到不代表不可用：只要 LS 端口活着、客户端进程在、且未待登录，就判就绪。
    # 之前要求 csrf 非空，导致 discover 抓不到令牌时整条通道被误报 channel_unavailable。
    ready = bool(live_ports)
    running = bool(live_ports)
    ls_port = live_ports[0] if live_ports else 0
    if not live_ports and _client_process_running(("language_server.exe", "Antigravity.exe", "antigravity.exe")):
        # discover 抓不到端口时（PowerShell 查询失败/超时）退回进程判据，避免误报离线
        running = True
        ready = True
    bridge = sys.modules.get("antigravity_bridge")
    cdp_port = (getattr(bridge, "_CDP_CACHE", {}).get("port") or _AG_DISCOVERY.get("cdp_port")
                or getattr(bridge, "CDP_PORT", 0))
    login_required = _ag_login_required(cdp_port)
    ready = ready and not login_required
    process_running = _client_process_running(("Antigravity.exe", "language_server.exe"))
    with _CACHE_LOCK:
        if not ready and not _AG_DISCOVERY["pending"] and time.time() - _AG_DISCOVERY["checked"] >= 30:
            _AG_DISCOVERY["pending"] = True
            threading.Thread(target=_refresh_ag_discovery, daemon=True).start()

    return {
        **_desktop_diagnostic(process_running, login_required, ready),
        "id": "ag",
        "display_name": "Gemini",
        "installed": installed,
        "running": running,
        "ready": ready,
        "port": ls_port,
        "exe": exe,
        "runtime_status": "desktop_online" if ready else ("desktop_launchable" if installed else "offline")
    }


# ---------------- 4. Codex (GPT) 探针 ----------------
def probe_codex():
    exe = shutil.which("codex") or shutil.which("codex.cmd") or shutil.which("codex.exe")
    if not exe:
        try:
            exe = cfg.codex_exe()
        except (SystemExit, OSError):
            pass
    if not exe:
        from pathlib import Path
        local = os.environ.get("LOCALAPPDATA") or cfg.home_path("AppData/Local")
        candidates = sorted((Path(local) / "OpenAI/Codex/bin").glob("*/codex.exe"),
                            key=lambda path: path.stat().st_mtime, reverse=True)
        exe = str(candidates[0]) if candidates else None
    installed = bool(exe and os.path.isfile(exe))
    ready = False
    try:
        with open(os.path.expanduser("~/.codex/auth.json"), encoding="utf-8") as stream:
            auth = json.load(stream)
        token = (auth.get("tokens") or {}).get("access_token") or auth.get("access_token")
        if token:
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            ready = installed and float(claims.get("exp", 0)) > time.time()
    except (OSError, ValueError, TypeError, IndexError, AttributeError):
        pass
    daemon_ready = _is_port_open(8778, timeout=0.15)
    process_running = _client_process_running(("codex.exe",))
    return {"readiness_state": "ready" if ready else ("login_required" if installed else "not_installed"),
            "process_running": process_running, "authenticated": bool(ready),
            "detail": "CLI 通道就绪" if ready else ("CLI 待登录或凭证已过期" if installed else "CLI 未安装"),
            "id": "codex", "display_name": "GPT", "installed": installed,
            "running": ready, "ready": ready, "exe": exe, "daemon_ready": daemon_ready,
            "runtime_status": "desktop_online" if ready else ("desktop_launchable" if installed else "offline")}


# ---------------- 5. DeepSeek (DSH) 探针 ----------------
def probe_dsh():
    installed = os.path.isdir(os.path.expanduser("~/.dsh"))
    ready = _is_port_open(8643, timeout=0.15)
    qq_ports = [port for port in (3000, 6000, 6099) if _is_port_open(port, timeout=0.15)]
    return {"id": "dsh", "display_name": "DeepSeek", "installed": installed,
            "running": ready, "ready": ready, "port": 8643,
            "qq_bridge": {"channel_ok": bool(qq_ports), "ports": qq_ports},
            "runtime_status": "desktop_online" if ready else ("desktop_launchable" if installed else "offline")}


PROBERS = {
    "workbuddy": probe_workbuddy,
    "doubao": probe_doubao,
    "ag": probe_antigravity,
    "codex": probe_codex,
    "dsh": probe_dsh
}


def get_client_status(client_id, force_refresh=False):
    """获取指定客户端的实时探测状态，含缓存与 API 降级检测。"""
    now = time.time()
    with _CACHE_LOCK:
        if not force_refresh and client_id in _PROBE_CACHE:
            item, exp = _PROBE_CACHE[client_id]
            if now < exp:
                return dict(item)

    fn = PROBERS.get(client_id)
    if not fn:
        res = {"id": client_id, "runtime_status": "offline", "detail": "未知客户端"}
    else:
        try:
            res = fn()
        except Exception as e:
            res = {"id": client_id, "runtime_status": "offline", "error": str(e)}

    try:
        import confluxconfig as cfg
        import json
        with open(cfg.root_path("data/hub/providers.json"), encoding="utf-8") as stream:
            registry = json.load(stream)
        entries = registry.get("providers", []) if isinstance(registry, dict) else registry
        provider = next((p for p in entries if p.get("id") == client_id), {})
    except (OSError, ValueError):
        provider = {}
    if provider.get("enabled") is False:
        res.update(runtime_status="offline", detail="角色已移除/停用", api_fallback=False)
        with _CACHE_LOCK:
            _PROBE_CACHE[client_id] = (dict(res), now + CACHE_TTL)
        return res
    # 检查是否配有 API Key（若桌面端离线但有 API，则判定为 api_online）
    if res.get("runtime_status") != "desktop_online":
        has_api = False
        api_keys = [
            f"CONFLUX_{client_id.upper()}_KEY",
            f"{client_id.upper()}_API_KEY",
            "OPENAI_API_KEY" if client_id in ("codex", "mimo") else None,
            "DEEPSEEK_API_KEY" if client_id == "dsh" else None,
            "GEMINI_API_KEY" if client_id in ("ag", "arch") else None,
            "ARK_API_KEY" if client_id == "doubao" else None,
            "HUNYUAN_API_KEY" if client_id == "workbuddy" else None
        ]
        for ak in filter(None, api_keys):
            if os.environ.get(ak):
                has_api = True
                break
        ref = provider.get("api_key_ref") or ""
        if ref.startswith("env:"):
            has_api = has_api or bool(os.environ.get(ref[4:].strip()))
        elif ref.startswith("file:"):
            path = os.path.realpath(ref[5:].strip())
            roots = [os.path.realpath(cfg.root_path(d)) for d in ("data/secrets", "data/hub/secrets")]
            has_api = has_api or (any(os.path.normcase(path).startswith(os.path.normcase(d) + os.sep)
                                    for d in roots) and os.path.isfile(path) and os.path.getsize(path) > 0)
        if provider.get("probe_status") in ("AUTH_ERROR", "UNREACHABLE"):
            has_api = False
            res["detail"] = provider.get("probe_detail") or "API 未连通"
        if has_api:
            res["runtime_status"] = "api_online"
            res["api_fallback"] = True
            res["detail"] = "API 在线"

    with _CACHE_LOCK:
        _PROBE_CACHE[client_id] = (res, now + CACHE_TTL)

    return res


def probe_all_clients(force_refresh=False):
    """全量扫描 5 大客户端。"""
    results = {}
    for cid in PROBERS:
        results[cid] = get_client_status(cid, force_refresh=force_refresh)
    return results


def ensure_client_runtime(client_id):
    """
    当客户端已安装但未开启 CDP 时，温和拉起该客户端：
    绝不强杀用户窗口，采用带调试端口的参数拉起。
    """
    st = get_client_status(client_id, force_refresh=True)
    if st.get("runtime_status") == "desktop_online":
        return True, "已在线"
    
    exe = st.get("exe")

    if client_id == "workbuddy":
        if not exe or not os.path.isfile(exe):
            return False, f"未检测到安装可执行文件: {exe}"
        port = st.get("port", 9230)
        try:
            subprocess.Popen(
                [exe, f"--remote-debugging-port={port}", "--remote-allow-origins=*"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            time.sleep(2.5)
            new_st = get_client_status("workbuddy", force_refresh=True)
            return new_st.get("runtime_status") == "desktop_online", "已尝试启动 WorkBuddy"
        except Exception as e:
            return False, str(e)

    if client_id == "doubao":
        if not exe or not os.path.isfile(exe):
            return False, f"未检测到安装可执行文件: {exe}"
        port = st.get("port", 9229)
        try:
            subprocess.Popen(
                [exe, f"--remote-debugging-port={port}"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            time.sleep(2.5)
            new_st = get_client_status("doubao", force_refresh=True)
            return new_st.get("runtime_status") == "desktop_online", "已尝试启动豆包"
        except Exception as e:
            return False, str(e)

    if client_id == "dsh":
        try:
            ps_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restart-dsh.ps1")
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            time.sleep(3.0)
            new_st = get_client_status("dsh", force_refresh=True)
            return True, "已触发 DeepSeek 桌面守护拉起"
        except Exception as e:
            return False, str(e)

    if client_id == "ag":
        try:
            if not _client_process_running(("Antigravity.exe", "language_server.exe")):
                ag_exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe")
                if os.path.exists(ag_exe):
                    subprocess.Popen([ag_exe], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True, "Antigravity / Gemini 客户端就绪"
        except Exception as e:
            return False, str(e)

    if client_id == "codex":
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                time.sleep(1.5)
                return True, "已尝试拉起 Codex CLI"
            except Exception as e:
                return False, str(e)

    return False, "当前客户端不支持自动静默拉起"



if __name__ == "__main__":
    import pprint
    print("=== Conflux 通用客户端探针扫描结果 ===")
    pprint.pprint(probe_all_clients(force_refresh=True))