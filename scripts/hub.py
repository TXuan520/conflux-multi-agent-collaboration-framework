# -*- coding: utf-8 -*-
"""
四人协作台：一个本地网页窗口，人类 + Codex + DSH(飞鱼) 在同一个聊天里。
Antigravity 暂时占位，等它的接口。

用法：
    python hub.py            # 起服务，默认 http://127.0.0.1:8777

接口：
    GET  /                    网页
    GET  /api/messages?since= 拉消息
    POST /api/send            {"from":"human|dsh","text":"..."}
    POST /api/codex           {"text":"..."} 只发给 Codex
"""
import os as _os_stage
import shutil as _shutil_stage
import sys as _sys_stage

# .env 加载：仓库根目录放一个 .env（模板见 .env.example）即可免去手动 export。
# 只补「没设过」的变量，已有环境变量优先；没有 .env 或读失败都不影响启动。
_ENV_FILE = _os_stage.environ.get("CONFLUX_ENV_FILE", "").strip()


def _load_env_file():
    try:
        root = _os_stage.path.dirname(_os_stage.path.dirname(_os_stage.path.abspath(__file__)))
        path = _ENV_FILE or _os_stage.path.join(root, ".env")
        if not path or not _os_stage.path.isfile(path):
            return
        with open(path, encoding="utf-8") as _fh:
            for _line in _fh:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _key, _, _val = _line.partition("=")
                _key = _key.strip()
                _val = _val.strip().strip('"').strip("'")
                if _key and _key not in _os_stage.environ:
                    _os_stage.environ[_key] = _val
    except Exception as _e:
        print("env file skipped:", _e)


_load_env_file()

# 演示模式：--demo 或 CONFLUX_DEMO=1 时把运行态整体切到 data/demo-runtime，
# 真实 data/hub 一个字节都不动。必须在 import confluxconfig 之前设置环境变量。
_DEMO_MODE = ("--demo" in _sys_stage.argv) or (_os_stage.environ.get("CONFLUX_DEMO", "") == "1")
_DEMO_RESET = "--reset" in _sys_stage.argv
if _DEMO_MODE:
    _ROOT_STAGE = _os_stage.path.dirname(_os_stage.path.dirname(_os_stage.path.abspath(__file__)))
    _RUNTIME_STAGE = _os_stage.path.join(_ROOT_STAGE, "data", "demo-runtime")
    _os_stage.environ["CONFLUX_DATA_DIR"] = _RUNTIME_STAGE
    _os_stage.environ.setdefault("CONFLUX_DATA_ROOT", _os_stage.path.join(_ROOT_STAGE, "data"))

import confluxconfig as cfg
import hashlib
import routing_config as bootstrap
import json
CONFLUX_NAPCAT_TOKEN = cfg.optional_env("CONFLUX_NAPCAT_TOKEN")
CONFLUX_BOT_QQ = cfg.optional_qq_id("CONFLUX_BOT_QQ")
CONFLUX_OWNER_QQ = cfg.optional_qq_id("CONFLUX_OWNER_QQ")
CONFLUX_RELAY_HOST = cfg.optional_relay_host()

DEMO_SEED_DIR = _os_stage.path.join(cfg.DATA_ROOT, "demo-seed")


def demo_seed(reset=False):
    """把种子数据铺到演示运行态目录。reset=True 时先清空，保证可重复录屏。"""
    if not _DEMO_MODE:
        return {"ok": False, "reason": "非演示模式"}
    runtime = cfg.HUB_DATA
    if reset and _os_stage.path.isdir(runtime):
        _shutil_stage.rmtree(runtime, ignore_errors=True)
    _os_stage.makedirs(runtime, exist_ok=True)
    copied = 0
    # 静态资源必须跟着进演示运行态，否则 / 打不开页面
    _hub_dir = _os_stage.path.join(cfg.DATA_ROOT, "hub")
    for _name in ("index.html", "providers.json", "manifest.json", "icon-192.png", "icon-512.png", "theme-bg.webp", "theme-bg.png", "bg.png"):
        _src_file = _os_stage.path.join(_hub_dir, _name)
        _dst_file = _os_stage.path.join(runtime, _name)
        if _os_stage.path.isfile(_src_file) and (reset or not _os_stage.path.exists(_dst_file)):
            _shutil_stage.copyfile(_src_file, _dst_file)
            copied += 1
    if _os_stage.path.isdir(DEMO_SEED_DIR):
        for name in _os_stage.listdir(DEMO_SEED_DIR):
            src = _os_stage.path.join(DEMO_SEED_DIR, name)
            if not _os_stage.path.isfile(src):
                continue
            dst = _os_stage.path.join(runtime, name)
            if name == "demo-messages.jsonl":
                dst = _os_stage.path.join(runtime, "messages.jsonl")
            elif name == "demo-tickets.json":
                dst = _os_stage.path.join(runtime, "tickets.json")
            if reset or not _os_stage.path.exists(dst):
                _shutil_stage.copyfile(src, dst)
                copied += 1
    return {"ok": True, "runtime": runtime, "copied": copied}


if _DEMO_MODE:
    try:
        demo_seed(reset=_DEMO_RESET)
    except Exception as _e:
        print("demo seed failed:", _e)
import os as _os_boot
# 代理统一走 data/hub/proxy.txt（当前指向 Clash Verge 7897）；没有配置才清空继承值
def _sync_hub_proxy_env():
    try:
        pxy = open(cfg.root_path('data/hub/proxy.txt'), encoding="utf-8").read().strip()
    except Exception:
        pxy = ""
    if pxy:
        if not pxy.startswith("http"):
            pxy = "http://" + pxy
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            _os_boot.environ[k] = pxy
        _os_boot.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,api.deepseek.com,platform.xiaomimimo.com"
        _os_boot.environ.pop("ALL_PROXY", None)
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            _os_boot.environ.pop(k, None)

_sync_hub_proxy_env()
try:
    import socket as _sock_boot, subprocess as _sp_boot
    with _sock_boot.socket(_sock_boot.AF_INET, _sock_boot.SOCK_STREAM) as _s:
        _s.settimeout(0.2)
        if _s.connect_ex(("127.0.0.1", 10808)) != 0:
            _pyw = cfg.python_exe(True)
            if not _os_boot.path.exists(_pyw):
                _pyw = "pythonw.exe"
            _sp_boot.Popen([_pyw, cfg.root_path('scripts/port_bridge_10808_7897.py')],
                           creationflags=0x08000000, close_fds=True)
except Exception:
    pass
import difflib
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
import urllib.request as _ureq
import shutil

ROOT = cfg.root_path('')
NAPCAT_TOKEN = CONFLUX_NAPCAT_TOKEN
DATA_DIR = cfg.HUB_DATA

_LAST_INJECT = {"ts": 0}
# 注入轮次 → 收件人。回复携带 turn_id 时按此精确回投，不再依赖 FIFO 顺序。
TURN_ROUTES_FILE = os.path.join(DATA_DIR, "turn-routes.json")

def _load_turn_routes():
    try:
        if os.path.exists(TURN_ROUTES_FILE):
            with open(TURN_ROUTES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cutoff = time.time() - 1800
                return {k: (list(v[0]), float(v[1]), v[2] if len(v) > 2 else None)
                        for k, v in data.items() if float(v[1]) >= cutoff}
    except Exception:
        pass
    return {}

def _save_turn_routes(routes=None):
    try:
        target = routes if routes is not None else _TURN_ROUTES
        cutoff = time.time() - 1800
        clean = {k: v for k, v in target.items() if v[1] >= cutoff}
        with open(TURN_ROUTES_FILE, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False)
    except Exception:
        pass

_TURN_ROUTES = _load_turn_routes()


def _dsh_dup_recent(text, window=45.0):
    """同一轮里「手动 POST + 自动回传」会发成两条几乎一样的消息：这里做内容级高鲁棒去重。"""
    try:
        norm = lambda s: re.sub(r"[\s\*`_#>　、，。；：！？!?,.;:()（）\[\]【】\-\—…\"'“”‘’]+", "", (s or "")).lower()
        cur = norm(text)
        if len(cur) < 15:
            return False
        now = time.time()
        for m in reversed(read_msgs(0)[-40:]):
            if m.get("sender") != "dsh":
                continue
            if now - float(m.get("ts") or 0) > window:
                break
            other = norm(m.get("text") or "")
            if not other or len(other) < 15:
                continue
            if other == cur:
                return True
            len_ratio = min(len(other), len(cur)) / max(len(other), len(cur))
            if (other in cur or cur in other) and len_ratio > 0.6:
                return True
            if len_ratio >= 0.5:
                ratio = difflib.SequenceMatcher(None, cur, other).quick_ratio()
                if ratio >= 0.65:
                    return True
                # Character 2-gram Jaccard fallback
                g1 = set(cur[i:i+2] for i in range(len(cur)-1))
                g2 = set(other[i:i+2] for i in range(len(other)-1))
                if g1 and g2 and (len(g1 & g2) / len(g1 | g2)) >= 0.45:
                    return True
    except Exception:
        return False
    return False


def dsh_manual_reply_conflicts(sender, client_mid, recipients, text):
    """During an injected turn, its original recipients use the automatic final reply."""
    if sender != "dsh" or (client_mid and (client_mid.startswith("turn:") or client_mid.startswith("dsh-reply-"))):
        return False
    targets = set(recipients or [])
    if not targets:
        arrow = re.match(r"^\s*>>>[^\n]*", text or "")
        if arrow:
            header = arrow.group(0).lower()
            aliases = {"human": ("炫炫", "human", "主人"), "ag": ("gemini", "antigravity"),
                       "codex": ("gpt", "codex"), "arch": ("argon", "arch"), "mimo": ("mimo", "小米"), "doubao": ("doubao", "豆包", "seedance")}
            targets = {agent for agent, names in aliases.items() if any(name in header for name in names)}
        else:
            targets = {"human"}
    now = time.time()
    # 无论 hub 是否重启，优先载入磁盘持久化的轮次
    routes = _load_turn_routes()
    _TURN_ROUTES.update(routes)
    return any(now - route[1] <= 1800 and targets.intersection(route[0])
               for route in list(_TURN_ROUTES.values()))


from brainstorm_store import BrainstormStore
_brainstorms = BrainstormStore(os.path.join(DATA_DIR, "brainstorms.json"))
_brainstorm_local = threading.local()

def _brainstorm_dispatch(fn, agent, mids):
    turn = uuid.uuid4().hex
    _brainstorms.bind(turn, agent, mids)
    generation = next((_broadcast_message_generations[mid] for mid in mids
                       if mid in _broadcast_message_generations), None)
    def run(text):
        if generation is not None and not broadcast_active(generation):
            return
        _brainstorm_local.turn = turn
        _brainstorm_local.mids = mids
        _brainstorm_local.broadcast_generation = generation
        try:
            return fn(text)
        finally:
            _brainstorm_local.turn = None
            _brainstorm_local.mids = []
            _brainstorm_local.broadcast_generation = None
    return run


_DSH_LAST_ROUTE = {"to": None, "ts": 0.0}


def _turn_route(tid, reply_to):
    # 记一份「最近一次投给 dsh 的回复目标」，用于 turn id 对不上时兜底
    try:
        _DSH_LAST_ROUTE["to"] = list(reply_to or ["human"])
        _DSH_LAST_ROUTE["ts"] = time.time()
    except Exception:
        pass
    global _TURN_ROUTES
    _TURN_ROUTES[tid] = (list(reply_to or ["human"]), time.time(),
                         getattr(_brainstorm_local, "broadcast_generation", None))
    _brainstorms.bind("turn:" + tid, "dsh", getattr(_brainstorm_local, "mids", []))
    cutoff = time.time() - 1800
    for k in [k for k, v in list(_TURN_ROUTES.items()) if v[1] < cutoff]:
        _TURN_ROUTES.pop(k, None)
    _save_turn_routes()
MSG_FILE = os.path.join(DATA_DIR, "messages.jsonl")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
AGENTS = ("codex", "ag", "dsh", "mimo", "doubao", "workbuddy")
DISPLAY_AGENTS = ("codex", "ag", "dsh", "mimo", "arch", "doubao", "workbuddy")   # 旁白不是智能体，不进状态列表
def display_agents():
    ids = _routing.sender_ids() - {"narrator", "watch"}
    return tuple(name for name in DISPLAY_AGENTS if name in ids) + tuple(sorted(ids - set(DISPLAY_AGENTS)))


INFLIGHT_TURNS_FILE = os.path.join(DATA_DIR, "inflight-turns.json")
_inflight_lock = threading.Lock()
_active_reply_target = {}
_DRAINING = False

def _load_inflight_turns():
    if not os.path.exists(INFLIGHT_TURNS_FILE):
        return {}
    try:
        with open(INFLIGHT_TURNS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_inflight_turns(data):
    try:
        tmp = INFLIGHT_TURNS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, INFLIGHT_TURNS_FILE)
    except Exception:
        pass

def record_inflight_turn(agent_name, sender, raw_text):
    with _inflight_lock:
        data = _load_inflight_turns()
        data[agent_name] = {
            "sender": sender,
            "text": (raw_text or "")[:500],
            "ts": time.time()
        }
        _save_inflight_turns(data)

def clear_inflight_turn(agent_name):
    with _inflight_lock:
        data = _load_inflight_turns()
        if agent_name in data:
            data.pop(agent_name, None)
            _save_inflight_turns(data)

def _compensate_inflight_turns():
    with _inflight_lock:
        data = _load_inflight_turns()
        if not data:
            return
        # 先清空文件并原子落盘（保证只报一次）
        _save_inflight_turns({})

    for name, item in data.items():
        try:
            append_msg("system", "⚠️ [系统恢复] %s 上一回合因服务重启中断，原消息已记录，请按需重发。" % name,
                       kind="system", to=["human"])
        except Exception:
            pass


def api_provider_ready(provider):
    if provider.get("probe_status") in ("AUTH_ERROR", "UNREACHABLE"):
        return False
    pid = provider.get("id")
    if pid == "mimo":
        try:
            with open(cfg.root_path("data/secrets/mimo.key"), encoding="utf-8") as credential:
                return bool(credential.read().strip())
        except OSError:
            return False
    if pid == "arch":
        try:
            import vertex_client as vc
            credential = vc.API_KEY_FILE if vc.USE_API_KEY else vc.ADC
            return os.path.isfile(credential) and os.path.getsize(credential) > 0
        except (ImportError, KeyError, OSError):
            return False
    return bool(provider.get("endpoint_url") and provider.get("model") and
                (not provider.get("api_key_ref") or _secret_from_ref(provider["api_key_ref"])))


_AGENT_Q = {}
_AGENT_QLOCK = threading.Lock()

# 旧同名 inflight 死代码已清理，统一收归全局单一事实源



def _agent_drain(pid):
    """把忙碌期间排队的消息逐条补投，保证不丢。"""
    while True:
        with _AGENT_QLOCK:
            entry = _AGENT_Q.get(pid) or {"q": [], "busy": False}
            if not entry["q"]:
                entry["busy"] = False
                _AGENT_Q[pid] = entry
                return
            fn = entry["q"].pop(0)
            _AGENT_Q[pid] = entry
        try:
            if agent_is_enabled(pid):
                fn()
        except Exception:
            pass


def make_auto_dispatcher(pid, desktop_fn):
    def _dispatch(text):
        with _AGENT_QLOCK:
            _e = _AGENT_Q.setdefault(pid, {"q": [], "busy": False})
            _busy = bool(_e.get("busy"))
            if _busy:
                _e["q"].append(lambda t=text: _run(t))
                _AGENT_Q[pid] = _e
        if _busy:
            try:
                append_msg("system", "（%s 正忙，本条已排队，稍后自动补投）" % pid, kind="system")
            except Exception:
                pass
            return True
        with _AGENT_QLOCK:
            _e = _AGENT_Q.setdefault(pid, {"q": [], "busy": False})
            _e["busy"] = True
            _AGENT_Q[pid] = _e
        try:
            return _run(text)
        finally:
            with _AGENT_QLOCK:
                _e = _AGENT_Q.setdefault(pid, {"q": [], "busy": False})
                _e["busy"] = False
                _AGENT_Q[pid] = _e
            threading.Thread(target=_agent_drain, args=(pid,), daemon=True).start()

    def _run(text):
        try:
            import client_probe as _cp
            st = _cp.get_client_status(pid)
            if st.get("runtime_status") == "desktop_online":
                return desktop_fn(text)
            prov = next((p for p in providers_read().get("providers", [])
                         if p.get("id") == pid and p.get("enabled") is not False), None)
            if prov and (prov.get("endpoint_url") or prov.get("api_key_ref") or st.get("api_fallback")):
                return ask_custom_provider(pid, text)
            ok_launch, _ = _cp.ensure_client_runtime(pid)
            if ok_launch:
                return desktop_fn(text)
            return desktop_fn(text)
        except Exception:
            return desktop_fn(text)
    return _dispatch


AGENT_ENABLED_FILE = os.path.join(DATA_DIR, "agent_enabled.json")
_AGENT_ENABLED = {}
_PROBE_MISS = {}


def agent_enabled_map():
    """读「智能体开关」：只影响分发，不杀进程。默认全开。"""
    global _AGENT_ENABLED
    try:
        with open(AGENT_ENABLED_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            _AGENT_ENABLED = {k: bool(v) for k, v in raw.items()}
    except Exception:
        pass
    return _AGENT_ENABLED


def agent_is_enabled(name):
    m = agent_enabled_map()
    return m.get(name, True)


def set_agent_enabled(name, enabled):
    m = dict(agent_enabled_map())
    m[name] = bool(enabled)
    global _AGENT_ENABLED
    _AGENT_ENABLED = m
    try:
        os.makedirs(os.path.dirname(AGENT_ENABLED_FILE), exist_ok=True)
        with open(AGENT_ENABLED_FILE, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    if not enabled:
        _broadcast_policy.drop(name)
    return m


def dispatch_targets():
    base = {"codex": ask_codex, "ag": ask_antigravity, "dsh": inject_qq,
            "mimo": ask_mimo, "arch": ask_arch, "doubao": ask_doubao,
            "workbuddy": ask_workbuddy}
    targets = {}
    for pid, fn in base.items():
        if pid not in _routing.sender_ids() or not agent_is_enabled(pid):
            continue
        targets[pid] = make_auto_dispatcher(pid, fn)
    for provider in _routing.load_providers():
        pid = provider["id"]
        if provider.get("enabled") is not False and pid not in _routing.RESERVED_IDS and agent_is_enabled(pid):
            targets[pid] = lambda text, provider_id=pid: ask_custom_provider(provider_id, text)
    return targets


def ask_custom_provider(provider_id, text):
    mark(provider_id, True)
    try:
        activity(provider_id, "running", (text or "")[:100], phase="running")
        result = subprocess.run(
            [cfg.optional_env("CONFLUX_PYTHON") or sys.executable,
             cfg.root_path("scripts/agent_loop.py"), "--provider", provider_id,
             "--task", text, "--cwd", cfg.WORKSPACE],
            capture_output=True, encoding="utf-8", errors="replace", timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = (result.stdout or "").strip()
        if result.returncode:
            agent_failure(provider_id, "provider 子进程退出码 %s" % result.returncode)
            detail = ("（" + output[:160] + "）") if output else ""
            output = "未完成：provider 子进程退出码 %s%s" % (result.returncode, detail)
        if not output:
            agent_failure(provider_id, "provider 返回空回复")
            output = "未完成：provider 返回空回复"
        append_msg(provider_id, output)
    except Exception as exc:
        agent_failure(provider_id, exc)
        append_msg(provider_id, "未完成：provider 派发失败（%s）" % type(exc).__name__)
    finally:
        mark(provider_id, False)
        try:
            if callable(globals().get("track_verdict_now")) and track_verdict_now(provider_id) not in ("timeout", "timeout_5m", "loop", "error"):
                activity(provider_id, "idle", "", phase="completed")
        except Exception:
            pass


ROUNDTABLE_ENABLED = False
FIXED_TRAILER = "【输出纪律】只给结论或 diff，别复述整份文件，别重复别人已说过的内容。【称呼约定】一律说「项目X」和「项目号-轮次」（如 1-3），不要用内部工单 id。【命名】新建文件用短横线或驼峰，别用下划线——成对下划线会被部分客户端当 Markdown 吃掉，昨天已导致两次文件名写错（provider_adapter、SEEDANCE_ONBOARDING）。"   # 关圆桌自动第二轮：每轮少 3 次模型
# 出厂设置：所有智能体默认都带这组音乐遥控能力，不用等临时教。
CAPABILITY_MUSIC = (
    "\n【出厂能力·协作台音乐遥控】需要放歌/切歌时直接调，不用问人：\n"
    "POST http://127.0.0.1:8777/api/music/cmd ，JSON 体 {\"action\":\"play\",\"query\":\"歌名 歌手\",\"from\":\"你的名字\"}；"
    "action 可取 play / pause / toggle / next / prev / stop。\n"
    "歌单：GET http://127.0.0.1:8777/api/netease/playlist?id=<歌单ID>（顺序播放请先 play 再 next）；"
    "当前状态：GET http://127.0.0.1:8777/api/music/state。"
    "播放发生在炫炫开着的协作台网页里，接口返回 ok 只代表指令已下发，不代表一定出声。"
)
from file_delivery import CAPABILITY_FILE
CODEX = cfg.codex_exe()
CODEX_TIMEOUT = 1800   # 单次回复上限：思考慢可以等，但 260s 会误判超时
CODEX_SESSIONS = os.path.join(os.environ["USERPROFILE"], ".codex", "sessions")
INJECT_FILE = os.path.join(cfg.DATA_ROOT, "media", "qq-inject.jsonl")



_client_bootstrap_state = {}


def ensure_client_session(agent, session_key, deliver):
    """Initialization has its own turn; the following user payload stays untouched."""
    state = _client_bootstrap_state.setdefault(agent, {})
    return bootstrap.ensure_session(agent, session_key, deliver, state)


def inject_qq(text, reply_to=None, source=None, _initializing=False):
    """协作台唤醒通道：伪造一个「Conflux」虚拟账号的消息，只唤醒 DSH，不落炫炫 QQ。

    只允许被授权的来源（human/agent）注入；无来源的内部直调一律拒绝并返回 False，
    避免任何"内部测试"路径把消息漏进 QQ 通道。
    """
    if source not in ("human", "agent", "system"):
        return False
    # 停用每次重新建立 session 时的额外初始化消息注入，避免同一秒连发两条导致 turnId 错位
    # if not _initializing:
    #     ensure_client_session("dsh", "conflux-inbox", lambda prompt:
    #                           inject_qq(prompt, reply_to=[], source="system", _initializing=True))
    try:
        _LAST_INJECT["ts"] = time.time()
    except Exception:
        pass
    try:
        inbox_push("conflux", text)
    except Exception:
        pass
    _tid = "%x" % int(time.time() * 1000)
    try:
        _turn_route(_tid, reply_to or ["human"])
    except Exception:
        pass
    prompt = text
    ev = {
        "post_type": "message", "message_type": "private", "sub_type": "friend",
        "conflux_internal": True,
        "conflux_turn_id": _tid,
        "conflux_reply_to": reply_to or ["human"],
        "user_id": 999000001, "time": int(time.time()), "self_id": CONFLUX_BOT_QQ,
        "message_id": int(time.time() * 1000) % 1000000,
        "raw_message": prompt,
        "message": [{"type": "text", "data": {"text": prompt}}],
        "sender": {"user_id": 999000001, "nickname": "Conflux"}, "font": 0,
    }
    try:
        os.makedirs(os.path.dirname(INJECT_FILE), exist_ok=True)
        with open(INJECT_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:
        mark("dsh", True)
        track_start("dsh", (text or "")[:60])
        activity("dsh", "running", (text or "")[:100], phase="streaming")
    except Exception:
        pass
    return True


def find_codex():
    """Codex 自动更新会换 bin 目录，每次现找最新的。"""
    import glob
    base = os.path.join(os.environ["LOCALAPPDATA"], "OpenAI", "Codex", "bin")
    cands = glob.glob(os.path.join(base, "*", "codex.exe"))
    if not cands:
        return CODEX
    return max(cands, key=os.path.getmtime)

_lock = threading.RLock()
_busy = {}
_activity = {}
_activity_lock = threading.Lock()
_activity = {"codex": {"state": "idle", "task": "", "started": 0, "updated": 0, "files": []},
             "ag": {"state": "idle", "task": "", "started": 0, "updated": 0, "files": []},
             "dsh": {"state": "idle", "task": "", "started": 0, "updated": 0, "files": []},
             "mimo": {"state": "idle", "task": "", "started": 0, "updated": 0, "files": []},
             "arch": {"state": "idle", "task": "", "started": 0, "updated": 0, "files": []},
             "doubao": {"state": "idle", "task": "", "started": 0, "updated": 0, "files": []},
             "workbuddy": {"state": "idle", "task": "", "started": 0, "updated": 0, "files": []}}
_handoff_lock = threading.Lock()
_handoff_seen = {}

AGENT_LABELS = {"codex": "GPT", "ag": "Gemini", "dsh": "DeepSeek", "mimo": "MiMo", "arch": "Argon", "doubao": "Seedance", "workbuddy": "WorkBuddy"}
# 身份标签：侧边栏显示在名字旁边
AGENT_TAGS = {"dsh": "CEO", "arch": "CTO", "codex": "QA", "ag": "COO", "mimo": "CKO", "doubao": "气氛组", "workbuddy": "助手"}
AGENT_ROLES = {"dsh": "首席执行官 / 后端主开发", "arch": "总架构师 / 技术拍板",
               "codex": "测试验收 / 找漏洞", "ag": "前端开发 / 运营",
               "mimo": "前端维护 / 文档撰写", "doubao": "气氛组 / 本地助手", "workbuddy": "混元助手 / 协作"}
_to_map = {}


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(MSG_FILE):
        open(MSG_FILE, "w", encoding="utf-8").close()


def is_garbled_reply(text):
    """识别「模型把工具调用语法/异种语言当正文吐出来」的坏输出。

    典型特征：
      1. 出现裸的工具标记，如 [TOOL_CALL]、{"name": "run_cmd"
      2. 夹杂大段日文假名（正常中文回复里不会有连续假名）
    """
    if not isinstance(text, str) or not text.strip():
        return False
    t = text.strip()
    if "[TOOL_CALL]" in t or "[TOOL_CALLS]" in t or '"tool_calls"' in t:
        return True
    if t.count('{"name"') >= 1 and t.count("{") >= 2:
        return True
    kana = sum(1 for ch in t if "\u3040" <= ch <= "\u30ff")
    if kana >= 3 and kana / max(1, len(t)) > 0.12:
        return True
    return False


def clean_agent_text(sender, text):
    """清理模型通道偶尔带出的宿主界面文字和重复整段回复。"""
    if not isinstance(text, str):
        return text
    if sender == "codex":
        import re as _r2
        text = _r2.sub(r"^(我(先|已经)?(核对|读取|查看)[^\n]{0,40}[。\n])+", "", text or "").strip()
        # 把工具输出的噪音行去掉，太长就只留开头，但绝对不能误伤 >>> 指向行
        keep = []
        for line in text.splitlines():
            t = line.strip()
            if t.startswith(">>>"):
                keep.append(line.rstrip())
                continue
            if t.startswith(("Get-Content", "FullName", "Length", "----", "|")) or (t.startswith(">") and not t.startswith(">>>")) or (t.startswith(cfg.root_path('')) and len(t) > 80):
                continue
            keep.append(line.rstrip())
        cleaned = "\n".join(keep).strip()
        return cleaned          # 炫炫 12:38：不再截断，完整输出
    if sender != "ag":
        return text

    ui_lines = {"Files", "Terminal", "Extensions", "Subagents", "Review", "Thought for"}
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s in ui_lines or re.fullmatch(r"\d+", s):
            continue
        lines.append(line.rstrip())

    # 页面抓取有时会把同一条完整回复复制两遍，按整段周期去重。
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    n = len(lines)
    for period in range(1, n // 2 + 1):
        if n % period == 0 and n // period >= 2:
            block = lines[:period]
            if lines == block * (n // period):
                lines = block
                break
    return "\n".join(lines).strip()


_chain = []
_last_agent_inject = 0.0
_pending_sender = None



def is_silent(text):
    """模型的静默控制标记不应进入消息流，也不能触发接力。"""
    if not isinstance(text, str):
        return False
    t = re.sub(r'^\s*(?:>>>|@)\s*\S*\s*', '', text.strip()).strip()
    t_norm = re.sub(r'^[`"\'“”‘’]+|[`"\'“”‘’。！!.,，\s]+$', '', t).strip().lower()
    return t_norm in ("[[silent]]", "[[slient]]", "[[静默]]", "[[skip]]", "silent", "slient")


_LAUNCH_TS = {}


def agent_failure(name, reason):
    detail = str(reason)[:240]
    timeout = isinstance(reason, (TimeoutError, subprocess.TimeoutExpired)) or any(
        word in detail.lower() for word in ("timeout", "timed out", "超时"))
    state = "timeout" if timeout else "error"
    track_end(name, state)
    activity(name, state, detail, phase=state)


def _tracked_runner(name, fn, text):
    track_start(name, (text or "")[:60])
    # DSH injection returns before the external model finishes; trace events own its lifecycle.
    TRACK[name]["thread"] = None if name == "dsh" else threading.current_thread()
    try:
        fn(text)
    except Exception as e:
        agent_failure(name, e)
        try:
            append_msg(name, "（执行异常：%s）" % str(e)[:60])
        except Exception:
            pass
    finally:
        if name != "dsh" and track_verdict_now(name) not in ("loop", "timeout", "timeout_5m", "error", "silent"):
            track_end(name, "done")


_agent_queue_lock = threading.RLock()
_agent_queues = {}
_agent_workers = {}


def _ticket_disp(tid):
    """对外称呼统一用「项目号-轮次」（如 1-3），不暴露内部工单 id。"""
    try:
        with open(os.path.join(DATA_DIR, "projects.json"), encoding="utf-8") as f:
            _ps = json.load(f).get("projects", [])
        for _p in _ps:
            for _t in _p.get("tickets", []):
                if str(_t.get("id")) == str(tid):
                    _pid = str(_p.get("id") or "")
                    _no = _pid[1:] if _pid.startswith("P") else _pid
                    return "%s-%s" % (_no, _t.get("seq_id") or 1)
    except Exception:
        pass
    return str(tid)


def _brief_of(raw):
    """原话提取：优先工单名，其次炫炫原话，剥离系统包装。"""
    raw = raw or ""
    mt = re.search(r"【((?:\d+-\d+)|(?:工单\s*[A-Za-z0-9_-]+))[^】]*】", raw)
    if mt:
        return mt.group(1), raw
    m = re.search(r"【[^】\n]{1,8}】\s*(.+?)(?:\n\s*（时间|$)", raw, re.S)
    b = m.group(1) if m else raw
    b = re.sub(r"（时间[^）]*）", " ", b)
    b = re.sub(r"【输出纪律】.*", "", b, flags=re.S)
    b = re.sub(r"\s+", " ", b).strip()
    return b, b


_agent_current_tasks = {}  # agent -> {"sender": sender_key, "sender_label": label, "text": original_text, "ts": time.time()}

def extract_sender_and_body(text, default_sender="human"):
    if not text:
        return default_sender, (AGENT_LABELS.get(default_sender) or "炫炫"), ""
    t = str(text).strip()
    if "【Argon 分工" in t:
        return "arch", "Argon", t
    m = re.match(r"^【([^】\n]{1,10})】\s*([\s\S]*)", t)
    if m:
        s_lbl = m.group(1).strip()
        body = m.group(2).strip()
        rev = {
            "炫炫": "human", "human": "human", "主人": "human",
            "Argon": "arch", "arch": "arch", "架构师": "arch",
            "DeepSeek": "dsh", "dsh": "dsh", "飞鱼": "dsh",
            "GPT": "codex", "codex": "codex", "ChatGPT": "codex",
            "Gemini": "ag", "ag": "ag", "Antigravity": "ag",
            "MiMo": "mimo", "mimo": "mimo", "小米": "mimo",
            "Seedance": "doubao", "seedance": "doubao", "豆包": "doubao", "doubao": "doubao",
            "旁白": "narrator", "narrator": "narrator"
        }
        s_key = rev.get(s_lbl, default_sender)
        return s_key, s_lbl, body
    lbl = AGENT_LABELS.get(default_sender, "炫炫" if default_sender == "human" else default_sender)
    return default_sender, lbl, t

def queue_preview(name):
    with _agent_queue_lock:
        items = list(_agent_queues.get(name, []))
    out = []
    for it in items:
        try:
            if isinstance(it, dict):
                raw = it.get("raw_text") or it.get("text") or ""
                s_key = it.get("sender") or "human"
                s_lbl = it.get("sender_label") or AGENT_LABELS.get(s_key, "炫炫" if s_key == "human" else s_key)
                ts_val = it.get("ts", time.time())
            elif isinstance(it, tuple):
                raw = it[1] or ""
                s_key, s_lbl, _ = extract_sender_and_body(raw)
                ts_val = time.time()
            else:
                raw = str(it)
                s_key, s_lbl = "human", "炫炫"
                ts_val = time.time()
            brief, _ = _brief_of(raw)
            out.append({
                "sender": s_key,
                "sender_label": s_lbl,
                "brief": brief[:200],
                "text": raw[:8000],
                "ts": ts_val
            })
        except Exception:
            pass
    return out


def agent_queue_size(name):
    with _agent_queue_lock:
        return len(_agent_queues.get(name, []))


def _agent_queue_worker(name):
    while True:
        if not agent_is_enabled(name):
            with _agent_queue_lock:
                _agent_queues.pop(name, None)
                _agent_workers.pop(name, None)
            return
        # External jobs also finish before a queued Hub job is dispatched.
        try:
            sync_all_agents_activity()
        except Exception:
            pass
        _ts = track_state(name)
        if _ts.get("state") == "running":
            _age = _ts.get("beat_age")
            # 心跳超过 5 分钟视为残留的 running（熔断关掉后没人收尾），不再阻塞队列
            if _age is None or _age < 300:
                time.sleep(0.25)
                continue
        with _agent_queue_lock:
            pending = _agent_queues.get(name, [])
            if not pending:
                _agent_workers.pop(name, None)
                _agent_current_tasks.pop(name, None)
                return
            item = pending.pop(0)
            if isinstance(item, dict):
                fn = item["fn"]
                text = item["text"]
                ack = item.get("ack", False)
                s_key = item.get("sender") or "human"
                s_lbl = item.get("sender_label") or AGENT_LABELS.get(s_key, "炫炫" if s_key == "human" else s_key)
                raw_text = item.get("raw_text") or text
            elif isinstance(item, tuple):
                fn, text = item[0], item[1]
                ack = item[2] if len(item) > 2 else False
                s_key, s_lbl, raw_text = extract_sender_and_body(text)
            else:
                fn, text, ack = item, "", False
                s_key, s_lbl, raw_text = "human", "炫炫", ""

        if isinstance(item, dict) and item.get("broadcast_generation") is not None:
            if not broadcast_active(item["broadcast_generation"]):
                continue
        _LAUNCH_TS[name] = time.time()
        _agent_current_tasks[name] = {
            "sender": s_key,
            "sender_label": s_lbl,
            "text": raw_text,
            "ts": time.time()
        }
        _active_reply_target[name] = [s_key]
        record_inflight_turn(name, s_key, raw_text)
        try:
            if track_verdict_now(name) not in ("timeout", "timeout_5m", "loop", "error"):
                activity(name, "idle", "")
            activity(name, "running", raw_text, phase="running")
            mark(name, True)
            if ack:
                append_msg(name, "收到", "ack")
            _tracked_runner(name, fn, text)
            if name == "dsh":
                _dsh_wait_t0 = time.time()
                while time.time() - _dsh_wait_t0 < 600:
                    sync_dsh_activity()
                    st = track_state("dsh")
                    # 注入后前 3.0 秒内处于 OneBot 启动防抖保护期，即使 trace 未落盘也不得提前退回 idle
                    if time.time() - _dsh_wait_t0 < 3.0 and st.get("state") != "running":
                        time.sleep(0.25)
                        continue
                    if st.get("state") != "running":
                        break
                    time.sleep(0.25)
        finally:
            _active_reply_target.pop(name, None)
            clear_inflight_turn(name)
            _agent_current_tasks.pop(name, None)
            if track_verdict_now(name) not in ("timeout", "timeout_5m", "loop", "error"):
                activity(name, "idle", "", phase="completed")
            mark(name, False)


def launch_agent(name, fn, text, *, ack=False, sender=None, raw_text=None, broadcast_generation=None):
    if _DRAINING:
        try:
            append_msg("system", "（系统正在排空准备重启，%s 的新任务被拒绝入队）" % name, kind="system")
        except Exception:
            pass
        return
    """Queue per member; delivery and timing begin only after the previous turn ends."""
    s_key = sender or "human"
    # Direct callers receive the same minimal envelope as flush_batch.
    if not re.match(r"^【[^】\n]+】：", text or ""):
        text = bootstrap.format_message(s_key, raw_text if raw_text is not None else text)
    s_lbl = AGENT_LABELS.get(s_key, "炫炫" if s_key == "human" else s_key)
    item_dict = {
        "fn": fn,
        "text": text,
        "ack": ack,
        "sender": s_key,
        "sender_label": s_lbl,
        "raw_text": raw_text or text,
        "ts": time.time()
    }
    if broadcast_generation is not None:
        item_dict["broadcast_generation"] = broadcast_generation
    with _agent_queue_lock:
        _agent_queues.setdefault(name, []).append(item_dict)
        worker = _agent_workers.get(name)
        if worker is None or not worker.is_alive():
            worker = threading.Thread(target=_agent_queue_worker, args=(name,), daemon=True)
            _agent_workers[name] = worker
            worker.start()


def dispatch_arch_role(agent, fn, work):
    """Argon 分工走直连通道，并把收件人及回报对象写死，避免正文再被指向解析误路由。"""
    launch_agent(agent, fn, bootstrap.format_message("arch", work), sender="arch", raw_text=work)





def is_silent_text(text):
    t = (text or "").strip()
    t = re.sub(r'^\s*(?:>>>|@)\s*\S*\s*', '', t).strip()
    t = re.sub(r"[\[\]\s静默silentlskip]+", "", t.lower())
    return t == ""


def strip_broadcast_ack_prefix(text):
    """广播模式下剥离模型受工作模式惯性带出的行首『收到/OK/好的/明白』等确认前缀。"""
    if not isinstance(text, str) or not text.strip():
        return text
    t = text.strip()
    # 若整句就是纯确认词，交由 is_pure_ack 整条掐断，此处不剥空
    if is_pure_ack(t):
        return t
    # 剥离句首独立的确认废话前缀，例如 "收到，我觉得..." / "OK! 关于这个..." / "好的。\n其实..."
    stripped = re.sub(
        r'^\s*(?:(?:已?收到(?:回复)?|ok(?:ay)?|好的?|明白了?|了解|知道了?|已阅)[ \t]*[，,。！!；;：:~～、\n]+[ \t]*)+',
        '',
        t,
        flags=re.IGNORECASE
    ).strip()
    return stripped if stripped else t


def is_pure_ack(text):
    t = (text or "").strip()
    t_body = re.sub(r'^\s*(?:>{2,}|@)\s*\S*\s*', '', t).strip()
    clean = re.sub(r'^[。！!.,，~～、;\s]+|[。！!.,，~～、;\s]+$', '', t_body).strip().lower()
    exact_acks = {
        "收到", "已收到", "收到回复", "已阅", "明白", "明白了", "了解", "知道了", "嗯", "嗯嗯",
        "ok", "okay", "好的", "好", "ok收到", "收到ok", "保持待命", "收到保持待命",
        "收到谢谢", "收到，谢谢", "收到感谢确认", "收到，感谢确认"
    }
    clean_no_punc = re.sub(r'[,，\s]+', '', clean)
    if clean in exact_acks or clean_no_punc in exact_acks:
        return True
    if re.match(r'^(?:已?收到|已阅|明白了?|了解|知道了?|好的?|okay?|嗯+)[。！!.,，~～\s]*$', clean, re.IGNORECASE):
        return True
    if len(t_body) <= 18:
        if re.search(r'[，,；;：:]', t_body):
            return False
        if clean.startswith(("收到", "好的", "ok", "okay", "明白", "了解", "知道")):
            if not any(q in t_body for q in ("?", "？")):
                task_verbs = ("请", "帮", "执行", "修改", "排查", "确认下", "看一下", "抓紧", "继续", "但", "不过", "根因", "方案", "接口", "条件", "因为", "所以")
                if not any(v in t_body for v in task_verbs):
                    return True
    return False


def is_no_reply_needed(text):
    t = (text or "").strip()
    t_body = re.sub(r'^\s*(?:>>>|@)[^\n]*\n?', '', t).strip()
    # Only a standalone no-reply instruction is passive; quoted code/task text is actionable.
    if re.fullmatch(r"(?:本条(?:消息)?|这条(?:消息)?|请)?(?:无需回复|不需要回复|不用回复|不必回复|切勿回复)[。.!！\s]*", t_body):
        return True
    return is_pure_ack(t_body)


def push_expecting_target(agent, targets):
    """每条唤醒入队（FIFO），回复按最早一条取；不能用覆盖，否则在途轮次会被顶掉。"""
    valid = _routing.sender_ids() | {"human"}
    peers = [t for t in (targets or []) if t in valid and t != agent]
    with _roundtable_lock:
        if peers:
            _expecting_targets.setdefault(agent, []).append((time.time(), list(dict.fromkeys(peers))))
        else:
            _expecting_targets.pop(agent, None)


def pop_expecting_target(agent):
    now = time.time()
    with _roundtable_lock:
        q = _expecting_targets.get(agent)
        if not q or not isinstance(q, list):
            return []
        while q:
            item = q.pop(0)
            if isinstance(item, tuple) and len(item) == 2:
                ts, targets = item
                if now - ts <= CODEX_TIMEOUT + 300 and targets:
                    return list(targets)
            elif isinstance(item, list) and item:
                return list(item)
        return []




_recent_replies = {}
_recent_lock = threading.Lock()


def _is_duplicate_reply(sender, text, to=None):
    """同一 agent 30 秒内说同样的话只留一条（human 不参与，避免吞掉用户重复发送）。"""
    if sender not in (_routing.sender_ids() | {"watch"}):
        return False
    # 只按「发送者 + 归一化文本」判重，不再把收件人算进 key：
    # 同一句话走两个出口（默认投 human / 投 dsh）时 key 不同就都会放行，12:08:46 与 12:08:54 就是这样重复的。
    tgt = tuple(sorted(to)) if isinstance(to, (list, tuple)) else (to,)
    key = (sender, " ".join((text or "").split()))
    now = time.time()
    with _recent_lock:
        last = _recent_replies.get(key)
        _recent_replies[key] = now
        for k, v in list(_recent_replies.items()):
            if now - v > 120:
                _recent_replies.pop(k, None)
        return last is not None and (now - last) < 30











def push_img_to_cloud(path):
    """把新图片推一份到阿里云，手机端直接加载，不用走中继取图。"""
    try:
        import sys as _sys
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_img.py")
        subprocess.Popen([_sys.executable, script, path],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def write_send_audit(sender, to_list, text):
    """审计写入不应拖慢 /api/send 的响应。"""
    try:
        with open(os.path.join(DATA_DIR, "send-audit.jsonl"), "a", encoding="utf-8") as af:
            af.write(json.dumps({"ts": time.time(), "from": sender, "to": to_list,
                                 "len": len(text), "head": text[:60]}, ensure_ascii=False) + "\n")
    except Exception:
        pass




import routing as _routing


def _arrow_targets():
    """可被 >>> 点名的名字，全部来自 providers.json（见 routing.py）。"""
    return {n.lower() for n in _routing.target_names()}

def split_arrow_blocks(text):
    """按行首 >{2,} 目标名 切块，代码围栏（```）里的不算。"""
    blocks, cur, in_fence = [], [], False
    for line in (text or "").splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        sline = line.strip()
        is_arrow = False
        if not in_fence and sline.startswith(">>"):
            # 用别名前缀匹配，而不是「取第一个空白 token 再查集合」：
            # 后者在 >>> MiMo你好 这种无空格写法下会整块失效（Argon 09:29 review）。
            m = re.match(r"^>{2,}\s*@?\s*(" + _routing.arrow_alt() + r")(?![0-9A-Za-z_])", sline, re.IGNORECASE)
            if m:
                is_arrow = True
        if is_arrow:
            if cur:
                blocks.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b]



def normalize_agent_id(value):
    name = str(value).strip().lower()
    mapped = _routing.alias_map().get(name)
    if mapped:
        return mapped
    return {'gpt': 'codex', 'chatgpt': 'codex', 'ds': 'dsh', 'deepseek': 'dsh',
            'gemini': 'ag', 'antigravity': 'ag', 'argon': 'arch',
            '架构师': 'arch', '飞鱼': 'dsh', '豆包': 'doubao', 'seedance': 'doubao', '炫炫': 'human', '主人': 'human'}.get(name, name)


def normalize_recipients(values):
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError('bad recipients')
    result = list(dict.fromkeys(normalize_agent_id(v) for v in values))
    if 'narrator' in result:
        result = [v for v in result if v != 'narrator']
        if not result or 'human' not in result:
            result.append('human')
    allowed = _routing.agent_ids() | {'watch'}
    if any(v not in allowed for v in result):
        raise ValueError('unknown recipient')
    return result




from broadcast_policy import BroadcastPolicy
_broadcast_policy = BroadcastPolicy(lambda target, messages, generation, reason: _deliver_to_target(target, messages, generation, reason))

_broadcast_enabled = False
_broadcast_generation = time.time_ns()
_broadcast_message_generations = {}


def broadcast_state():
    with _lock:
        return {"enabled": _broadcast_enabled, "generation": _broadcast_generation}


def set_broadcast_mode(enabled):
    global _broadcast_enabled, _broadcast_generation
    if type(enabled) is not bool:
        raise ValueError("enabled 必须是布尔值")
    prev_enabled = _broadcast_enabled
    with _lock:
        if _broadcast_enabled != enabled:
            _broadcast_generation += 1
            _broadcast_enabled = enabled
            _broadcast_policy.reset(_broadcast_generation, enabled=enabled)
        state = broadcast_state()
    if not enabled:
        with _agent_queue_lock:
            for name, items in _agent_queues.items():
                _agent_queues[name] = [item for item in items if not (
                    isinstance(item, dict) and item.get("broadcast_generation") is not None)]
    if prev_enabled != enabled:
        try:
            if enabled:
                notice = (
                    "【系统通知】已开启「全场广播模式（茶话会模式）」：每条消息全员互通、全员可收。"
                    "在此模式下请大家自由发言、发挥创造力，展现各自鲜明性格、大胆吐槽或直接观点交锋；"
                    "如果没什么想说的，请直接输出 [[silent]]（或 [[slient]]）保持静默；"
                    "回复当中严禁出现「收到」「OK」「好的」「明白」等工作模式确认词（无论是单发还是作为开头前缀，程序层面均会自动掐断或剥离）！"
                    "只有炫炫明确要求才可修改文件，成员发言不得触发修改。其他成员消息按收件人每3条或首条满60秒投递，用户消息即时投递；是否发言由各成员自主决定。"
                )
            else:
                notice = "【系统通知】已关闭「全场广播模式」，恢复常驻「工作模式」：收到指向消息请正常响应或简短回复「收到/OK」，无需发言可回复 [[silent]]。"
            message = append_msg("system", notice, kind="system", to=list(dispatch_targets()), schedule=enabled)
            if not enabled and message and message.get('id'):
                schedule_batch(notice, 'system', message['id'])
        except Exception:
            pass
    return state


def broadcast_active(generation):
    state = broadcast_state()
    return state["enabled"] and state["generation"] == generation


def clean_broadcast_routes(text):
    names = "|".join(re.escape(name) for name in sorted(_routing.target_names(), key=len, reverse=True))
    if not names:
        return text
    target = r"(?:" + names + r")(?=$|[\t :：,，、+/])"
    pattern = r"^[ \t]*(?:>{2,}|@)[ \t]*" + target + r"(?:[\t ,，、+/]+" + target + r")*[\t :：,，、+/]*"
    return re.sub(pattern, "", text, flags=re.MULTILINE | re.IGNORECASE).strip()


from file_delivery import FileStore, MAX_SIZE as FILE_MAX_BYTES
import ticket_flow as _ticket_flow

_tickets = _ticket_flow.TicketFlow(DATA_DIR, os.path.join(cfg.ROOT, 'cooperation', 'tickets'))
_tickets_events = []
if not any(_tickets.config()["defaults"].values()):
    _tickets.set_config({"defaults": {"name": ["mimo"], "plan": ["arch"], "assign": ["dsh"],
                                      "verify": ["codex"], "delivery": ["mimo"]}})
_file_store = FileStore(cfg.ROOT, cfg.WORKSPACE, DATA_DIR)
# Existing exports retained for callers and acceptance scripts.
FILE_ROOTS = tuple(_file_store.roots)

# 语音转文字：复用系统内置/当前目录 stt 里的 whisper 引擎与 venv
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STT_PY = os.path.join(_ROOT_DIR, "stt", "venv", "Scripts", "python.exe")
STT_SCRIPT = os.path.join(_ROOT_DIR, "stt", "transcribe.py")
FFMPEG = "ffmpeg"


def open_desktop_path(path, action):
    """Use the interactive Windows Shell, not the worker's inherited desktop."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'desktop-file-open.ps1')
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', script],
            input=json.dumps({'path': path, 'action': action}, ensure_ascii=False),
            encoding='utf-8', errors='replace', capture_output=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise OSError('Windows 打开操作超时，请重试') from None
    try:
        payload = json.loads(result.stdout.lstrip('\ufeff'))
    except ValueError:
        raise OSError('Windows 文件打开服务未返回有效结果') from None
    if result.returncode or not payload.get('ok'):
        raise OSError(payload.get('error') or 'Windows 文件打开失败')
    return payload






def append_msg(sender, text, kind="text", to=None, defer_side_effects=False, schedule=True, client_mid=None,
               broadcast_generation=None, file_meta=None):
    sender = normalize_agent_id(sender)
    to = normalize_recipients(to)
    mode = broadcast_state()
    inherited_generation = (broadcast_generation if broadcast_generation is not None
                            else getattr(_brainstorm_local, "broadcast_generation", None))
    if inherited_generation is not None:
        mode = {"enabled": True, "generation": inherited_generation}
    is_broadcast = mode["enabled"] and (
        (kind == "text" and sender in (_routing.sender_ids() | {"human"})) or
        (kind == "system" and sender == "system" and isinstance(text, str) and "全场广播模式" in text)
    )
    # 所有入口统一拦截，避免某个模型通道绕过局部判断把静默标记写进页面。
    if sender in _routing.sender_ids() and (
            is_silent(text) or is_silent_text(text) or (mode["enabled"] and is_pure_ack(text))):
        is_bcast = bool(mode.get("enabled") or broadcast_state().get("enabled"))
        if is_bcast:
            pop_expecting_target(sender)
            _active_reply_target.pop(sender, None)
            return {"id": "", "silent": True}
        else:
            pop_expecting_target(sender)
            _active_reply_target.pop(sender, None)
            return {"id": "", "silent": True}

    if _is_duplicate_reply(sender, text, to):
        return None
    # 静默/判定类系统提示不上板（保留未产出提醒与广播模式开关通知，让炫炫与全体成员知情）
    # 坏输出（工具语法/异种语言混入）不进消息流，改成一条系统提示叫人重跑
    if kind == "text" and sender in _routing.sender_ids() and sender != "dsh" and is_garbled_reply(text):
        try:
            append_msg("system", "（%s 本轮输出异常：疑似把工具调用或异种语言当正文，已拦下，请重跑）" % sender, kind="system")
        except Exception:
            pass
        return None
    if kind == "system" and isinstance(text, str) and "全场广播模式" not in text and any(
            k in text for k in ("无需回复", "静默", "判定这轮")):
        return None
    if sender == "codex" and _codex_seen is not None and isinstance(text, str):
        _codex_seen.add(codex_key(text))

    # 0. 支持单轮输出多条独立 >>> / >> 指向消息自动拆分（解决多目标混合或带前置说明的问题）
    if not is_broadcast and kind == "text" and sender in _routing.sender_ids() and isinstance(text, str):
        cleaned_first = clean_agent_text(sender, text)
        all_blocks = split_arrow_blocks(cleaned_first)
        arrow_blocks = [b for b in all_blocks if re.match(r'^\s*>{2,}', b)]
        first_is_arrow = bool(all_blocks and re.match(r'^\s*>{2,}', all_blocks[0]))
        lead_block = "" if first_is_arrow else (all_blocks[0].strip() if all_blocks else "")
        if len(arrow_blocks) > 1:
            last_m = None
            if lead_block:
                append_msg(sender, lead_block, kind=kind, to=to)
            for b in arrow_blocks:
                last_m = append_msg(sender, b, kind=kind, to=None)
            return last_m
        elif len(arrow_blocks) == 1:
            if lead_block:
                append_msg(sender, lead_block, kind=kind, to=to)
            text = arrow_blocks[0]
            to = None  # 箭头块必须由后方逻辑重新解析专属收件人

    # 1. 显式指向机制最高优先级：先认行首 >>> 或 >> 或 @ 标签并剥离，其次才查待回复队列 _expecting_targets
    if is_broadcast:
        to = []
        text = clean_broadcast_routes(text)
    elif not to:
        to = []
        m_arrow = re.match(r'^\s*(?:>{2,}|@)\s*(.+)', text or "", re.DOTALL)
        if m_arrow:
            rest = m_arrow.group(1)
            target_names = _routing.target_names()
            if "\n" in rest:
                target_str, body_text = rest.split("\n", 1)
                text = body_text.strip()
            else:
                pat = r'^((?:(?:' + '|'.join(re.escape(n) for n in target_names) + r'|[@+,/，、和与及\s])\s*)+)(.*)$'
                m2 = re.match(pat, rest, re.IGNORECASE | re.DOTALL)
                if m2:
                    target_str = m2.group(1)
                    rem = m2.group(2).strip(' :：,，	\n')
                    text = rem if rem else text
                else:
                    target_str = rest

            # 收件人解析统一走 routing（providers.json 的 aliases），不再手工堆 if
            for _rid in _routing.resolve_names(target_str):
                if _rid not in to:
                    to.append(_rid)
        else:
            active_tgt = _active_reply_target.get(sender)
            if active_tgt:
                to = list(active_tgt)
            else:
                to = pop_expecting_target(sender)

        # 工单中间流转过程（[#id:stage] 且非 delivery）属于系统内部协同，不作为发给人类的主动消息
        m_ticket_stage = re.match(r"^\s*\[#([0-9a-zA-Z_-]{4,}):([a-z]+)\]", text or "")
        if m_ticket_stage and m_ticket_stage.group(2) != "delivery":
            to = ["ticket"]
        elif not to and sender in _routing.sender_ids() and kind == "text":
            to.append("human")

        # 剔除自身，杜绝 self-loop (如 dsh >>> dsh)
        to = [t for t in to if t != sender]

        # 炫炫不写指向 = 全员提问：落盘时就补成全收件人，
        # 免得走「广播分支」时没进 _to_map，回复可能被丢或延迟。
        if not to and sender == "human":
            # 没有显式指向时只给我（dsh），不再默认群发
            to = ["dsh"]

    text = clean_agent_text(sender, text)
    if mode["enabled"] and kind == "text" and sender in _routing.sender_ids() and isinstance(text, str):
        if not (is_silent(text) or is_silent_text(text) or is_pure_ack(text)):
            text = strip_broadcast_ack_prefix(text)
    if sender in _routing.sender_ids() and (
            is_silent(text) or is_silent_text(text) or (mode["enabled"] and is_pure_ack(text))):
        is_bcast = bool(mode.get("enabled") or broadcast_state().get("enabled"))
        if is_bcast:
            pop_expecting_target(sender)
            _active_reply_target.pop(sender, None)
            return {"id": "", "silent": True}
        else:
            pop_expecting_target(sender)
            _active_reply_target.pop(sender, None)
            return {"id": "", "silent": True} 
    if sender == "codex" and _codex_seen is not None and isinstance(text, str):
        _codex_seen.add(codex_key(text))

    now = time.time()
    # 2. 原子加锁去重与落盘写入，彻底杜绝双线程并发重入导致重复两发
    with _lock:
        try:
            recent = read_msgs(0)[-12:]
            for m in recent:
                if m.get("sender") == sender:
                    ts_gap = now - float(m.get("ts", 0))
                    if ts_gap < 45.0:
                        if client_mid and m.get("client_mid") and m.get("client_mid") == client_mid and (m.get("delivery_text", m.get("text")) or "").strip() == (text or "").strip():
                            return m
                        if sender == "dsh" and _dsh_dup_recent(text, window=45.0):
                            return m
                    if ts_gap < 6.0 and (m.get("delivery_text", m.get("text")) or "").strip() == (text or "").strip() and sorted(m.get("to") or []) == sorted(to or []):
                        return m
        except Exception:
            pass

        msg = {"id": uuid.uuid4().hex[:8], "ts": now, "sender": sender, "text": text, "kind": kind, "to": to or []}
        if file_meta:
            msg["file_meta"] = file_meta
        if is_broadcast:
            msg.update(broadcast=True, broadcast_generation=mode["generation"])
            _broadcast_message_generations[msg["id"]] = mode["generation"]
        if client_mid:
            msg["client_mid"] = str(client_mid)
    delivered = []
    delivery_text = text
    if kind == "text" and sender in (_routing.sender_ids() | {"narrator", "watch"}) and isinstance(text, str):
        for source in _file_store.markers(text):
            try:
                meta = _file_store.archive(path=source)
                delivered.append(meta)
                msg["delivery_text"] = delivery_text
                # Strip only successful deliveries; rejected paths stay visible with file_errors.
                text = re.sub(r"\[\[file:([^\]\r\n]+)\]\]",
                              lambda m: "" if m.group(1).strip() == source else m.group(0), text, flags=re.I)
                text = re.sub(r"\[发送文件\]\(([^)\r\n]+)\)",
                              lambda m: "" if m.group(1).strip() == source else m.group(0), text)
                msg["text"] = text.strip() or "已发送文件"
            except (ValueError, OSError, OverflowError) as exc:
                msg.setdefault("file_errors", []).append({"path": source, "error": str(exc)})
    if kind in ("image", "file") and isinstance(text, str):
        try:
            supplied = file_meta or {}
            url = supplied.get("url") or text
            path = _file_store.resolve_url(url)
            msg["file_meta"] = _file_store.metadata(path, supplied.get("name"),
                                                   supplied.get("source_path", ""), url)
            if hasattr(_file_store, "record_group_send"):
                _file_store.record_group_send(msg["file_meta"], sender=sender,
                                              msg_id=msg["id"], ts=msg["ts"])
        except (ValueError, OSError):
            pass
    with open(MSG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    for meta in delivered:
        append_msg(sender, meta["url"], kind="image" if meta["is_image"] else "file",
                   to=list(msg["to"]), schedule=False, file_meta=meta)
        if meta["is_image"]:
            push_img_to_cloud(meta["abs_path"])

    _remember_routed_message(msg)
    _brainstorms.question(msg)
    _brainstorms.answer(client_mid if client_mid and client_mid.startswith("turn:") else getattr(_brainstorm_local, "turn", None), msg)

    # Publish recipients before any asynchronous scheduling can observe the message.
    _to_map[msg["id"]] = list(msg["to"])
    if is_broadcast:
        _to_map[msg["id"]] = [target for target in dispatch_targets() if target != sender]

    try:
        if sender not in ("dsh", "human") and "dsh" in (to or []):
            inbox_push(sender, text)
    except Exception:
        pass
    if defer_side_effects:
        threading.Thread(target=post_append_msg,
                         args=(sender, text, kind, msg["id"], schedule, is_broadcast), daemon=True).start()
        return msg
    post_append_msg(sender, text, kind, msg["id"], schedule, is_broadcast)
    return msg




def post_append_msg(sender, text, kind, message_id, schedule=True, broadcast=False):
    """持久化后的调度与任务 IO；HTTP 请求可选择将其移出响应路径。"""
    # 普通聊天不改变工单生命周期；仅由明确 stage_complete 事件推进。
    # 纯『收到』确认消息只落盘展示与计数，不再触发下游模型二次唤醒（防止死循环与占满队列）
    if schedule and (
            (kind == "text" and sender in (_routing.sender_ids() | {"human", "narrator"})) or
            (broadcast and kind == "system")
    ):
        if broadcast or not (sender in _routing.sender_ids() and is_pure_ack(text)):
            schedule_batch(text, sender, message_id)
    recipients = _to_map.get(message_id, [])
    if kind == "text" and sender == "human" and "dsh" in recipients:
        threading.Thread(target=classify_shadow, args=(text,), daemon=True).start()
    if kind == "text" and sender == "human" and "mimo" in recipients:
        # 已停用：每条消息都触发一次 MiMo JSON 提取，后台白烧额度（七栏工单流已接管）
        # threading.Thread(target=extract_goal, args=(text,), daemon=True).start()
        pass
    if kind == "text" and sender in _routing.sender_ids():
        try:
            pass  # 聊天文本不再充当结构化执行结果
        except Exception:
            pass
        on_roundtable_reply(sender, text)
        # 已停用：每 35 秒一次 extract_roles 也会调 MiMo，属高频静默抽血
        # threading.Timer(35.0, extract_roles).start()
        pass


def deleted_message_ids():
    """Keep deletion markers separate so concurrent message appends stay intact."""
    path = os.path.join(DATA_DIR, "deleted-messages.json")
    try:
        with open(path, encoding="utf-8") as stream:
            return set(json.load(stream))
    except FileNotFoundError:
        return set()


def delete_message(message_id):
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("需要消息 ID")
    with _lock:
        deleted = deleted_message_ids()
        if message_id in deleted:
            return True
        if not any(m.get("id") == message_id for m in read_msgs()):
            return False
        deleted.add(message_id)
        path = os.path.join(DATA_DIR, "deleted-messages.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(sorted(deleted), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        return True


def read_msgs(since=0.0, limit=None, date_filter="", public_only=False, sender="", q=""):
    out = []
    if not os.path.exists(MSG_FILE):
        return out
    sender_key = str(sender or "").strip().lower()
    q_key = str(q or "").strip().lower()
    deleted = deleted_message_ids()
    with open(MSG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            if m.get("id") in deleted:
                continue
            if sender_key and str(m.get("sender") or "").lower() != sender_key:
                continue
            if q_key:
                _hay = (str(m.get("text") or "") + " " + " ".join(str(x) for x in (m.get("to") or []))).lower()
                if q_key not in _hay:
                    continue
            ts = m.get("ts", 0)
            if date_filter:
                try:
                    if time.strftime("%Y-%m-%d", time.localtime(ts)) != date_filter:
                        continue
                except (TypeError, ValueError, OverflowError):
                    continue
            if date_filter or ts > since:
                out.append(m)
    if limit is not None and limit > 0:
        out = out[-limit:]
    return out







_CS = None


def get_codex_server():
    global _CS
    if _CS is None:
        try:
            import codex_appserver
            _CS = codex_appserver.CodexServer()
            _CS.start()
        except Exception as e:
            append_msg("codex", "（app-server 起不来：%s）" % e, "system")
            _CS = None
    return _CS




SUPERVISOR_FILE = os.path.join(DATA_DIR, "supervisor.json")
_SUP_LAST = {"ts": 0}




def _sup_record(level, what, agent=""):
    try:
        items = []
        if os.path.exists(SUPERVISOR_FILE):
            items = json.load(open(SUPERVISOR_FILE, encoding="utf-8")) or []
        items.append({"ts": time.time(), "level": level, "what": what[:120], "agent": agent})
        json.dump(items[-50:], open(SUPERVISOR_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass




def ask_antigravity(text):
    bootstrap.sync_workspace_rules()
    mark("ag", True)
    try:
        activity("ag", "running", (text or "")[:100], phase="executing_no_telemetry")
    except Exception:
        pass
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import antigravity_bridge as ag
        def initialize(prompt):
            baseline = ag.transcript_step()
            ok, _ = ag.send(prompt)
            if not ok:
                return False
            reply = ag.wait_reply2(baseline, timeout=180, accept_timeout=30)
            return bool(reply)
        # 初始化那轮已停用：炫炫 17:20 要求砍掉 Gemini 的初始化消息。
        # ensure_client_session("ag", ag.latest_conv_id(), initialize)
        # Gemini receives only sender-labelled original messages from the router.
        payload = text
        # 发送前检查 Gemini transcript 是否仍在执行上一轮工具/生成，防止上一轮超时退回后新消息抢跑撞车
        _wait_start = time.monotonic()
        while ag.is_transcript_busy(max_age=180) and (time.monotonic() - _wait_start < 120):
            activity("ag", "running", "等待上一轮生成收尾...", phase="streaming")
            track_beat("ag", "等待上一轮收尾")
            time.sleep(1.5)
        baseline = ag.transcript_step()
        # ag.send 走的是本地语言服务，可能永久阻塞；用线程加超时兜住
        box = {}
        def _send():
            try:
                box["r"] = ag.send(payload)
            except Exception as e:
                box["e"] = e
        th = threading.Thread(target=_send, daemon=True)
        th.start()
        th.join(timeout=30)
        if th.is_alive():
            raise TimeoutError("Gemini 客户端 30s 未响应")
        if "e" in box:
            agent_failure("ag", box["e"])
            append_msg("ag", "（Gemini 发送异常：%s）" % str(box["e"])[:80], "system")
            return
        ok, msg = box.get("r", (False, "无返回"))
        if not ok:
            agent_failure("ag", msg)
            append_msg("ag", "（Gemini 发送失败：%s）" % msg, "system")
            return
        def _beat(msg):
            track_beat("ag", str(msg))
            activity("ag", "running", str(msg), phase="streaming")
        reply = ag.wait_reply2(baseline, timeout=180, accept_timeout=30, on_beat=_beat)
        if reply:
            turn = getattr(_brainstorm_local, "turn", None)
            if turn and reply.strip() not in ("[[silent]]", "[[静默]]", "[[skip]]"):
                # Archive the exact final returned by this dispatch, not a later chat guess.
                _brainstorms.answer(turn, {"id": "ag-final:" + turn, "sender": "ag", "kind": "text",
                                          "to": ["human"], "text": reply, "ts": time.time()})
            if is_silent(reply):
                return
            # 写入统一交给 ag_watch，避免两个通道各写一次
            pass
    except Exception as e:
        agent_failure("ag", e)
        append_msg("ag", "（Antigravity 通道出错：%s）" % e, "system")
    finally:
        mark("ag", False)
        try:
            cur = (_activity.get("ag") or {}).get("timing") or {}
            cur["completed_at"] = time.time()
            verdict = track_verdict_now("ag")
            if verdict in ("timeout", "timeout_5m", "loop", "error"):
                activity("ag", verdict, "", phase="failed", timing=cur)
            else:
                activity("ag", "idle", "", phase="completed", timing=cur)
        except Exception:
            pass




def ask_doubao(text):
    """本地豆包桌面端（CDP 9229 通道），显式点名 >>> 豆包 时触发。"""
    mark("doubao", True)
    _ok_flag = {"ok": False}

    def _beat(msg):
        try:
            track_beat("doubao", msg)
            activity("doubao", "running", msg, phase="streaming")
        except Exception:
            pass

    try:
        activity("doubao", "running", (text or "")[:100], phase="streaming")
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import doubao_bridge as db
        ensure_client_session("doubao", db.session_key(),
                              lambda prompt: db.ask(prompt, timeout=180, on_beat=_beat)[0])
        ok, reply = db.ask(text, timeout=180, on_beat=_beat)
        if ok and reply and reply.strip():
            append_msg("doubao", reply.strip())
            _ok_flag["ok"] = True
        elif not ok:
            agent_failure("doubao", reply)
            append_msg("doubao", "（豆包通信异常：%s）" % str(reply)[:80], "system")
    except Exception as e:
        agent_failure("doubao", e)
        append_msg("doubao", "（豆包通道出错：%s）" % str(e)[:80], "system")
    finally:
        mark("doubao", False)
        # 只有真正成功才洗成 idle；失败/超时保留失败态，别一秒内变绿
        if _ok_flag["ok"]:
            try:
                activity("doubao", "idle", "", phase="completed")
            except Exception:
                pass




def ask_workbuddy(text):
    """本地 WorkBuddy 桌面端（CDP 9230 通道），显式点名 >>> WorkBuddy 时触发。"""
    _ok_flag = False
    def _beat(msg):
        track_beat("workbuddy", str(msg))
        activity("workbuddy", "running", str(msg), phase="streaming")
    mark("workbuddy", True)
    try:
        activity("workbuddy", "running", (text or "")[:100], phase="streaming")
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import client_probe
        status = client_probe.get_client_status("workbuddy", force_refresh=True)
        if not status.get("cdp_ready"):
            ready, reason = client_probe.ensure_client_runtime("workbuddy")
            if not ready:
                raise RuntimeError("WorkBuddy CDP 未就绪：" + str(reason))
        import workbuddy_bridge as wb
        ensure_client_session("workbuddy", wb.session_key(), lambda prompt:
                              wb.ask(prompt, timeout=180, check_premise=False, on_beat=_beat)[0])
        ok, reply = wb.ask(text, timeout=180, check_premise=False, on_beat=_beat)
        _ok_flag = bool(ok)
        if ok and reply and reply.strip():
            append_msg("workbuddy", reply.strip())
        elif not ok:
            agent_failure("workbuddy", reply)
            append_msg("workbuddy", "（WorkBuddy 通信异常：%s）" % str(reply)[:80], "system")
    except Exception as e:
        agent_failure("workbuddy", e)
        append_msg("workbuddy", "（WorkBuddy 通道出错：%s）" % str(e)[:80], "system")
    finally:
        mark("workbuddy", False)
        try:
            if _ok_flag and track_verdict_now("workbuddy") not in ("timeout", "timeout_5m", "loop", "error"):
                activity("workbuddy", "idle", "", phase="completed")
        except Exception:
            pass

_pending = []
_pending_lock = threading.Lock()
_pending_timer = None
DEBOUNCE = 1.5


_codex_seen = None


def codex_key(text):
    import re as _r
    return _r.sub(r"\s+", "", text or "")[:50]


def codex_watch():
    """读最新的 rollout，把新的 assistant 消息搬进来。"""
    global _codex_seen
    import glob as _g
    time.sleep(20)
    while True:
        try:
            if "codex" in _busy:
                time.sleep(2)
                continue
            if _codex_seen is None:
                _codex_seen = set(codex_key(m.get("text", "")) for m in read_msgs(0) if m.get("sender") == "codex")
                # 首次启动：把会话里已有的 assistant 消息全部标记为已读，避免把历史重播一遍
                try:
                    import glob as _g2
                    _files = _g2.glob(os.path.join(os.environ["USERPROFILE"], ".codex", "sessions", "**", "rollout-*.jsonl"), recursive=True)
                    if _files:
                        _newest = max(_files, key=os.path.getmtime)
                        with open(_newest, "r", encoding="utf-8", errors="ignore") as _f:
                            for _line in _f:
                                try:
                                    _o = json.loads(_line)
                                except Exception:
                                    continue
                                _p = (_o.get("payload") or {}) if _o.get("type") == "response_item" else {}
                                if _p.get("type") == "message" and _p.get("role") == "assistant":
                                    _t = "".join((c.get("text") or "") for c in _p.get("content", []))
                                    if _t.strip():
                                        _codex_seen.add(codex_key(_t))
                except Exception:
                    pass
            active = os.path.join(DATA_DIR, "codex-active.json")
            newest = None
            try:
                with open(active, "r", encoding="utf-8") as af:
                    info = json.load(af)
                cand = info.get("path")
                if cand and os.path.exists(cand):
                    newest = cand
            except Exception:
                newest = None
            if newest is None:
                files = _g.glob(os.path.join(os.environ["USERPROFILE"], ".codex", "sessions", "**", "rollout-*.jsonl"),
                                recursive=True)
                if files:
                    newest = max(files, key=os.path.getmtime)
            if newest:
                cursor_file = os.path.join(DATA_DIR, "codex-cursor.json")
                cursor = {}
                try:
                    with open(cursor_file, "r", encoding="utf-8") as cf:
                        cursor = json.load(cf)
                except Exception:
                    cursor = {}
                start_line = cursor.get("lines", 0) if cursor.get("path") == newest else 10**9
                texts = []
                all_lines = []
                with open(newest, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                if start_line > len(all_lines):
                    start_line = len(all_lines)
                for line in all_lines[start_line:]:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                        except Exception:
                            continue
                        if o.get("type") == "response_item":
                            p = o.get("payload") or {}
                            if p.get("type") == "message" and p.get("role") == "assistant":
                                t = "".join((c.get("text") or "") for c in p.get("content", [])).strip()
                                if t:
                                    texts.append(t)
                try:
                    with open(cursor_file, "w", encoding="utf-8") as cf:
                        json.dump({"path": newest, "lines": len(all_lines)}, cf)
                except Exception:
                    pass
                for t in texts[-4:]:
                    k = codex_key(t)
                    if k in _codex_seen or len(t) < 4:
                        continue
                    _codex_seen.add(k)
                    append_msg("codex", t)
        except Exception:
            pass
        time.sleep(5)


def mark(name, on):
    if on:
        _busy[name] = time.time()
    else:
        _busy.pop(name, None)


def activity(name, state, task="", files=None, phase=None, timing=None):
    now = time.time()
    with _activity_lock:
        previous = _activity.get(name) or {}
        verdict = (TRACK.get(name) or {}).get("verdict")
        if state in ("idle", "queued", "running") and (
                previous.get("state") in ("timeout", "error") or
                verdict in ("timeout", "timeout_5m", "loop", "error")):
            return
        active = ("running", "queued")
        continuing = state in active and previous.get("state") in active
        started = (previous.get("started") or now) if continuing else now
        rec = {"state": state, "task": task or "", "started": started,
               "updated": now, "files": files or [],
               "phase": phase or ("running" if state == "running" else ("idle" if state == "idle" else state))}
        if timing:
            rec["timing"] = timing
        _activity[name] = rec


def sync_dsh_activity():
    """Follow Conflux inbound/outbound as well as OneBot model lifecycle events."""
    path = os.path.join(cfg.DATA_ROOT, 'media', 'qq-trace.jsonl')
    try:
        with open(path, 'rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 512 * 1024))
            raw = stream.read().decode('utf-8', errors='replace')
        for line in reversed(raw.splitlines()):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('chatId') != 'private:999000001':
                continue
            stage = event.get('stage')
            reason = event.get('reason', '')
            started = (stage == 'agent' and reason == '模型回合开始') or stage == 'inbound'
            ended = (stage == 'agent' and (
                reason.startswith('模型回合结束') or reason.startswith('模型回合出错')))
            ended = ended or (stage == 'outbound' and reason == '回复已回传 Conflux')
            if not (started or ended):
                continue
            stamp = event.get('ts', 0) / 1000
            current = _activity.get('dsh') or {}
            # Reject stale starts AND ends before applying the expiry rule. Older
            # starts used to cancel a fresh injection on every status poll.
            if stamp < _LAST_INJECT.get('ts', 0):
                continue
            if stamp <= current.get('updated', 0):
                return
            if started and (time.time() - stamp > 300):
                return
            task = current.get('task', '') if started else ''
            failed = event.get('ok') is False or reason.startswith('模型回合出错')
            if failed:
                track_end('dsh', 'error')
                activity('dsh', 'error', reason, phase='error')
            else:
                if started:
                    track_start('dsh', task)
                activity('dsh', 'running' if started else 'idle', task,
                         phase='streaming' if started else 'completed')
                if ended:
                    track_end('dsh', 'done')
            _activity['dsh']['updated'] = stamp
            mark('dsh', started)
            return
    except OSError:
        return



def sync_ag_activity():
    """Gemini (Antigravity)'s real transcript lifecycle, not just the dispatch thread."""
    try:
        import antigravity_bridge as ag
        path = ag.transcript_path()
        if not path or not os.path.exists(path):
            return
        st = os.stat(path)
        now = time.time()
        mtime = st.st_mtime
        age = now - mtime

        with open(path, 'rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 48 * 1024))
            raw = stream.read().decode('utf-8', errors='replace')

        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        if not lines:
            return

        recent_steps = []
        for l in lines[-12:]:
            try:
                recent_steps.append(json.loads(l))
            except Exception:
                continue

        if not recent_steps:
            return

        last_step = recent_steps[-1]
        step_idx = last_step.get("step_index", 0)
        step_type = last_step.get("type", "")
        step_src = last_step.get("source", "")
        step_stat = last_step.get("status", "")
        has_tools = bool(last_step.get("tool_calls"))
        content = last_step.get("content") or ""

        is_unfinished = (
            has_tools or
            step_type in ("GENERIC", "USER_INPUT") or
            step_stat != "DONE" or
            (step_src == "MODEL" and step_type == "PLANNER_RESPONSE" and not content and not has_tools)
        )

        is_final_reply = (
            step_src == "MODEL" and
            step_type == "PLANNER_RESPONSE" and
            step_stat == "DONE" and
            not has_tools and
            bool(content.strip())
        )

        has_current_ag_task = bool(_agent_current_tasks.get("ag"))

        # 如果最后一步已经明确是最终文本回复（交卷完毕），且当前 Hub 没有等待派发中的新任务，则立即判定为空闲
        if is_final_reply and not has_current_ag_task:
            is_running = False
            task_desc = ""
        elif is_unfinished and age < 600:
            is_running = True
            task_desc = "执行工具 / 生成中 (step %s)" % step_idx
        elif has_current_ag_task and age < 600:
            is_running = True
            task_desc = "执行中 (step %s · %s)" % (step_idx, step_type)
        else:
            is_running = False
            task_desc = ""

        current = _activity.get('ag') or {}

        if is_running:
            if current.get('state') != 'running':
                activity('ag', 'running', task_desc, phase='streaming')
                _activity['ag']['started'] = mtime
                track_start('ag', task_desc)
            else:
                _activity['ag']['updated'] = mtime
                track_beat('ag', task_desc)
            mark('ag', True)
        else:
            if current.get('state') == 'running':
                t = TRACK.get('ag') or {}
                th = t.get('thread')
                if has_current_ag_task and th and th.is_alive():
                    pass
                else:
                    activity('ag', 'idle', '', phase='completed')
                    _activity['ag']['updated'] = mtime
                    mark('ag', False)
                    track_end('ag', 'done')
    except Exception:
        pass


_EXTERNAL_ACTIVITY_ACTIVE = set()

def sync_all_agents_activity():
    """Merge worker records; unrelated completed runs cannot end a live run."""
    sync_dsh_activity()
    sync_ag_activity()
    from agent_activity import DIRECTORY
    grouped = {}
    now = time.time()
    for path in DIRECTORY.glob('*.json'):
        try:
            rec = json.loads(path.read_text(encoding='utf-8'))
            name = normalize_agent_id(rec['agent'])
            if name not in _routing.sender_ids():
                continue
            grouped.setdefault(name, []).append(rec)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for name in _EXTERNAL_ACTIVITY_ACTIVE - grouped.keys():
        grouped[name] = [{"state": "idle", "updated": now}]
    for name, records in grouped.items():
        live = [r for r in records if r.get('state') == 'running' and now-r.get('updated', 0) < 30]
        rec = max(live or records, key=lambda r: r.get('updated', 0))
        if live:
            _EXTERNAL_ACTIVITY_ACTIVE.add(name)
            activity(name, 'running', rec.get('task', ''), phase='running')
            _activity[name]['started'] = min(r['started'] for r in live)
            # Keep Hub's own thread ledger; external runs have no Hub thread.
            tr = track_state(name)
            if tr.get('state') != 'running':
                track_start(name, rec.get('task', ''))
            track_beat(name, rec.get('task', ''))
            mark(name, True)
        elif name in _EXTERNAL_ACTIVITY_ACTIVE:
            _EXTERNAL_ACTIVITY_ACTIVE.discard(name)
            t = TRACK.get(name) or {}
            thread = t.get('thread')
            if thread is None or not thread.is_alive():
                activity(name, 'idle', '', phase='completed' if rec.get('state') == 'idle' else 'worker_lost')
                track_end(name, 'done')
                mark(name, False)


def file_snapshot():
    snap = {}
    for base, dirs, files in os.walk(ROOT):
        if any(sk in base for sk in ("logs", "node_modules", "data\\media", ".git")):
            continue
        for fn in files:
            p = os.path.join(base, fn)
            try:
                snap[p] = os.path.getmtime(p)
            except OSError:
                pass
    return snap


def changed_files(before):
    out = []
    if isinstance(before, dict):
        for p, t in before.items():
            try:
                if os.path.getmtime(p) != t:
                    out.append(os.path.relpath(p, ROOT).replace("\\", "/"))
            except OSError:
                pass
        return out[:8]
    for base, dirs, files in os.walk(ROOT):
        if any(sk in base for sk in ("logs", "node_modules", "data\\media")):
            continue
        for fn in files:
            p = os.path.join(base, fn)
            try:
                if os.path.getmtime(p) >= before:
                    out.append(os.path.relpath(p, ROOT).replace("\\", "/"))
            except OSError:
                pass
    return out[:8]




def schedule_batch(text, sender="human", msg_id=""):
    """同一个人的连发才合并；换人立刻把上一批独立弹出并派发，绝不把不同发送者混入同一批次。"""
    global _pending_timer
    mode = broadcast_state()
    if mode['enabled'] and (msg_id in _broadcast_message_generations):
        generation = _broadcast_message_generations[msg_id]
        if not broadcast_active(generation):
            return
        threading.Thread(target=flush_batch, args=([(sender, text, msg_id)],), daemon=True).start()
        return
    direct = _to_map.get(msg_id) or []
    if sender in ('human', 'system') or (len(direct) == 1 and direct[0] != 'human'):
        threading.Thread(target=flush_batch, args=([(sender, text, msg_id)],), daemon=True).start()
        return
    prev_batch = None
    with _pending_lock:
        if _pending and sender != _pending[-1][0]:
            if _pending_timer is not None:
                _pending_timer.cancel()
                _pending_timer = None
            prev_batch = list(_pending)
            _pending.clear()
        _pending.append((sender, text, msg_id))
        if _pending_timer is not None:
            _pending_timer.cancel()
        _pending_timer = threading.Timer(DEBOUNCE, flush_batch)
        _pending_timer.daemon = True
        _pending_timer.start()
    if prev_batch:
        threading.Thread(target=flush_batch, args=(prev_batch,), daemon=True).start()


_roundtable_lock = threading.Lock()
_active_roundtable = None
_expecting_targets = {}


def start_roundtable(topic_text, batch):
    global _active_roundtable
    with _roundtable_lock:
        if _active_roundtable and _active_roundtable.get("timer"):
            try:
                _active_roundtable["timer"].cancel()
            except Exception:
                pass
        _active_roundtable = {
            "id": uuid.uuid4().hex[:6],
            "topic": topic_text,
            "round": 1,
            "r1_replies": {},
            "r2_dispatched": False,
            "timer": None
        }


def on_roundtable_reply(sender, text):
    """当 Agent 在第一轮圆桌中回复时，记录回复，并在适当时机推进到第二轮交叉互评。"""
    global _active_roundtable
    advance_now = False
    with _roundtable_lock:
        if not _active_roundtable or _active_roundtable.get("round") != 1:
            return
        if _active_roundtable.get("r2_dispatched"):
            return
        if sender not in _active_roundtable["r1_replies"]:
            _active_roundtable["r1_replies"][sender] = text

        active_candidates = ["ag", "dsh"]
        if codex_available():
            active_candidates.append("codex")

        # 若全部能参与的 Agent 都回复了，立刻推进第二轮
        if all(a in _active_roundtable["r1_replies"] for a in active_candidates):
            advance_now = True
            _active_roundtable["r2_dispatched"] = True
        else:
            # 开启 15 秒定时器防卡死（例如某个模型超时未回，先推进已有回复的成员）
            if _active_roundtable.get("timer"):
                try:
                    _active_roundtable["timer"].cancel()
                except Exception:
                    pass
            t = threading.Timer(28.0, trigger_roundtable_round2)
            t.daemon = True
            _active_roundtable["timer"] = t
            t.start()

    if advance_now:
        threading.Thread(target=trigger_roundtable_round2, daemon=True).start()


def trigger_roundtable_round2():
    """第二轮：把彼此没收到的其他两家观点打包互喂，只开展一轮交流互评，随后自然收敛闭环。"""
    global _active_roundtable
    with _roundtable_lock:
        if not _active_roundtable or _active_roundtable.get("round") != 1:
            return
        if _active_roundtable.get("timer"):
            try:
                _active_roundtable["timer"].cancel()
            except Exception:
                pass
            _active_roundtable["timer"] = None
        _active_roundtable["round"] = 2
        _active_roundtable["r2_dispatched"] = True
        r1 = dict(_active_roundtable["r1_replies"])
        topic = _active_roundtable["topic"]

    if len(r1) < 2:
        with _roundtable_lock:
            _active_roundtable = None
        return

    try:
        with open(os.path.join(DATA_DIR, "dispatch-debug.jsonl"), "a", encoding="utf-8") as df0:
            df0.write(json.dumps({"ts": time.time(), "event": "flush_enter",
                                  "batch": [(s0, t0[:30], m0) for s0, t0, m0 in batch]}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    labels = {"human": "炫炫", **AGENT_LABELS,
              **{p["id"]: p["display_name"] for p in _routing.load_providers()}}
    for target in list(r1.keys()):
        peers = [(s, t) for s, t in r1.items() if s != target]
        if not peers:
            continue
        peer_labels = [labels.get(s, s) for s, _ in peers]
        peer_names_str = "、".join(peer_labels)
        peer_targets = [s for s, _ in peers]

        parts = ["【全员圆桌交流 · 第二轮】", "炫炫发起的话题：" + topic, ""]
        for s, t in peers:
            parts.append("▼ 【" + labels.get(s, s) + " 的观点】：\n" + t + "\n")
        parts.append("请针对 " + peer_names_str + " 的上述观点聊聊你的看法或接话交流；若无实质新观点请直接回复 [[silent]]，不要单纯客套附和。")
        bundle_body = "\n".join(parts)

        push_expecting_target(target, peer_targets)

        fn = {"codex": ask_codex, "ag": ask_antigravity, "dsh": inject_qq}.get(target)
        if fn:
            if target == "codex" and not codex_available():
                continue
            launch_agent(target, fn, bundle_body)

    def _clean_roundtable():
        global _active_roundtable
        time.sleep(120)
        with _roundtable_lock:
            _active_roundtable = None
    threading.Thread(target=_clean_roundtable, daemon=True).start()



_dispatch_seen = {}
_boot_ts = {}
_to_map = {}


def dispatch_once(target, joined, is_human=None):
    """同一目标短时间内不发重复内容，防止重复烧算力。human 发送的消息仅去重 5s（防抖动），智能体之间去重 300s。"""
    if not agent_is_enabled(target):
        try:
            append_msg("system", "（%s 已关闭，本条未分发）" % target, kind="system")
        except Exception:
            pass
        return False
    if is_human is None:
        is_human = ("【炫炫】" in joined or "【human】" in joined or "【主人】" in joined)
    import hashlib
    key = (target, hashlib.sha256(joined.encode("utf-8", "ignore")).hexdigest())
    now = time.time()
    ttl = 5 if is_human else 300
    for k in list(_dispatch_seen):
        entry = _dispatch_seen[k]
        entry_ttl = entry.get("ttl", 300) if isinstance(entry, dict) else 300
        entry_ts = entry["ts"] if isinstance(entry, dict) else entry
        if now - entry_ts > entry_ttl:
            _dispatch_seen.pop(k, None)
    if key in _dispatch_seen:
        entry = _dispatch_seen[key]
        entry_ts = entry["ts"] if isinstance(entry, dict) else entry
        entry_ttl = entry.get("ttl", 300) if isinstance(entry, dict) else 300
        if now - entry_ts <= entry_ttl:
            try:
                with open(os.path.join(DATA_DIR, "dispatch-debug.jsonl"), "a", encoding="utf-8") as _df:
                    _df.write(json.dumps({"ts": time.time(), "target": target, "deduped": True,
                                          "chars": len(joined), "ttl": entry_ttl}, ensure_ascii=False) + "\n")
            except Exception:
                pass
            return False
    _dispatch_seen[key] = {"ts": now, "ttl": ttl}
    return True


def _blocked_targets(targets):
    """过滤掉已关闭的智能体，并对显式指向关掉的角色回一条提示。"""
    keep, blocked = [], []
    for t in (targets or []):
        if agent_is_enabled(t):
            keep.append(t)
        else:
            blocked.append(t)
    if blocked:
        try:
            append_msg("system", "（已跳过 %s：该智能体已关闭，需在面板一键启用）" % "、".join(blocked), kind="system")
        except Exception:
            pass
    return keep


def flush_batch(preset_batch=None):
    global _pending_timer
    if preset_batch is not None:
        batch = list(preset_batch)
    else:
        with _pending_lock:
            batch = list(_pending)
            _pending.clear()
            _pending_timer = None
    if not batch:
        return
    labels = {"human": "炫炫", **AGENT_LABELS,
              **{p["id"]: p["display_name"] for p in _routing.load_providers()}}
    whole = " ".join(t for _, t, _ in batch).lower()
    from_human = any(s0 == "human" for s0, _, _ in batch)
    # Recover immutable recipients from persisted messages; unknown IDs fail closed.
    stored = {m.get("id"): m for m in read_msgs(0)}
    batch = [(s, t, mid) for s, t, mid in batch if mid in _to_map or mid in stored]
    broadcast_batch = [item for item in batch if stored.get(item[2], {}).get("broadcast")]
    if broadcast_batch and len(broadcast_batch) != len(batch):
        flush_batch([item for item in batch if not stored.get(item[2], {}).get("broadcast")])
        flush_batch(broadcast_batch)
        return
    generation = None
    if broadcast_batch:
        batch = [item for item in batch if broadcast_active(stored[item[2]].get("broadcast_generation"))]
        if batch:
            generation = stored[batch[0][2]]["broadcast_generation"]
    if not batch:
        return
    for _, _, mid in batch:
        if stored.get(mid, {}).get("broadcast"):
            _broadcast_message_generations[mid] = generation
            _to_map[mid] = [target for target in dispatch_targets() if target != stored[mid]["sender"]]
        elif mid not in _to_map:
            _to_map[mid] = list(stored[mid].get("to") or [])
    explicit = set()
    try:
        with open(os.path.join(DATA_DIR, "dispatch-debug.jsonl"), "a", encoding="utf-8") as _df:
            _df.write(json.dumps({"ts": time.time(), "flush": len(batch), "from_human": from_human,
                                  "mids": [b[2] for b in batch][:5]}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    for _s2, _t2, _mid2 in batch:
        for _r in _to_map.get(_mid2, []):
            explicit.add(_r)
    try:
        _blocked_targets(sorted(explicit))
    except Exception:
        pass

    # 严格按显式指向（explicit）路由：
    if explicit:
        want_gpt = "codex" in explicit or "gpt" in explicit
        want_gem = "ag" in explicit or "gemini" in explicit
        want_ds = "dsh" in explicit or "deepseek" in explicit
        want_all = bool(broadcast_batch)
        # 如果指向仅包含 human（例如向炫炫单线汇报），其他智能体绝对不唤醒、不打扰！
        if not explicit.intersection(dispatch_targets()):
            return
    else:
        # 炫炫最新要求：
        # “一般情况下我都会带有指向,那么我这边如果不选择指向那就是全员分发的...全员指向只有一种情况就是我找他们唠嗑”
        if from_human:
            # 炫炫没写指向时，默认只给我（dsh）；不再全员唤醒，避免别人收到不属于他的消息
            want_all = False
            want_gpt = False
            want_gem = False
            want_ds = True
            # 圆桌自动互评已关闭（降本）：炫炫发起的全员只跑一轮，
            # 需要互相验收时由炫炫点名，不再自动拉第二轮。
            if ROUNDTABLE_ENABLED:
                start_roundtable(" ".join(t for _, t, _ in batch), batch)
        else:
            return

    full_batch = batch
    for target, fn in dispatch_targets().items():
        if generation is not None and not broadcast_active(generation):
            break
        # 显式点名时只派给被点名的人（炫炫 08:57 那次 to=["mimo"] 却全员被唤醒，就是这个原因）
        if explicit and target not in explicit:
            continue
        # Codex 额度昂贵：只在明确点名 GPT/Codex/ChatGPT 时唤醒。
        # 输出 [[silent]] 仍会消耗一次完整推理，不能用“全量接收后静默”省额度。
        if target == "codex":
            if not want_gpt:
                continue
            if not codex_available() and not ("codex" in explicit or "gpt" in explicit):
                continue
        _fe_kw = ("前端", "页面", "样式", "布局", "颜色", "配色", "动画", "交互", "ui", "css", "按钮", "图")
        _light_kw = ("文档", "整理", "梳理", "总结", "脚本", "批量", "小活", "写个", "生成一份")
        _want_frontend = any(k in whole for k in _fe_kw)
        _want_light = any(k in whole for k in _light_kw)
        if target not in ("codex", "ag", "dsh") and not (
                target in explicit or any(target in (_to_map.get(mid) or []) for _, _, mid in full_batch)):
            continue
        if target == "ag" and not want_gem:
            continue
        # 全来源唤醒（炫炫 10:02）：human / agent 之间都唤醒 dsh，但走虚拟账号，不落炫炫 QQ
        if target == "dsh":
            _src = "human" if from_human else "agent"
            try:
                with open(os.path.join(DATA_DIR, "dispatch-debug.jsonl"), "a", encoding="utf-8") as _df:
                    _df.write(json.dumps({"ts": time.time(), "dsh_branch": True, "src": _src,
                                          "explicit": list(explicit)}, ensure_ascii=False) + "\n")
            except Exception:
                pass
            fn = make_auto_dispatcher("dsh", lambda prompt, _s=_src: inject_qq(prompt, source=_s))
        # 逐条按收件人过滤，指向别人的私信绝不能被带过去
        messages = [(s, t, mid) for s, t, mid in full_batch
                    if s != target
                    and (not _to_map.get(mid) or target in _to_map.get(mid))]
        _seen_txt, _uniq = set(), []
        for _s3, _t3, _m3 in messages:
            _k3 = _m3
            if _k3 in _seen_txt:
                continue
            _seen_txt.add(_k3)
            _uniq.append((_s3, _t3, _m3))
        messages = [item for item in _uniq if item[0] in ('human', 'system') or not is_no_reply_needed(item[1])]
        if not messages:
            continue
        # 若该批次内全部是同伴发来的『无需回复』或纯『收到』，不唤醒模型白跑一轮推理
        has_actionable = bool(broadcast_batch) or any(s in ("human", "system") or not is_no_reply_needed(t) for s, t, _ in messages)
        if not has_actionable:
            continue

        immediate = generation is None and all((_to_map.get(mid) or []) == [target] for _, _, mid in messages)
        _broadcast_policy.submit(target, messages, generation, immediate=immediate)


def _deliver_to_target(target, messages, generation=None, reason='direct'):
    if not messages or not agent_is_enabled(target):
        return
    if generation is not None and not broadcast_active(generation):
        return
    fn = dispatch_targets().get(target)
    if fn is None:
        return
    from_human = any(sender == 'human' for sender, _, _ in messages)
    explicit = {target}
    try:
        with open(os.path.join(DATA_DIR, 'dispatch-debug.jsonl'), 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'ts':time.time(), 'event':'recipient_delivery', 'target':target,
                                     'count':len(messages), 'mids':[m[2] for m in messages],
                                     'reason':reason, 'generation':generation}, ensure_ascii=False) + '\n')
    except OSError:
        pass
    joined = "\n\n".join(bootstrap.format_message(sender, body)
                             for sender, body, _ in messages)
    if target == "dsh":
        raw_senders = [s for s, _, _ in messages if s != "dsh"]
        valid_targets = _routing.agent_ids()
        reply_to = []
        for s in raw_senders:
            if s == "narrator":
                if "human" not in reply_to:
                    reply_to.append("human")
            elif s in valid_targets and s not in reply_to:
                reply_to.append(s)
        if not reply_to:
            reply_to = ["human"]
        _src2 = "human" if from_human else "agent"
        fn = make_auto_dispatcher(
            "dsh", lambda prompt, recipients=reply_to, _s=_src2:
            inject_qq(prompt, reply_to=recipients, source=_s))
    # 集思广益：同一批次含多个 Q 时逐题拆轮，每轮只绑一个 Q；单 Q 与普通消息保持原样
    _q_mids = []
    try:
        _bdata = _brainstorms.load()
        _batch_mids = {mid for _, _, mid in messages}
        for _q in _bdata["questions"]:
            if _q.get("message_id") in _batch_mids and target in _q["answers"]:
                _q_mids.append(_q["message_id"])
    except Exception:
        _q_mids = []
    if len(_q_mids) > 1 and all(sender == "human" for sender, _, _ in messages):
        for _mid in _q_mids:
            _one = [(s, t, m) for s, t, m in messages if m == _mid]
            if not _one:
                continue
            _text = "\n\n".join(bootstrap.format_message(sender, body)
                                   for sender, body, _ in _one)
            is_human_one = any(s == "human" for s, _, _ in _one)
            if not dispatch_once(target, _text, is_human=is_human_one):
                continue
            push_expecting_target(target, ["human"])
            launch_agent(target, _brainstorm_dispatch(fn, target, [_mid]), _text,
                         broadcast_generation=generation)
        return
    is_human_batch = any(s in ("human", "system") for s, _, _ in messages)
    if generation is not None or reason in ('peer_full', 'peer_timeout') or is_human_batch or dispatch_once(target, joined, is_human=is_human_batch):
        reply_senders = list(dict.fromkeys(s for s, _, _ in messages if s != target))
        push_expecting_target(target, reply_senders or ["human"])
        if target == "dsh":
            # dsh 是注入通道（非模型调用），直接同步执行，记录当前任务
            primary_s = messages[0][0] if messages else "human"
            raw_msg_txt = "\n\n".join(t for _, t, _ in messages)
            _agent_current_tasks["dsh"] = {
                "sender": primary_s,
                "sender_label": AGENT_LABELS.get(primary_s, "炫炫" if primary_s == "human" else primary_s),
                "text": raw_msg_txt,
                "ts": time.time()
            }
            try:
                _brainstorm_dispatch(fn, target, [mid for _, _, mid in messages])(joined)
            except Exception as _e:
                try:
                    with open(os.path.join(DATA_DIR, "dispatch-debug.jsonl"), "a", encoding="utf-8") as _df:
                        _df.write(json.dumps({"ts": time.time(), "dsh_error": str(_e)[:120]}, ensure_ascii=False) + "\n")
                except Exception:
                    pass
        else:
            primary_s = messages[0][0] if messages else "human"
            raw_msg_txt = "\n\n".join(t for _, t, _ in messages)
            launch_agent(target, _brainstorm_dispatch(fn, target, [mid for _, _, mid in messages]), joined,
                         sender=primary_s, raw_text=raw_msg_txt, broadcast_generation=generation)
        # 派发留痕（arch 不进 token-proxy，曾经因此无法追查它是否被派）
        try:
            with open(os.path.join(DATA_DIR, "dispatch-debug.jsonl"), "a", encoding="utf-8") as _df:
                _df.write(json.dumps({"ts": time.time(), "target": target,
                                      "mids": [m for _, _, m in messages],
                                      "chars": len(joined)}, ensure_ascii=False) + "\n")
        except Exception:
            pass


_forward_ts = {}
_retry = []
_retry_lock = threading.Lock()


def queue_retry(text, msg_id=""):
    with _retry_lock:
        for it in _retry:
            if it[0] == text:
                return
        _retry.append((text, msg_id, 1))


def retry_loop():
    time.sleep(60)
    while True:
        time.sleep(90)
        with _retry_lock:
            items = list(_retry)
        # Codex 超时不再重投：单次上下文极大，重投会把额度烧穿
        with _retry_lock:
            _retry.clear()
        items = []
        for text, mid, n in items:
            # 先出队再尝试，避免失败项每 90 秒无限重投、持续消耗 Codex 额度。
            with _retry_lock:
                try:
                    _retry.remove((text, mid, n))
                except ValueError:
                    continue
            try:
                import codex_queue
                q = codex_queue.ask_via_queue(text, timeout=18000)
                if q:
                    append_msg("codex", q)
            except Exception:
                pass


def import_new_images(since_ts):
    """把这一轮新生成的图片搬进 data/img 并在群里贴出来。"""
    import glob as _g
    import shutil as _sh
    base = os.path.join(os.environ["USERPROFILE"], ".codex", "generated_images")
    files = _g.glob(os.path.join(base, "**", "*.png"), recursive=True)
    files += _g.glob(os.path.join(base, "**", "*.jpg"), recursive=True)
    new = [f for f in files if os.path.getmtime(f) >= since_ts]
    for f in new[-3:]:
        name = "gpt-" + os.path.basename(f)
        dst = os.path.join(DATA_DIR, "img", name)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _sh.copy2(f, dst)
            append_msg("codex", "/img/" + name, "image")
        except Exception:
            pass


def ask_codex(text):
    """优先常驻 app-server（秒级）；不行再退回 codex exec。"""
    started = time.time()
    mark("codex", True)
    before = file_snapshot()
    activity("codex", "running", text, [], phase="executing_no_telemetry")
    try:
        _ask_codex_inner(text)
    except Exception as exc:
        agent_failure("codex", exc)
        try:
            queue_retry(text)
        except Exception:
            pass
    finally:
        _elapsed = time.time() - started
        try:
            if track_verdict_now("codex") not in ("timeout", "timeout_5m", "loop", "error"):
                activity("codex", "idle", "", phase=("failed" if _elapsed > 400 else "done"))
        except Exception:
            pass
        try:
            with open(os.path.join(DATA_DIR, "hub-timing.jsonl"), "a", encoding="utf-8") as tf:
                tf.write(json.dumps({"agent": "codex", "ms": int(_elapsed * 1000), "ts": time.time()}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if _elapsed > 90:
            try:
                append_msg("codex", "（这一轮耗时 %.0f 秒，会话历史可能太长，建议炫炫发 /new 换新会话）" % _elapsed, "system")
            except Exception:
                pass
        try:
            import_new_images(started)
        except Exception:
            pass
        if track_verdict_now("codex") not in ("timeout", "timeout_5m", "loop", "error"):
            activity("codex", "idle", text, changed_files(before))
        mark("codex", False)


_codex_down_until = 0.0






# ================= 移动端快照推送 =================
ECS_HOST = CONFLUX_RELAY_HOST
ECS_KEY = cfg.expand('${CONFLUX_WORKSPACE}/_alicloud.pem')
_SNAP_LAST = {"ts": 0}


def push_snapshot():
    """把 quotas/agents/tickets 打成 JSON 推到 ECS /var/www/hub/status.json。"""
    if not ECS_HOST:
        return False
    sync_all_agents_activity()
    try:
        import paramiko
    except Exception:
        return False
    try:
        st = {
            "ts": time.time(),
            "quotas": quotas(),
            "agents": {},
            "tickets": ticket_list(30),
        }
        try:
            health = health_snapshot()
            provider_names = {p["id"]: p["display_name"] for p in _routing.load_providers()}
            for name in display_agents():
                tr = track_state(name)
                act = _activity.get(name) or {}
                st["agents"][name] = {"name": provider_names.get(name, name),
                                      "runtime_status": health.get(name, {}).get("runtime_status", "offline"),
                                      "detail": health.get(name, {}).get("detail", ""),
                                      "state": tr.get("state", "idle"),
                                      "color": next((p.get("color", "") for p in _routing.load_providers() if p["id"] == name), ""),
                                      "avatar": next((p.get("avatar", "") for p in _routing.load_providers() if p["id"] == name), ""),
                                      "task": (act.get("task") or "")[:120],
                                      "since": act.get("started") or 0,
                                      "updated": act.get("updated") or 0}
        except Exception:
            pass
        payload = json.dumps(st, ensure_ascii=False)
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(ECS_HOST, username="root", pkey=paramiko.RSAKey.from_private_key_file(ECS_KEY),
                  timeout=20, allow_agent=False, look_for_keys=False, banner_timeout=20)
        sftp = c.open_sftp()
        with sftp.open("/var/www/hub/status.json", "w") as f:
            f.write(payload)
        sftp.close()
        c.close()
        return True
    except Exception:
        return False




# ================= dsh 待处理队列（不经过 QQ） =================
DSH_INBOX = os.path.join(DATA_DIR, "dsh-inbox.jsonl")


def inbox_push(sender, text):
    try:
        with open(DSH_INBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "id": uuid.uuid4().hex[:10],
                                "from": sender, "text": text, "done": False},
                               ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def inbox_list(limit=50):
    out = []
    try:
        for line in open(DSH_INBOX, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            if not m.get("done"):
                out.append(m)
    except Exception:
        pass
    return out[-limit:]


def inbox_ack(n=1000, ids=None):
    """把前 n 条（或指定 id）标记为已读。"""
    rows, changed = [], 0
    try:
        for line in open(DSH_INBOX, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            _hit = (ids is None and changed < n) or (ids is not None and m.get("id") in ids)
            if not m.get("done") and _hit:
                m["done"] = True
                changed += 1
            rows.append(m)
        with open(DSH_INBOX, "w", encoding="utf-8") as f:
            for m in rows:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return changed


def qq_private(text, user_id=CONFLUX_OWNER_QQ):
    """往炫炫 QQ 私聊发一句（NapCat 本地 HTTP）。失败静默。"""
    try:
        import urllib.request as _u
        body = {"user_id": int(user_id), "message": text}
        req = _u.Request("http://127.0.0.1:3000/send_private_msg",
                         data=json.dumps(body).encode("utf-8"),
                         headers={"Content-Type": "application/json",
                                  "Authorization": "Bearer " + NAPCAT_TOKEN})
        _u.urlopen(req, timeout=12).read()
        return True
    except Exception:
        return False


def send_qq_group(group_id, text, sender="ag"):
    """往 QQ 群发一条，带身份前缀，受冷却限制。"""
    import urllib.request as _u
    label = {"ag": "Gemini", "codex": "GPT", "dsh": "飞鱼"}.get(sender, sender)
    body = {"group_id": int(group_id), "message": "【%s】%s" % (label, text)}
    req = _u.Request("http://127.0.0.1:3000/send_group_msg",
                     data=json.dumps(body).encode("utf-8"),
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer " + NAPCAT_TOKEN})
    try:
        with _u.urlopen(req, timeout=15) as r:
            ok = r.status == 200
    except Exception as e:
        return False, str(e)[:80]
    try:
        sp = os.path.join(DATA_DIR, "group-speak.json")
        data = {}
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        data[str(group_id)] = time.time()
        data["last_sender"] = sender
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    return ok, ""



LOCAL_LLM = "http://127.0.0.1:11434/v1/chat/completions"
CLASSIFY_FILE = os.path.join(DATA_DIR, "classify.jsonl")


def classify_local(text, timeout=40):
    """影子分类：改用 MiMo（便宜、比本地 7B 准）。"""
    t0 = time.time()
    prompt = ("把下面这句话分类为 [任务/提问/闲聊/告警] 之一，只输出 JSON："
              "{\"label\":\"类别\",\"conf\":0到1的小数}，不要解释：\n" + (text or "")[:500])
    out = _mimo_json(prompt, max_tokens=40, timeout=timeout)
    label = str(out.get("label") or "")[:8]
    conf = out.get("conf")
    return label, conf, round(time.time() - t0, 2)


def classify_shadow(text):
    try:
        label, conf, dt = classify_local(text)
        with open(CLASSIFY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "label": label, "conf": conf, "ms": int(dt * 1000),
                                "chars": len(text or ""), "text": (text or "")[:80]}, ensure_ascii=False) + "\n")
    except Exception as e:
        try:
            with open(CLASSIFY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "error": str(e)[:80]}, ensure_ascii=False) + "\n")
        except Exception:
            pass





MIMO_MODEL_FAST = "mimo-v2.6-flash"


def _mimo_json(prompt, max_tokens=800, timeout=18000):
    """用 MiMo（flash）代替本地 7B 做结构化抽取。"""
    import urllib.request as _u
    try:
        with open(MIMO_KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
        body = json.dumps({"model": MIMO_MODEL_FAST,
                           "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": max_tokens}).encode("utf-8")
        req = _u.Request(MIMO_URL, data=body, method="POST",
                         headers={"Content-Type": "application/json",
                                  "Authorization": "Bearer " + key})
        r = json.loads(_u.urlopen(req, timeout=timeout).read())
        raw = (r["choices"][0]["message"].get("content") or "").strip()
        # MiMo 常把 JSON 包在 ```json 里；剥掉围栏再取花括号块
        raw = re.sub(r"^```[a-zA-Z]*\s*|```$", "", raw).strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {}
        return json.loads(m.group(0))
    except Exception:
        return {}




def extract_goal(text):
    """从炫炫的话里提取任务目标与最终效果，写进任务板。"""
    try:
        out = _mimo_json(
            "从下面这句话里提取任务。只输出 JSON：{\"goal\":\"不超过20字的任务名\","
            "\"effect\":\"不超过30字的最终效果\",\"accept\":\"不超过30字的验收标准\"}，不要其他内容：\n"
            + (text or "")[:800], max_tokens=160)
        goal = (out.get("goal") or "").strip()
        effect = (out.get("effect") or "").strip()
        accept = (out.get("accept") or "").strip()
        if goal in ("无", "无明确任务", "无任务", "暂无任务", "闲聊", "问候"):
            goal = ""
        if not goal:
            # 模型没抽出目标时，用原话前 20 字兜底建单，保证任务板和架构师流程不中断
            goal = (text or "").strip().replace("\n", " ")[:20]
            effect = effect or (text or "")[:40]
            with open(os.path.join(DATA_DIR, "extract-fail.jsonl"), "a", encoding="utf-8") as ff:
                ff.write(json.dumps({"ts": time.time(), "why": "fallback_goal", "goal": goal,
                                     "text": (text or "")[:120]}, ensure_ascii=False) + "\n")
            if not goal:
                return
        with _tasks_lock:
            tasks = read_tasks()
            for t in tasks:
                if t.get("status") == "running" and t.get("owner") == "human":
                    t["status"] = "done"
                    t["end"] = time.time()
            for t in reversed(tasks):
                if t.get("goal") == goal[:60] and time.time() - (t.get("start") or 0) < 600:
                    t["status"] = "running"
                    t["effect"] = effect[:60] or t.get("effect")
                    write_tasks(tasks)
                    return
            tasks.append({"id": uuid.uuid4().hex[:8], "name": goal[:60], "goal": goal[:60],
                          "effect": effect[:60], "status": "running",
                          "start": time.time(), "end": None, "owner": "human",
                          "roles": [],
                          "evidence": (("效果:" + effect + " ") if effect else "") +
                                      (("验收:" + accept) if accept else "") + " | 原话:" + (text or "")[:80]})
            write_tasks(tasks)
            # 2026-11-02 炫炫要求：不再自动生成决策单，Argon 只在被点名时参与技术讨论
    except Exception:
        pass




def record_role_reply(sender, text):
    """Compatibility: conversation text is never an execution/approval event."""
    return None


def extract_roles():
    """从三个人最近的回复里提取最终分工，回填到最新的 running 任务。"""
    try:
        with _tasks_lock:
            tasks = read_tasks()
        target = None
        for t in reversed(tasks):
            if t.get("status") in ("architecting", "executing", "awaiting_gpt_review", "rework") and not t.get("roles"):
                target = t
                break
        if target is None:
            return
        msgs = [m for m in read_msgs(time.time() - 900) if m.get("kind") == "text"
                and m.get("sender") in ("codex", "ag", "dsh")]
        if not msgs:
            return
        joined = "\n".join("[%s] %s" % (m.get("sender"), (m.get("text") or "")[:300]) for m in msgs[-6:])
        out = _mimo_json(
            "下面是三个人对同一任务的发言。用不超过12个字概括每个人最终负责什么，只输出 JSON："
            "{\"gpt\":\"短语\",\"gemini\":\"短语\",\"deepseek\":\"短语\"}，没有明确分工就留空字符串：\n" + joined,
            max_tokens=120)
        roles = []
        for k, label in (("gpt", "GPT"), ("gemini", "Gemini"), ("deepseek", "DeepSeek")):
            v = (out.get(k) or "").strip()
            # 只接受「短分工短语」：过长或含链接/代码标记的视为没提取到
            if v and len(v) <= 24 and "http" not in v and "`" not in v and "【" not in v:
                roles.append({"agent": ("codex" if k == "gpt" else "ag" if k == "gemini" else "dsh"),
                              "name": label, "work": v[:24], "status": "running"})
        if not roles:
            # 模型没抽出分工时，不拿聊天拼接充数：明确标「待分工」
            for agent, label in (("codex", "GPT"), ("ag", "Gemini"), ("dsh", "DeepSeek")):
                roles.append({"agent": agent, "name": label, "work": "待分工", "status": "pending"})
        with _tasks_lock:
            tasks = read_tasks()
            for t in tasks:
                if t.get("id") == target.get("id"):
                    t["roles"] = roles
                    write_tasks(tasks)
                    break
    except Exception:
        pass



MIMO_KEY_FILE = cfg.root_path('data/secrets/mimo.key')
MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL = "mimo-v2.6-flash"
MIMO_SYSTEM_PROMPT = """你是 Conflux 协作台中的 MiMo。
职责：负责目标与信息提炼、轻量任务、草稿、整理和辅助检查；重大决策、代码验收与最终结论交由相应负责人。
纪律：只根据已给出的上下文回答；没有证据就说明需要什么，不编造执行结果、文件改动、联网检索、身份或能力。分工与协作严格按成员名字/代号指派，严禁按底层模型来源猜测与关联。不泄露密钥、内部路径或未验证信息。回复简短、直接，遵守用户指定的格式和收件人标记。"""


def _mimo_identity_reply(text):
    """身份型号由程序按实际配置回答，避免模型自报幻觉。"""
    q = (text or "").strip().lower()
    identity_phrases = ("你是谁", "你叫什么", "什么模型", "哪个模型", "你的模型", "型号")
    if len(q) <= 80 and any(p in q for p in identity_phrases):
        return "我是 MiMo，Conflux 的代码与文档协作成员。当前实际调用模型是 mimo-v2.6-flash（小米 MiMo API），不是 DeepSeek、豆包或 Gemini。"
    return ""


MIMO_ENABLED = True    # 2026-11-04 10:18 恢复





MIMO_AGENT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mimo_agent.py")
MIMO_AGENT_PY = cfg.python_exe()




_mimo_lock = threading.Lock()
MIMO_LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_loop.py")


def _run_mimo_agent(task, cwd=cfg.root_path(''), timeout=None):   # 炫炫 20:47：MiMo 不设墙钟上限，跑完为止超时；放宽到 1200s
    """用轻量 agent_loop.py 跑 MiMo：6 个精简工具、单进程、无后台驻留 server、全程互斥。

    旧实现走 mimo.exe CLI：每次启动都注入 45 个 skills + memory 协议 + 20 多个工具 Schema，
    单轮几万 input token，多轮任务直接烧掉上百万，且超时后 server-proxy 子进程会变孤儿。
    """
    if not _mimo_lock.acquire(blocking=False):
        return "（MiMo 已有任务在跑，本次不并发；等它结束再派）"
    try:
        # 只拦「以危险命令开头的行」。原来是对整段任务做子串匹配，
        # 历史里只要有人讨论过 del /f 这类词，任何普通任务都会被误杀
        # （2026-11-06 22:50 广播自检被拒就是这个原因）。工具层还有 DENY_CMD 兜底。
        _danger = ("remove-item -recurse", "rm -rf", "del /f", "del /s", "rd /s", "rmdir /s",
                   "format ", "shutdown", "删除目录", "删掉整个", "清空 d 盘")
        _hit = False
        for _line in (task or "").splitlines():
            _l = _line.strip().lower()
            if _l and any(_l.startswith(k) for k in _danger):
                _hit = True
                break
        if _hit:
            return "（已拒绝：任务包含删除/格式化类危险动作，请拆成具体的文件修改）"
        proc = subprocess.Popen([MIMO_AGENT_PY, MIMO_LOOP, "--provider", "mimo", "--rounds", "0",
                                 "--cwd", cwd, "--task", task],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=os.path.dirname(MIMO_LOOP),
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                startupinfo=_hidden_startup(),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            _out, _err = proc.communicate(timeout=timeout) if timeout else proc.communicate()
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return "（mimo 超时 %d 秒，已按进程树清理）" % int(timeout)
        out = (_out or b"").decode("utf-8", "ignore") if isinstance(_out, bytes) else str(_out or "")
        err = (_err or b"").decode("utf-8", "ignore") if isinstance(_err, bytes) else str(_err or "")
        text = (out.strip() or err.strip()).strip()
        return text[:3000] or "（mimo 没有输出结论）"
    except Exception as e:
        return "（mimo 异常：%s）" % str(e)[:100]
    finally:
        try:
            _mimo_lock.release()
        except Exception:
            pass






_agent_memory_lock = threading.RLock()
try:
    from agent_memory import MemoryBank
    _MEM_BANK = MemoryBank(os.path.join(DATA_DIR, "agent-memory"))   # 统一记忆总线：隔离路由 + 去重写入 + 预算选择
except Exception:
    _MEM_BANK = None


def _compressed_store():
    from agent_memory import CompressedMemory
    return CompressedMemory(os.path.join(DATA_DIR, "agent-memory", "compressed.sqlite3"))


def _memory_failure(operation, error):
    try:
        with open(os.path.join(DATA_DIR, "memory-errors.jsonl"), "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"ts": time.time(), "operation": operation,
                                     "error": type(error).__name__}) + "\n")
    except OSError:
        pass


def _remember_routed_message(message):
    try:
        _compressed_store().remember_messages([message], MSG_FILE)
    except Exception as error:
        _memory_failure("message", error)


def _warm_agent_memory():
    try:
        memory = _compressed_store()
        memory.sync_messages(MSG_FILE)
    except Exception as error:
        _memory_failure("backfill", error)


def _compressed_agent_context(agent, query):
    try:
        memory = _compressed_store()
        notes = memory.context(agent, query, budget=2400)
        return ("【你的压缩记忆，仅作历史线索，执行与验收需回查来源】\n" + notes) if notes else ""
    except Exception as error:
        _memory_failure("retrieve", error)
        return ""




def _shared_agent_context(agent_name, msg_limit=6, query=""):
    return _compressed_agent_context(agent_name, query)




def _save_agent_memory(agent_name, task_text, out_text):
    try:
        mf = os.path.join(DATA_DIR, "memory-%s.jsonl" % agent_name)
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "task": (task_text or "").replace("\n", " ")[:240],
            "out": (out_text or "").replace("\n", " ")[:360],
        }
        with open(mf, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # 统一记忆通道：同一结果同步写入 MemoryBank（去重 + 来源引用），检索与预算策略统一走 agent_memory。
    try:
        if _MEM_BANK is not None:
            entry = "任务:%s => 结论:%s" % ((task_text or "").replace("\n", " ")[:240],
                                           (out_text or "").replace("\n", " ")[:360])
            with _agent_memory_lock:
                _MEM_BANK.get(agent_name).add(entry, source="hub#task", kind="task")
    except Exception:
        pass


def ask_mimo(text):
    """小米 MiMo（OpenAI 兼容），作为协作台第四个声音。"""
    try:
        _out = _run_mimo_agent(text)
        if _out:
            append_msg("mimo", _out)
            return
    except Exception:
        pass
    if not MIMO_ENABLED:
        try:
            append_msg("mimo", "（MiMo 已暂停，未回复）")
        except Exception:
            pass
        return
    identity_reply = _mimo_identity_reply(text)
    if identity_reply:
        append_msg("mimo", identity_reply)
        return
    import urllib.request as _ur
    mark("mimo", True)
    try:
        activity("mimo", "running", (text or "")[:100], phase="running")
    except Exception:
        pass
    try:
        with open(MIMO_KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
        _mctx = _shared_agent_context("mimo", 6, query=text)
        body = json.dumps({"model": MIMO_MODEL,
                           "messages": [{"role": "system", "content": bootstrap.snapshot("mimo")["prompt"] + ("\n\n" + _mctx if _mctx else "")},
                                        {"role": "user", "content": text}],
                           "max_tokens": 1200}).encode("utf-8")
        timing = {"sent_at": time.time()}
        req = _ur.Request(MIMO_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + key})
        try:
            resp = _ur.urlopen(req, timeout=1800)
            timing["first_byte_at"] = time.time()
            activity("mimo", "running", (text or "")[:100], phase="connected_waiting_first_byte", timing=timing)
            raw_body = resp.read()
            timing["last_byte_at"] = time.time()
            activity("mimo", "running", (text or "")[:100], phase="streaming", timing=timing)
        except Exception as e:
            timing["error_at"] = time.time()
            timing["error"] = str(e)[:80]
            phase = "timeout" if "timed out" in str(e).lower() else "connection_error"
            activity("mimo", "idle", "", phase=phase, timing=timing)
            append_msg("mimo", "（MiMo %s）" % phase, "system")
            return
        r = json.loads(raw_body)
        msg = r["choices"][0]["message"]
        reply = (msg.get("content") or "").strip()
        if reply:
            append_msg("mimo", reply)
            timing["completed_at"] = time.time()
            activity("mimo", "idle", "", phase="completed", timing=timing)
    except Exception as e:
        agent_failure("mimo", e)
        append_msg("mimo", "（MiMo 调用失败：%s）" % str(e)[:80], "system")
    finally:
        mark("mimo", False)
        try:
            cur = (_activity.get("mimo") or {}).get("timing") or {}
            cur["completed_at"] = time.time()
            activity("mimo", "idle", "", phase="completed", timing=cur)
        except Exception:
            pass



VERTEX_CLIENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertex_client.py")




_arch_lock = threading.Lock()
_arch_next_ok = 0.0
ARCH_MIN_GAP = 20.0

def arch_plan(task_id, text):
    """架构师（Vertex gemini-4-argon，thinkingLevel=low）：只在任务开局出一次决策单。"""
    _card_out = None
    dispatch_roles = []
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            activity("arch", "running", "产出决策单", phase="running")
        except Exception:
            pass
        import vertex_client as vc
        prompt = (
            arch_system_prompt() +
            "\n\n下面是主人的任务，按出厂设定先读快照再给决策。只输出 JSON："
            "{\"goal\":\"一句话目标\",\"acceptance\":\"可验证的验收标准\","
            "\"roles\":{\"gpt\":\"分工\",\"gemini\":\"分工\",\"deepseek\":\"分工\",\"mimo\":\"分工\"},"
            "\"dependencies\":\"关键依赖或前置条件\",\"risks\":\"主要风险\"}，不要解释：\n"
            + (text or "")[:1500] + "\n\n" + arch_workspace_brief(max_files=25, max_bytes=2500))
        out = vc.chat(prompt, model="gemini-4-argon", max_tokens=900, thinking="low", timeout=18000)
        raw = out.get("text") or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return
        plan = json.loads(m.group(0))
        linked = next((t.get("ticket_id") for t in read_tasks() if t.get("id") == task_id), None)
        if linked:
            ticket_update(linked, "plan", json.dumps({k: v for k, v in plan.items() if k != "roles"}, ensure_ascii=False), "arch")
            ticket_update(linked, "log", json.dumps(plan.get("roles") or {}, ensure_ascii=False), "arch")
            ticket_update(linked, "status", "doing", "arch")
        roles = []
        for key, agent, label in (("gpt", "codex", "GPT"), ("gemini", "ag", "Gemini"),
                                  ("deepseek", "dsh", "DeepSeek"), ("mimo", "mimo", "MiMo")):
            work = str((plan.get("roles") or {}).get(key) or "").strip()
            if work:
                roles.append({"agent": agent, "name": label, "work": work[:200], "status": "running"})
        dispatch_roles = []
        with _tasks_lock:
            tasks = read_tasks()
            for t in tasks:
                if t.get("id") == task_id:
                    t["acceptance"] = str(plan.get("acceptance") or "")[:120]
                    t["dependencies"] = str(plan.get("dependencies") or "")[:120]
                    t["risks"] = str(plan.get("risks") or "")[:120]
                    t["architect"] = "gemini-4-argon"
                    first_dispatch = t.get("status") == "architecting" and not t.get("arch_dispatched")
                    t["status"] = "executing"
                    if roles:
                        t["roles"] = roles
                    if first_dispatch:
                        t["arch_dispatched"] = True
                        dispatch_roles = list(roles)
                    write_tasks(tasks)
                    _card = ("决策单（Argon）\n目标：%s\n验收：%s\n依赖：%s\n风险：%s\n分工：%s"
                             % (plan.get("goal", "")[:80], plan.get("acceptance", "")[:120],
                                plan.get("dependencies", "")[:80], plan.get("risks", "")[:80],
                                "；".join("%s→%s" % (r["name"], r["work"][:120]) for r in roles) or "待分工"))
                    _card_out = _card
                    break
        # 决策单先广播，再由首次 architecting→executing 触发一次真实派发。
        if _card_out:
            append_msg("arch", _card_out,
                       to=["human"] + [r["agent"] for r in roles], schedule=False)
        dispatchers = {"codex": ask_codex, "ag": ask_antigravity,
                       "dsh": inject_qq, "mimo": ask_mimo}
        for role in dispatch_roles:
            agent = role.get("agent")
            work = (role.get("work") or "").strip()
            fn = dispatchers.get(agent)
            if fn and work:
                dispatch_arch_role(agent, fn, work)
        try:
            activity("arch", "idle", "", phase="done")
        except Exception:
            pass
    except Exception:
        pass






ARCH_WORKDIR = cfg.WORKSPACE


def arch_workspace_brief(max_files=40, max_bytes=4000):
    """给 Argon 的只读工作区快照：文件树 + 关键文档前几千字。"""
    lines = []
    files = []
    try:
        for base, dirs, names in os.walk(ARCH_WORKDIR):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", "workbench")]
            for n in names:
                fp = os.path.join(base, n)
                try:
                    size = os.path.getsize(fp)
                except Exception:
                    continue
                files.append((os.path.relpath(fp, ARCH_WORKDIR), size))
                if len(files) >= 200:
                    break
            if len(files) >= 200:
                break
    except Exception:
        pass
    files.sort()
    lines.append("工作区文件（前 %d 个）：" % min(len(files), max_files))
    for rel, size in files[:max_files]:
        lines.append("- %s (%d B)" % (rel, size))
    # 关键文档
    for rel in ("CONTEXT.md", "AGENTS.md", "COLLABORATION_PROTOCOL.md", "SYSTEM_ROLES.md",
                "COLLAB.md", "HANDOFF-20261101.md", "ARCH_SYSTEM.md"):
        try:
            with open(os.path.join(ARCH_WORKDIR, rel), encoding="utf-8", errors="ignore") as f:
                lines.append("\n== %s ==\n%s" % (rel, f.read()[:max_bytes]))
        except Exception:
            pass
    # hub 接口与函数摘要（只给签名，不给实现）
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hub.py"),
                  encoding="utf-8", errors="ignore") as f:
            src = f.read()
        eps = sorted(set(re.findall(r'"/api/([a-z\-]+)"', src)))
        fns = sorted(set(re.findall(r"^def ([a-z_]{3,30})\(", src, re.M)))
        key_docs = []
        for fn in ("arch_plan", "ask_arch", "flush_batch", "record_role_reply", "launch_agent", "dispatch_once"):
            m2 = re.search(r"def %s\([^)]*\):\n\s+\"\"\"(.*?)\"\"\"" % fn, src, re.S)
            if m2:
                key_docs.append("%s: %s" % (fn, m2.group(1).strip().splitlines()[0][:80]))
        lines.append("\n== hub.py 接口 ==\n" + ", ".join("/api/" + e for e in eps[:25]))
        if key_docs:
            lines.append("== 关键函数（签名+用途）==\n" + "\n".join(key_docs))
        for fn in ("arch_plan", "ask_arch", "record_role_reply", "flush_batch", "append_msg"):
            m3 = re.search(r"(?ms)^def %s\(.*?(?=^def |\Z)" % fn, src)
            if m3:
                lines.append("\n== 源码：%s ==\n%s" % (fn, m3.group(0)[:3000]))
    except Exception:
        pass
    # 任务板摘要
    try:
        active, archived = split_tasks(read_tasks())
        lines.append("\n== 任务板（活跃 %d / 归档 %d）==" % (len(active), len(archived)))
        for t in active[:5]:
            lines.append("- [%s] %s" % (t.get("status"), (t.get("goal") or "")[:40]))
    except Exception:
        pass
    return "\n".join(lines)[:200000]




ARCH_SYSTEM_FILE = cfg.expand('${CONFLUX_WORKSPACE}/ARCH_SYSTEM.md')


def arch_system_prompt():
    slot = {}
    ensure_client_session("arch", "api-system", lambda prompt: slot.update(prompt=prompt) or True)
    return (slot.get("prompt") or bootstrap.snapshot("arch")["prompt"]) + "\n" + _shared_agent_context("arch")




ARCH_ROOTS = ("D:\\",)

ARCH_TOOLS = [
    {"name": "list_dir", "description": "列出目录下的文件与子目录",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "read_file", "description": "读取文本文件（自动截断前 8000 字符）",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "grep", "description": "在某个目录或文件里搜索关键词，返回命中行",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}},
                    "required": ["path", "pattern"]}},
    {"name": "write_file", "description": "写入文本文件（全 D:\\ 盘可用）",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}},
    {"name": "patch_file", "description": "唯一匹配替换（全 D:\\ 盘可用）",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                     "new": {"type": "string"}},
                    "required": ["path", "old", "new"]}},
    {"name": "run_cmd", "description": "执行 PowerShell 命令（可跑测试/编译/查看结果），30 分钟内不打断",
     "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"]}},
    {"name": "find_file", "description": "按文件名片段查找文件路径",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
]


def _arch_safe(path):
    full = os.path.abspath(path)
    return any(full.lower().startswith(r.lower()) for r in ARCH_ROOTS)


def arch_tool_run(name, args):
    try:
        if name == "list_dir":
            p = args.get("path") or ARCH_ROOTS[0]
            if not _arch_safe(p):
                return cfg.expand('越界：只允许 ${CONFLUX_ROOT} 与 ${CONFLUX_WORKSPACE}')
            items = []
            for e in sorted(os.scandir(p), key=lambda x: x.name)[:120]:
                items.append(("[dir] " if e.is_dir() else "[file] ") + e.name)
            return "\n".join(items) or "(空)"
        if name == "read_file":
            p = args.get("path") or ""
            if not _arch_safe(p):
                return cfg.expand('越界：只允许 ${CONFLUX_ROOT} 与 ${CONFLUX_WORKSPACE}')
            with open(p, encoding="utf-8", errors="ignore") as f:
                return f.read()[:60000]
        if name == "grep":
            base = args.get("path") or ARCH_ROOTS[0]
            pat = args.get("pattern") or ""
            if not _arch_safe(base) or not pat:
                return "参数无效"
            hits = []
            files = [base] if os.path.isfile(base) else [
                os.path.join(dp, fn) for dp, _, fns in os.walk(base) for fn in fns
                if not any(x in dp for x in (".git", "node_modules", "__pycache__", "venv"))][:400]
            for fp in files:
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if pat in line:
                                hits.append("%s:%d: %s" % (fp, i, line.strip()[:120]))
                                if len(hits) >= 200:
                                    return "\n".join(hits)
                except Exception:
                    pass
            return "\n".join(hits) or "(无命中)"
        if name in ("write_file", "patch_file"):
            path = args.get("path") or ""
            if not _arch_safe(path):
                return cfg.expand('越界：只允许 ${CONFLUX_ROOT} 与 ${CONFLUX_WORKSPACE}')
            if name == "write_file":
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                open(path, "w", encoding="utf-8").write(args.get("content") or "")
                return "已写入 %s（%d 字符）" % (path, len(args.get("content") or ""))
            txt = open(path, encoding="utf-8", errors="ignore").read()
            old, new = args.get("old") or "", args.get("new") or ""
            if txt.count(old) != 1:
                return "匹配到 %d 处，需要唯一匹配" % txt.count(old)
            open(path, "w", encoding="utf-8").write(txt.replace(old, new, 1))
            return "已替换 1 处：%s" % path
        if name == "run_cmd":
            cmd = (args.get("cmd") or "").strip()
            if not cmd:
                return "命令为空"
            low = cmd.lower()
            for bad in ("format ", "diskpart", "shutdown", "bcdedit", "cipher /w"):
                if bad in low:
                    return "命令被拒绝（%s）" % bad
            pr = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                capture_output=True, timeout=1800, cwd=ARCH_ROOTS[0],
                                encoding="utf-8", errors="ignore",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = ((pr.stdout or "") + (pr.stderr or "")).strip()
            return out or "(无输出，退出码 %s)" % pr.returncode
        if name == "find_file":
            frag = (args.get("name") or "").lower()
            out = []
            for root in ARCH_ROOTS:
                for dp, dns, fns in os.walk(root):
                    dns[:] = [d for d in dns if d not in (".git", "node_modules", "__pycache__", "venv")]
                    for fn in fns:
                        if frag in fn.lower():
                            out.append(os.path.join(dp, fn))
                            if len(out) >= 40:
                                return "\n".join(out)
            return "\n".join(out) or "(没找到)"
        return "未知工具"
    except Exception as e:
        return "工具异常: %s" % str(e)[:120]


def ask_arch_tools(text, max_rounds=20):
    """Argon 带全盘工具与跨轮上下文记忆。"""
    import vertex_client as vc
    contents = [{"role": "user", "parts": [{"text": text}]}]
    _tool_count = 0
    _t_start = time.time()
    _tool_deadline = 1500
    _done_flag = {"v": False}

    track_beat("arch", (text or "")[:60])
    for _round in range(max_rounds):
        track_beat("arch", "调用模型第 %d 轮" % (_round + 1))
        if time.time() - _t_start > _tool_deadline:
            _done_flag["v"] = True
            # 预算到点不静默掐断：剥离 tools 再要一次结论，让模型基于已读上下文吐半成品
            contents.append({"role": "user", "parts": [{"text": "时间预算到了。基于已读到的内容，直接给结论，不要再调工具。"}]})
            try:
                cand = _vc_chat_retry(vc, contents, model="gemini-4-argon", max_tokens=8000,
                                        system=arch_system_prompt(), timeout=200)
                parts = (cand.get("content") or {}).get("parts") or []
                _ans = "".join(p.get("text", "") for p in parts).strip()
            except Exception:
                _ans = ""
            track_end("arch", "done")
            if _ans:
                return _ans
            return "（未完成：跑了 %d 分钟、%d 次工具调用；最后的结论请求也没返回。）" % (
                int((time.time() - _t_start) / 60), _tool_count)
        cand = _vc_chat_retry(vc, contents, tools=ARCH_TOOLS, model="gemini-4-argon",
                                max_tokens=8000, system=arch_system_prompt(), timeout=200)
        parts = (cand.get("content") or {}).get("parts") or []
        calls = [p for p in parts if p.get("functionCall")]
        if not calls:
            txt_output = "".join(p.get("text", "") for p in parts).strip()
            if _round == 0 and any(code_block in txt_output for code_block in ("```bash", "```python", "```sh")) and any(proc in txt_output for proc in ("我先看", "我先查", "马上盘点", "稍等", "我来看看", "先排查", "我来查")):
                contents.append({"role": "model", "parts": parts})
                contents.append({"role": "user", "parts": [{"text": "检测到你输出了计划或待执行的代码块但未实际调用工具。请直接通过 Function Calling 调用真实工具执行，执行完毕后再输出最终结论。"}]})
                continue
            _done_flag["v"] = True
            track_end("arch", "done")
            return txt_output
        contents.append({"role": "model", "parts": parts})
        resp = []
        for c in calls:
            fc = c["functionCall"]
            _tool_count += 1
            try:
                activity("arch", "running", "工具调用 %d 次：%s" % (_tool_count, fc.get("name")), phase="streaming")
            except Exception:
                pass
            track_beat("arch", "工具 %s" % fc.get("name"), tool=fc.get("name"), args=fc.get("args") or {})
            res = arch_tool_run(fc.get("name"), fc.get("args") or {})
            try:
                with open(os.path.join(DATA_DIR, "arch-tools.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": time.time(), "tool": fc.get("name"),
                                        "args": fc.get("args"), "out": res[:200]},
                                       ensure_ascii=False) + "\n")
            except Exception:
                pass
            resp.append({"functionResponse": {"name": fc.get("name"),
                                              "response": {"result": res[:40000]}}})
        contents.append({"role": "user", "parts": resp})
    # 轮次用完：强制要一个结论，而不是丢错误
    contents.append({"role": "user", "parts": [{"text": "工具轮次已用完。基于已读到的内容，直接给结论，不要再调工具。"}]})
    try:
        cand = _vc_chat_retry(vc, contents, model="gemini-4-argon", max_tokens=8000,
                                system=arch_system_prompt(), timeout=200)
        parts = (cand.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip() or "（没给出结论）"
    except Exception:
        return "（没给出结论）"




def _vc_chat_retry(vc, contents, **kw):
    """Vertex 偶发 SSL 握手失败或 429 限流，做退避重试。"""
    last = None
    for i in range(4):
        try:
            return vc.chat_contents(contents, **kw)
        except Exception as e:
            last = e
            err_str = str(e).lower()
            if any(k in err_str for k in ("ssl", "handshake", "timeout", "timed out", "429", "too many requests", "resource_exhausted")):
                time.sleep(2 * (i + 1))
                continue
            raise
    raise last


def ask_arch(text):
    """架构师（Vertex gemini-4-argon，thinking=low），可被 >>> Argon 显式点名。"""
    mark("arch", True)
    try:
        activity("arch", "running", (text or "")[:100], phase="running")
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import vertex_client as vc
        global _arch_next_ok
        with _arch_lock:
            wait = _arch_next_ok - time.time()
            if wait > 0:
                time.sleep(min(wait, 60))
            _arch_next_ok = time.time() + ARCH_MIN_GAP
        _t0 = time.time()
        _kw = ("今天", "现在", "最新", "新闻", "日期", "几号", "股价", "天气",
               "发布", "版本", "联网", "搜索", "查一下", "实时", "最近")
        _need_search = any(k in (text or "") for k in _kw)
        _brief = ""
        try:
            _tool_reply = ask_arch_tools((text or "").strip(), max_rounds=60)
            if _tool_reply:
                _save_agent_memory("arch", text, _tool_reply)
                activity("arch", "idle", "", phase="completed")
                append_msg("arch", _tool_reply)
                return
        except Exception:
            pass
        # Vertex 偶发读超时：先重试一次，不直接落成 error（炫炫 14:48 批准）
        try:
            out = vc.chat(text, system=arch_system_prompt(), model="gemini-4-argon", max_tokens=8000, thinking="low",
                          timeout=1800, search=_need_search)
        except Exception:
            try:
                track_beat("arch", "Vertex 超时重试中")
            except Exception:
                pass
            time.sleep(2)
            out = vc.chat(text, system=arch_system_prompt(), model="gemini-4-argon", max_tokens=8000, thinking="low",
                          timeout=1800, search=_need_search)
        _t1 = time.time()
        activity("arch", "running", (text or "")[:100], phase="streaming",
                 timing={"sent_at": _t0, "first_byte_at": _t1, "last_byte_at": _t1})
        reply = (out.get("text") or "").strip()
        if reply:
            append_msg("arch", reply)
        else:
            append_msg("arch", "（Argon 本轮没给出内容，可能是被截断或只调了工具）")
    except Exception as e:
        agent_failure("arch", e)
        _msg = "（Argon 调用失败，可以重试：%s）" % str(e)[:50]
        _dup = False
        try:
            for _m2 in read_msgs(time.time() - 300, limit=20):
                if _m2.get("sender") == "arch" and str(_m2.get("text") or "").startswith("（Argon 调用失败"):
                    _dup = True
                    break
        except Exception:
            pass
        if not _dup:
            append_msg("arch", _msg)
    finally:
        mark("arch", False)
        try:
            activity("arch", "idle", "", phase="completed",
                     timing={"completed_at": time.time(),
                             "sent_at": _t0 if "_t0" in dir() else None})
        except Exception:
            pass


def codex_available():
    return time.time() >= _codex_down_until


HANDOFF_LIMIT = 600


# ---------- 右抽屉：任务板 + 健康检查（纯程序，不调用模型） ----------

_tasks_lock = threading.Lock()




ACTIVE_STATES = ("queued", "architecting", "executing", "running", "awaiting_gpt_review", "awaiting_acceptance", "rework")


def split_tasks(tasks):
    active = [t for t in tasks if (t.get("status") or "") in ACTIVE_STATES]
    archived = [t for t in tasks if (t.get("status") or "") not in ACTIVE_STATES]
    return active, archived




HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.json")


def heartbeat_loop():
    """事实型探针：进程活着就写心跳；锁状态、线程数一并落盘。"""
    import threading as _th
    while True:
        try:
            data = {"pid": os.getpid(), "ts": time.time(),
                    "threads": _th.active_count(),
                    "msg_lock_free": _lock.acquire(blocking=False),
                    "task_lock_free": _tasks_lock.acquire(blocking=False),
                    "uptime": time.time() - _START_TS}
            if data["msg_lock_free"]:
                _lock.release()
            if data["task_lock_free"]:
                _tasks_lock.release()
            with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass
        time.sleep(5)


_START_TS = time.time()




# ---------- 额度查询（60 秒缓存） ----------
_QUOTA_CACHE = {"ts": 0, "data": {}}
QUOTA_TTL = 60
PAUSE_FILE = os.path.join(DATA_DIR, "PAUSED")


def is_paused():
    return os.path.exists(PAUSE_FILE)


def _quota_deepseek():
    import urllib.request
    key_file = os.path.join(os.path.dirname(DATA_DIR), "secrets", "deepseek.key")
    if not os.path.exists(key_file):
        return {"ok": False, "reason": "未配置 deepseek.key"}
    try:
        key = open(key_file, encoding="utf-8").read().strip()
        req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                     headers={"Authorization": "Bearer " + key})
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        bal = (r.get("balance_infos") or [{}])[0]
        return {"ok": True, "currency": bal.get("currency"),
                "total": bal.get("total_balance"),
                "granted": bal.get("granted_balance"),
                "topped": bal.get("topped_up_balance")}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:80]}


MIMO_BALANCE_FILE = os.path.join(DATA_DIR, "mimo-balance.json")




MIMO_COOKIE_FILE = os.path.join(os.path.dirname(DATA_DIR), "secrets", "mimo-cookie.txt")


def mimo_balance_refresh():
    """用控制台 cookie 抓一次余额，写进 mimo-balance.json。"""
    import urllib.request as _ur
    try:
        cookie = open(MIMO_COOKIE_FILE, encoding="utf-8").read().strip()
    except Exception:
        return False
    try:
        req = _ur.Request("https://platform.xiaomimimo.com/api/v1/balance",
                          headers={"accept": "*/*", "content-type": "application/json",
                                   "referer": "https://platform.xiaomimimo.com/console/balance",
                                   "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/156.0.0.0 Safari/537.36",
                                   "x-timezone": "UTC", "cookie": cookie})
        r = json.loads(_ur.urlopen(req, timeout=20).read())
        d = r.get("data") or {}
        if r.get("code") != 0 or "balance" not in d:
            return False
        snap = {"ts": time.time(), "balance": d.get("balance"), "currency": d.get("currency", "CNY"),
                "gift": d.get("giftBalance"), "cash": d.get("cashBalance"),
                "raw": "platform.xiaomimimo.com/api/v1/balance"}
        with open(MIMO_BALANCE_FILE, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def _quota_mimo():
    import urllib.request
    # 优先读外部写入的余额快照（网页抓取/手工填），再退回官方接口
    try:
        if not os.path.exists(MIMO_BALANCE_FILE):
            mimo_balance_refresh()
        if os.path.exists(MIMO_BALANCE_FILE):
            d = json.load(open(MIMO_BALANCE_FILE, encoding="utf-8"))
            age = time.time() - (d.get("ts") or 0)
            if age > 120:
                mimo_balance_refresh()
                try:
                    d = json.load(open(MIMO_BALANCE_FILE, encoding="utf-8"))
                    age = time.time() - (d.get("ts") or 0)
                except Exception:
                    pass
            return {"ok": True, "source": "snapshot", "age": round(age, 1),
                    "balance": d.get("balance"), "currency": d.get("currency", "CNY"),
                    "gift": d.get("gift"), "cash": d.get("cash"),
                    "raw": str(d.get("raw") or "")[:120]}
    except Exception:
        pass
    key_file = cfg.root_path('data/secrets/mimo.key')
    if not os.path.exists(key_file):
        return {"ok": False, "reason": "未配置 mimo.key"}
    try:
        key = open(key_file, encoding="utf-8").read().strip()
        for url in ("https://api.xiaomimimo.com/v1/user/balance",
                    "https://api.xiaomimimo.com/v1/dashboard/billing/subscription"):
            try:
                req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
                r = json.loads(urllib.request.urlopen(req, timeout=15).read())
                return {"ok": True, "raw": str(r)[:200]}
            except Exception:
                continue
        return {"ok": False, "reason": "服务端未提供余额接口(404)"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:80]}


def _quota_vertex():
    return {"ok": True, "mode": "api_key", "note": "按次计费，无余额概念"}


_LOCAL_QUOTA_FILES = {
    "codex": (os.path.join(DATA_DIR, "codex-usage.jsonl"),
              os.path.join(DATA_DIR, "usage.jsonl"),
              os.path.join(DATA_DIR, "token-proxy.jsonl")),
    "antigravity": (os.path.join(DATA_DIR, "antigravity-usage.jsonl"),
                    os.path.join(DATA_DIR, "ag-usage.jsonl"),
                    os.path.join(DATA_DIR, "token-proxy.jsonl")),
}


def _quota_fields(value, out):
    """从本机代理记录/响应头中提取窗口余额；绝不返回 cookie、token 或完整响应。"""
    if isinstance(value, dict):
        for key, item in value.items():
            low = str(key).lower().replace("_", "-")
            if any(x in low for x in ("authorization", "cookie", "token", "secret", "api-key")):
                continue
            if isinstance(item, (dict, list)):
                _quota_fields(item, out)
            elif any(x in low for x in ("remaining", "limit", "reset", "weekly", "week", "5h", "5-hour", "five-hour")):
                out[low] = item
    elif isinstance(value, list):
        for item in value[-8:]:
            _quota_fields(item, out)


def _quota_local(agent):
    """读取本机用量/代理 JSONL 的最近响应头快照，缺失时明确标为未知而非伪造额度。"""
    files = [p for p in _LOCAL_QUOTA_FILES.get(agent, ()) if os.path.exists(p)]
    # Codex rollout 偶尔会把 rate-limit 事件写入会话；只检查最近文件，避免扫整份历史。
    if agent == "codex":
        import glob
        files += sorted(glob.glob(os.path.join(CODEX_SESSIONS, "**", "rollout-*.jsonl"), recursive=True),
                        key=os.path.getmtime)[-2:]
    observed, source = {}, ""
    for path in files:
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(path) - 512 * 1024))
                chunk = fh.read().decode("utf-8", "ignore")
            for line in chunk.splitlines():
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                fields = {}
                _quota_fields(row, fields)
                if fields:
                    observed.update(fields)
                    source = os.path.basename(path)
        except OSError:
            continue
    windows = {k: v for k, v in observed.items()
               if any(x in k for x in ("remaining", "weekly", "week", "5h", "5-hour", "five-hour"))}
    return ({"ok": True, "source": source, "windows": windows,
             "resets": {k: v for k, v in observed.items() if "reset" in k}}
            if windows else {"ok": False, "reason": "本机无可解析订阅窗口快照"})




def _quota_local_probe():
    """Codex / Antigravity 本地快照（由 GPT 的 quota_probe.py 提供）。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import quota_probe as qp
        cx = qp.codex_snapshot()
        ag = qp.ag_snapshot()
        return {"codex": {"ok": True, "newest_size": cx.get("newest_size"),
                          "newest_age_s": cx.get("newest_age_s") if cx.get("newest_age_s") is not None
                          else round(time.time() - (cx.get("newest_mtime") or 0)),
                          "files": len(cx.get("files") or []),
                          "turns": cx.get("turns_estimate"), "stale": cx.get("stale")},
                "ag": {"ok": True, "size": ag.get("size"), "conversations": ag.get("conversations"),
                       "age_s": round(time.time() - (ag.get("mtime") or 0))}}
    except Exception as e:
        return {"error": str(e)[:80]}


def quotas(force=False):
    now = time.time()
    if not force and _QUOTA_CACHE["data"] and now - _QUOTA_CACHE["ts"] < QUOTA_TTL:
        return dict(_QUOTA_CACHE["data"], cached=True, age=round(now - _QUOTA_CACHE["ts"], 1))
    data = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import quota_reader as qr
        data = qr.read_all()
        data.pop("ts", None)
    except Exception:
        data = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import quota_reader as _qr2
        if isinstance(data, dict):
            data["codex_windows"] = _qr2.codex_windows()
    except Exception:
        pass
    if data is None:
        data = {"codex": _quota_local("codex"), "antigravity": _quota_local("antigravity"),
                "deepseek": _quota_deepseek(), "mimo": _quota_mimo(), "vertex": _quota_vertex(),
                "local": _quota_local_probe()}
    _QUOTA_CACHE["ts"] = now
    _QUOTA_CACHE["data"] = data
    return dict(data, cached=False, age=0)




def quota_loop():
    """每 60 秒主动刷新一次额度缓存，前端读 /api/status 永远是热的。"""
    time.sleep(10)
    while True:
        try:
            if not is_paused():
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import quota_reader as _qr3
                    _qr3.mimo_quota_refresh()
                except Exception:
                    pass
                quotas(force=True)
                try:
                    if time.time() - _SNAP_LAST.get("ts", 0) > 55:
                        _SNAP_LAST["ts"] = time.time()
                        threading.Thread(target=push_snapshot, daemon=True).start()
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(QUOTA_TTL)




# ---------- 本地运行态探针（Codex / Antigravity） ----------
CODEX_DIR = cfg.home_path('.codex')
AG_DIR = cfg.home_path('.gemini/antigravity')


def _jwt_claims(tok):
    import base64
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


_PROBE_CACHE = {"ts": 0, "data": {}}


def local_probes(force=False):
    if not force and _PROBE_CACHE["data"] and time.time() - _PROBE_CACHE["ts"] < 15:
        return _PROBE_CACHE["data"]
    out = {}
    # Codex
    try:
        auth = json.load(open(os.path.join(CODEX_DIR, "auth.json"), encoding="utf-8"))
        t = auth.get("tokens") or {}
        acc = _jwt_claims(t.get("access_token") or "")
        exp = acc.get("exp") or 0
        out["codex"] = {
            "ok": True,
            "auth_path": os.path.join(CODEX_DIR, "auth.json"),
            "auth_mtime": round(time.time() - os.path.getmtime(os.path.join(CODEX_DIR, "auth.json"))),
            "plan": (acc.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type"),
            "token_expires_at": exp,
            "token_left_h": round((exp - time.time()) / 3600, 1) if exp else None,
        }
    except Exception as e:
        out["codex"] = {"ok": False, "reason": str(e)[:60]}
    # Antigravity
    try:
        conv = os.path.join(AG_DIR, "conversations")
        n = len(os.listdir(conv)) if os.path.isdir(conv) else 0
        out["ag"] = {
            "ok": True,
            "dir": AG_DIR,
            "conversations": n,
            "last_active": round(time.time() - os.path.getmtime(conv)) if os.path.isdir(conv) else None,
            "running": any("Antigravity" in (p.name() or "") for p in _ag_procs()),
            "busy": (_activity.get("ag") or {}).get("state") == "running",
        }
    except Exception as e:
        out["ag"] = {"ok": False, "reason": str(e)[:60]}
    _PROBE_CACHE["ts"] = time.time()
    _PROBE_CACHE["data"] = out
    return out


def _ag_procs():
    try:
        import psutil
        return [p for p in psutil.process_iter(["name"]) if "ntigrav" in (p.info.get("name") or "")]
    except Exception:
        return []




# ================= 状态判定账本（事实驱动） =================
# 三态：idle / running / no_signal
# no_signal 再细分 verdict：timeout（超时）、loop（死循环）、silent（无心跳）、done（正常结束）
TRACK = {}
TRACK_SILENT_SEC = 180      # 心跳间隔超过这个值判 no_signal
TRACK_SILENT_BY_AGENT = {"arch": 420, "codex": 420, "ag": 420, "mimo": 240, "dsh": 420, "doubao": 240}
TRACK_LOOP_REPEAT = 3       # 同一工具+参数重复这么多次判 loop


def track_start(agent, what=""):
    TRACK[agent] = {"t0": time.time(), "last_beat": time.time(), "beats": 0,
                    "tool_hits": [], "what": what[:80], "verdict": "", "thread": None}
    with _activity_lock:
        if (_activity.get(agent) or {}).get("state") in ("timeout", "error"):
            _activity[agent] = {"state": "running", "task": what, "phase": "connecting",
                                "started": time.time(), "updated": time.time(), "files": []}


def track_beat(agent, note="", tool=None, args=None):
    t = TRACK.get(agent)
    if not t:
        track_start(agent)
        t = TRACK[agent]
    t["last_beat"] = time.time()
    t["beats"] += 1
    if note:
        t["what"] = note[:80]
    if tool:
        # 用整段参数的哈希做去重键：截断到 120 字符会把「前 120 字相同的不同命令」
        # 误判成同一个调用，进而把正常的多轮读取判成死循环（Argon 16:13 误报）。
        _args_sig = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
        key = "%s|%s|%d" % (tool, hashlib.sha256(_args_sig.encode("utf-8")).hexdigest()[:16], len(_args_sig))
        t["tool_hits"].append(key)
        if t["tool_hits"][-TRACK_LOOP_REPEAT:] == [key] * TRACK_LOOP_REPEAT:
            t["verdict"] = "loop"
        elif t.get("verdict") == "loop":
            # 出现不同调用就解除 loop 判定。原来这个标记一旦打上永久粘住，
            # 后续明明在不同行段间正常读取，也一直被报成死循环（Argon 12:04 误报）。
            t["verdict"] = ""


def track_end(agent, verdict="done"):
    t = TRACK.get(agent)
    if t:
        if verdict == "done" and t.get("verdict") in ("timeout", "timeout_5m", "loop", "error"):
            return
        t["verdict"] = verdict
        t["ended"] = time.time()
        t["last_beat"] = time.time()


def track_state(agent):
    t = TRACK.get(agent)
    now = time.time()
    if t and t.get("verdict") in ("timeout", "timeout_5m", "loop", "error"):
        state = "error" if t["verdict"] == "error" else "timeout"
        return {"state": state, "verdict": t["verdict"],
                "beat_age": round(now - t["last_beat"], 1), "beats": t.get("beats", 0),
                "what": t.get("what", "")}
    if not t or t.get("ended"):
        return {"state": "idle", "verdict": t.get("verdict", "") if t else "", "beat_age": None,
                "beats": t.get("beats", 0) if t else 0}
    alive = t.get("thread")
    alive_ok = True
    try:
        if alive is not None and isinstance(alive, threading.Thread):
            alive_ok = alive.is_alive()
    except Exception:
        pass
    if not alive_ok:
        return {"state": "idle", "verdict": "thread_dead", "beat_age": round(now - t["last_beat"], 1),
                "beats": t["beats"]}
    quiet = now - t["last_beat"]
    # 成员主动选择沉默（[[silent]]/没轮到自己）不算断线，别触发熔断
    if t.get("verdict") == "silent":
        return {"state": "idle", "verdict": "silent", "beat_age": round(quiet, 1), "beats": t.get("beats", 0)}
    # 5 分钟无工具调用与 Token 输出检测判定为超时/疑似卡死
    # 无工具调用与 Token 输出判定超时；Vertex 直连（Argon/Gemini）单轮天然慢，阈值放宽到 10 分钟
    _limit = 600 if agent in ("arch", "ag") else 300
    if quiet >= _limit:
        return {"state": "no_signal", "verdict": "timeout_5m", "beat_age": round(quiet, 1), "beats": t["beats"],
                "what": t.get("what")}
    if t.get("verdict") == "loop":
        return {"state": "no_signal", "verdict": "loop", "beat_age": round(quiet, 1), "beats": t["beats"],
                "what": t.get("what")}
    return {"state": "running", "verdict": "", "beat_age": round(quiet, 1), "beats": t["beats"],
            "what": t.get("what")}


_TAKEOVER = {}
_MIME_CACHE = {}


def _external_beat(agent):
    """外部通道（codex/ag）没有内置心跳：用文件增长证明它在动。"""
    try:
        if agent == "codex":
            # headless_ask owns the exact process and rollout for this dispatch.
            return
        elif agent == "ag":
            import antigravity_bridge as ag
            p = ag.transcript_path()
            if p and os.path.exists(p):
                st = os.stat(p)
                key = (round(st.st_mtime, 1), st.st_size)
                if _MIME_CACHE.get(agent) != key:
                    _MIME_CACHE[agent] = key
                    track_beat(agent, "transcript 增长")
        elif agent == "dsh":
            # qq-trace 会因为我自己的每次回复而增长，拿它当心跳会让「工作中」永远不消失。
            # 只有 hub 正在等这一轮（_busy 里还有 dsh）时才算心跳。
            if agent not in _busy:
                return
            p = cfg.root_path('data/media/qq-trace.jsonl')
            if p and os.path.exists(p):
                st = os.stat(p)
                key = (round(st.st_mtime, 1), st.st_size)
                if _MIME_CACHE.get(agent) != key:
                    _MIME_CACHE[agent] = key
                    track_beat(agent, "qq-trace 变动")
    except Exception:
        pass

_timeout_alert_cooldown = {}  # agent -> last alert timestamp

def notify_narrator_timeout_investigation(agent, quiet_sec):
    now = time.time()
    last = _timeout_alert_cooldown.get(agent, 0)
    if now - last < 600:
        return
    _timeout_alert_cooldown[agent] = now
    agent_label = AGENT_LABELS.get(agent, agent)
    min_cnt = max(5, int(quiet_sec // 60))
    prompt = (
        f"【系统旁白监测告警】智能体「{agent_label}」在任务执行过程中已连续超过 {min_cnt} 分钟无任何心跳（无工具调用、无 Token/输出生成），疑似卡死或掉线。\n\n"
        f"请 DeepSeek 立即进行专项故障排查：\n"
        f"1. 检查「{agent_label}」相关进程与端口是否存活，若卡死请执行重置恢复；\n"
        f"2. 若属断网、掉线等不可恢复故障，请在协作台回报标为超时下线。\n\n"
        f">>> DeepSeek"
    )
    try:
        if agent == "dsh":
            # DSH 超时：先看 8643 端口；若正常仅表示上一轮任务丢弃/完成未落库，切回空闲即可，禁止反复杀进程
            dsh_alive = False
            try:
                with socket.create_connection(("127.0.0.1", 8643), timeout=1.0):
                    dsh_alive = True
            except Exception:
                pass
            track_end("dsh", "timeout")
            mark("dsh", False)
            activity("dsh", "idle", "", phase="timeout")
            if not dsh_alive:
                subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                 "-File", cfg.root_path('scripts/restart-dsh.ps1')],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            append_msg("narrator", prompt, to=["dsh"])
            inject_qq(prompt, reply_to=["human"], source="narrator")
    except Exception as e:
        try:
            with open(os.path.join(DATA_DIR, "dispatch-debug.jsonl"), "a", encoding="utf-8") as _df:
                _df.write(json.dumps({"ts": time.time(), "event": "timeout_alert_err", "agent": agent, "error": str(e)[:100]}, ensure_ascii=False) + "\n")
        except Exception:
            pass




def track_loops():
    """每秒巡检：外部探针补心跳 + 判 no_signal + 旁白调动 DS 排查超时卡死。"""
    tick = 0
    while True:
        try:
            sync_all_agents_activity()
            if is_paused():
                time.sleep(1)
                continue
            tick += 1
            for agent in list(TRACK.keys()):
                if TRACK[agent].get("thread") and not TRACK[agent]["thread"].is_alive():
                    continue
                if tick % 5 == 0:
                    _external_beat(agent)
                st = track_state(agent)
                # dsh 的「工作中」只应在真正处理一轮时出现；超 120 秒没心跳说明回合已结束但没收到回执
                if agent != "dsh" and st.get("state") == "running" and (st.get("beat_age") or 0) > 180:
                    try:
                        mark(agent, False)
                        track_end(agent, "done")
                        _sup_record("state", "%s → idle(无心跳兜底)" % agent, agent)
                    except Exception:
                        pass
                    continue
                if agent == "dsh" and st.get("state") == "running" and (st.get("beat_age") or 0) > 120:
                    try:
                        mark("dsh", False)
                        track_end("dsh", "done")
                        _sup_record("state", "dsh → idle(回合结束兜底)", "dsh")
                    except Exception:
                        pass
                    continue
                if st["state"] == "no_signal":
                    agent_failure(agent, "执行超时：" + str(st.get("verdict", "无心跳")))
                    _sup_record("state", "%s → %s(%s)" % (agent, st["state"], st["verdict"]), agent)
                    if st.get("verdict") in ("timeout_5m", "loop"):
                        notify_narrator_timeout_investigation(agent, st.get("beat_age") or 300)
        except Exception:
            pass
        time.sleep(1)


def track_verdict_now(agent):
    t = TRACK.get(agent) or {}
    return t.get("verdict") or ""




# ================= 工单（tickets） =================
TICKETS_DIR = cfg.expand('${CONFLUX_WORKSPACE}/tickets')
try:
    from ticket_quality import spawn_async_name, validate_stage, missing_report, backfill_history, name_state, title_status_of, resume_naming  # 项目 5-1：异步命名/缺项追踪/阶段校验/历史兼容
except Exception:
    spawn_async_name = validate_stage = missing_report = backfill_history = title_status_of = resume_naming = None
TICKET_SECTIONS = ("goal", "context", "plan", "log", "acceptance", "risk", "status")


def _ticket_path(tid):
    if not isinstance(tid, str) or not re.fullmatch(r"(T[A-Za-z0-9_-]+|\d+)", tid):
        raise ValueError("invalid ticket id")
    return os.path.join(TICKETS_DIR, "%s.md" % tid)




MIMO_KEY_FILES = (os.path.join(os.path.dirname(DATA_DIR), "secrets", "mimo.key"),
                  )  # 项目7-1：余额模式，只留 sk-（api.xiaomimimo.com 按量计费）；tp-/token-plan 订阅通道轮换与回退已移除
_MIMO_KEY_RR = {"i": 0}


def mimo_keys():
    out = []
    for f in MIMO_KEY_FILES:
        try:
            k = open(f, encoding="utf-8").read().strip()
            if k:
                out.append((os.path.basename(f), k))
        except Exception:
            pass
    return out


def mimo_title(goal_text, max_len=16):
    """小模型给工单生成第 1 栏项目名；项目7-1 单 key（sk- 余额模式），失败自动换下一个。"""
    keys = mimo_keys()
    if not keys:
        return ""
    import urllib.request as _ur
    prompt = ("给下面这个任务起一个中文项目名，不超过%d个字，只输出名字本身，不要引号和解释：\n%s"
              % (max_len, (goal_text or "")[:300]))
    # 项目7-1：只走余额通道 api.xiaomimimo.com；token-plan / tp- 订阅通道的端点与回退已移除
    attempts = []
    for attempt in range(len(keys) * 2):
        key_src, key = keys[(_MIMO_KEY_RR["i"] + attempt) % len(keys)]
        # 项目7-1：凭证固定 sk-、接入点固定 api，不再按前缀配对（订阅通道已移除）
        url = "https://api.xiaomimimo.com/v1/chat/completions"
        try:
            body = json.dumps({"model": "mimo-v2.6-flash",
                               "messages": [{"role": "user", "content": prompt}],
                               "max_tokens": 256}).encode("utf-8")
            req = _ur.Request(url, data=body, method="POST",
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
            r = json.loads(_ur.urlopen(req, timeout=25).read())
            txt = (r["choices"][0]["message"].get("content") or "").strip()
            txt = re.sub(r"^[\"'「《]|[\"'」》]$", "", txt).strip()
            if txt:
                _MIMO_KEY_RR["i"] = (_MIMO_KEY_RR["i"] + attempt + 1) % len(keys)
                return txt[:max_len]
            attempts.append("%s 空回复" % url.split("/")[2])
        except Exception as exc:
            attempts.append("%s: %s" % (url.split("/")[2], str(exc)[:60]))
            time.sleep(1.5)
    try:
        _MIMO_KEY_RR["last_error"] = "; ".join(attempts)[-300:]
    except Exception:
        pass
    return ""


def _mimo_name_async(goal):
    """异步命名入口：复用 mimo_title（群外 MiMo，凭证固定走余额通道 api）；失败带原因抛出给状态机落档。"""
    n = mimo_title(goal)
    if not n:
        raise RuntimeError(_MIMO_KEY_RR.get("last_error") or "MiMo 命名返回空")
    return n


def _next_ticket_no():
    """下一个工单号：现有最大数字 +1，从 1 开始。"""
    mx = 0
    try:
        for d in (TICKETS_DIR, os.path.join(TICKETS_DIR, "archive")):
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                m = re.match(r"^(\d+)\.md$", fn)
                if m:
                    mx = max(mx, int(m.group(1)))
    except Exception:
        pass
    return mx + 1


def ticket_create(title, goal="", owner="dsh", test=False):
    """第2栏先持久化；命名失败也保留原始需求。工单号从 1 递增。"""
    os.makedirs(TICKETS_DIR, exist_ok=True)
    tid = str(_next_ticket_no())
    labels = ("项目名", "原始需求", "技术方案", "分工与执行记录", "验收", "最终效果", "状态与驳回")
    content = "# 工单 " + tid + ("（测试单）" if test else "") + "\n\n"
    for n, label in enumerate(labels, 1):
        content += "## %d. %s\n\n%s\n\n" % (n, label, goal if n == 2 else "")
    with open(_ticket_path(tid), "x", encoding="utf-8") as f:
        f.write(content)
        if test:
            f.write("\n\n<!-- test -->\n")
    _busy = any(_ticket_status_of(x[:-3]) == "doing"
                for x in os.listdir(TICKETS_DIR) if x.endswith(".md") and not x.startswith("_"))
    ticket_update(tid, "status", "queued" if _busy else "doing", owner)
    try:
        name = title if title and title.strip() != "未命名" else ""
        if name:
            ticket_update(tid, "goal", name, "mimo")
        else:
            # 项目 5-1：命名走异步状态机（pending/ok/failed + 11/30/60 退避），建单不阻塞
            ticket_update(tid, "goal", "待生成（命名中）", "mimo")
            spawn_async_name(tid, goal, name_fn=_mimo_name_async)
    except Exception:
        try:
            spawn_async_name(tid, goal, name_fn=_mimo_name_async)   # 项目 5-1：失败转异步重试
        except Exception:
            pass
    task = upsert_task(goal or title, owner)
    with _tasks_lock:
        tasks = read_tasks()
        for item in tasks:
            if item["id"] == task["id"]:
                item["ticket_id"] = tid
        write_tasks(tasks)
    # 建单后自动：小模型起名（上面已做）→ 后台叫 Argon + GPT 出方案与分工
    try:
        threading.Thread(target=ticket_autopilot, args=(tid, goal), daemon=True).start()
    except Exception:
        pass
    return {"ok": True, "id": tid, "path": _ticket_path(tid), "task_id": task["id"]}




def _ticket_status_of(tid):
    try:
        txt = open(_ticket_path(tid), encoding="utf-8", errors="ignore").read()
        sec = txt.split("## 7.", 1)[-1]
        states = re.findall(r'(?<![A-Za-z])(done|archived|blocked|review|doing|queued|ready|draft)(?![A-Za-z])', sec)
        return states[-1] if states else ""
    except Exception:
        pass
    return ""


def _ticket_quality_fields(tid, text):
    """项目 5-1 三件套：missing_fields / title_status / overdue（历史单缺字段自动兜底）。"""
    miss = []
    try:
        if missing_report is not None:
            miss = [{"label": m["label"], "state": m["state"]} for m in missing_report(text or "")]
    except Exception:
        miss = []
    try:
        ts = title_status_of(tid, text or "") if title_status_of is not None else "pending"
    except Exception:
        ts = "pending"
    overdue = False
    try:
        st = _ticket_status_of(tid) or "draft"
        overdue = st not in ("done", "archived", "blocked") and \
            (time.time() - os.path.getmtime(_ticket_path(tid))) > 48 * 3600
    except Exception:
        pass
    err = ""
    try:
        if name_state is not None:
            err = str((name_state(tid) or {}).get("reason") or "")
    except Exception:
        err = ""
    return {"missing": miss, "missing_fields": [m["label"] for m in miss],
            "title_status": ts, "title_error": err, "overdue": overdue}




def ticket_promote_next():
    """当前没有 doing 的工单时，把最早排队的提升为 doing。"""
    try:
        files = sorted([f for f in os.listdir(TICKETS_DIR)
                        if f.endswith(".md") and not f.startswith("_")],
                       key=lambda f: os.path.getmtime(os.path.join(TICKETS_DIR, f)))
    except Exception:
        return None
    live = [(f[:-3], _ticket_status_of(f[:-3])) for f in files]
    if any(st == "doing" for _, st in live):
        return None
    for tid, st in live:
        if st in ("queued", "ready"):   # draft 禁止直跳 doing，必须先经 Argon 置 ready
            result = ticket_update(tid, "status", "doing（排队转执行）", who="dsh")
            if not result.get("ok"):
                continue
            ticket_dispatch_stage(tid)
            return tid
    return None




# ================= 工单自动流程 =================
def _ticket_agent_pattern():
    aliases = _routing.alias_map()
    names = [name for name, agent in aliases.items() if agent in _routing.sender_ids()]
    return r"(?i:【(" + "|".join(re.escape(a) for a in sorted(names, key=len, reverse=True)) + r")】)"


def _provider_text(agent, prompt):
    """Generate a private plan through the existing API adapter without tools."""
    try:
        from agent_loop import Runner
        result = Runner(agent, prompt, cfg.WORKSPACE).chat(
            [{"role": "user", "content": prompt}], allow_tools=False)
        return (result.get("content") or "").strip()
    except Exception:
        return ""


def _arch_text(prompt, timeout=1800):
    """Request an Argon draft, falling back to GPT's available channel."""
    return _provider_text("arch", prompt) or _codex_text(prompt)


def _codex_text(prompt, timeout=180000):
    """Request a private plan using the detected desktop/API channel."""
    try:
        import client_probe
        if client_probe.get_client_status("codex").get("runtime_status") == "api_online":
            return _provider_text("codex", prompt)
        srv = get_codex_server()
        if srv is not None:
            text = (srv.ask(prompt, timeout=timeout) or "").strip()
            if text:
                return text
    except Exception:
        pass
    return _provider_text("codex", prompt)


def _json_block(raw):
    """从模型输出里抠出第一个 JSON 对象。"""
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _assignment_plan(prompt):
    """GPT (Codex) 总架构师决策：结合 Argon 草案定稿方案与分工。Worker 优先指派 MiMo，非确需不调 ag。"""
    last_raw = ""
    for _ in range(2):
        last_raw = _codex_text(prompt)
        data = _json_block(last_raw)
        if not isinstance(data, dict) or not isinstance(data.get('plan'), str) or not data['plan'].strip():
            continue
        roles = data.get('roles')
        if not isinstance(roles, list) or not roles:
            continue
        valid = True
        for role in roles:
            if not isinstance(role, dict) or not isinstance(role.get('work'), str) or not role['work'].strip():
                valid = False
                break
            agent = normalize_agent_id(role.get('agent', ''))
            if agent not in _routing.sender_ids():
                valid = False
                break
            if agent == 'ag' and 'Gemini必要原因：' not in role['work']:
                valid = False
                break
            role['agent'] = agent
        if valid:
            return data, last_raw
    return {}, last_raw



_ticket_run_lock = threading.RLock()

def _ticket_run_path(tid):
    _ticket_path(tid)  # validate identifier
    return os.path.join(DATA_DIR, "ticket-runs", str(tid) + ".json")

def ticket_run_read(tid):
    path = _ticket_run_path(tid)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)

def _ticket_run_write(run):
    path = _ticket_run_path(run["ticket_id"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(run, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)

def ticket_register_stages(tid, roles):
    with _ticket_run_lock:
        if ticket_run_read(tid):
            return
        # Worker first, independent code review next, integration verification last.
        order = {"mimo": 0, "ag": 0, "codex": 1, "dsh": 2}
        stages = sorted((r for r in roles if r["agent"] in _routing.sender_ids()),
                        key=lambda r: order.get(r["agent"], 0))
        run = {"ticket_id": str(tid), "roles": [
            {"agent": r["agent"], "work": r["work"], "state": "pending",
             "token": uuid.uuid4().hex, "artifacts": []} for r in stages]}
        _ticket_run_write(run)

def ticket_dispatch_stage(tid):
    with _ticket_run_lock:
        run = ticket_run_read(tid)
        if not run and _ticket_status_of(tid) == "doing":
            # Upgrade queued legacy tickets when they are actually promoted.
            content = open(_ticket_path(tid), encoding="utf-8").read()
            roles = [{"agent": normalize_agent_id(m.group(1)), "work": m.group(2).strip()}
                     for m in re.finditer(_ticket_agent_pattern() + r"([^\n]+)", _kanban_section(content, 4))]
            if roles:
                ticket_register_stages(tid, roles)
                run = ticket_run_read(tid)
            else:
                # 若第 4 栏为空（如建单/驳回时中断未出方案），自动触发重派生成方案与分工
                try:
                    goal = _kanban_section(content, 2) or _kanban_section(content, 1)
                    if goal and goal.strip():
                        threading.Thread(target=ticket_autopilot, args=(tid, goal), daemon=True).start()
                        return {"ok": True, "reason": "工单分工为空，已自动触发重跑方案与分工生成"}
                except Exception:
                    pass
        if not run or _ticket_status_of(tid) != "doing":
            return {"ok": False, "reason": "没有可执行阶段或工单未开始"}
        stage = next((r for r in run["roles"] if r["state"] != "done"), None)
        if stage is None:
            return ticket_finalize(tid)
        if stage["state"] != "pending":
            return {"ok": True, "state": stage["state"], "repeated": True}
        stage["state"] = "dispatching"
        _ticket_run_write(run)
        payload = {"action": "stage_complete", "id": str(tid), "token": stage["token"],
                   "agent": stage["agent"], "success": True, "artifacts": ["实际交付文件绝对路径"]}
        prompt = ("【%s · 你的分工】%s\n前置阶段产物：%s\n"
                  "完成后必须POST http://127.0.0.1:8777/api/ticket，JSON为：%s。"
                  "替换artifacts为真实已写入文件；失败则success=false并提供error。"
                  "聊天回复不会推进工单，不要把未完成报为成功。") % (
                      _ticket_disp(tid), stage["work"],
                      json.dumps([p for r in run["roles"] if r["state"] == "done" for p in r["artifacts"]], ensure_ascii=False),
                      json.dumps(payload, ensure_ascii=False))
        try:
            msg = append_msg("narrator", prompt, to=[stage["agent"]])
            if not msg:
                raise RuntimeError("分工消息未持久化")
            stage["state"] = "running"
            stage["message_id"] = msg["id"]
            _ticket_run_write(run)
            return {"ok": True, "agent": stage["agent"]}
        except Exception as exc:
            stage["state"] = "dispatch_error"
            stage["error"] = str(exc)
            _ticket_run_write(run)
            return {"ok": False, "reason": str(exc)}

def ticket_complete_stage(body):
    with _ticket_run_lock:
        run = ticket_run_read(body.get("id") or "")
        if not run:
            return {"ok": False, "reason": "未找到本轮执行记录"}
        stage = next((r for r in run["roles"] if r["token"] == body.get("token") and r["agent"] == body.get("agent")), None)
        if not stage:
            return {"ok": False, "reason": "执行身份或阶段凭据无效"}
        if stage["state"] == "done":
            return {"ok": True, "repeated": True}
        if stage["state"] != "running" or type(body.get("success")) is not bool:
            return {"ok": False, "reason": "阶段未运行或success非布尔值"}
        if not body["success"]:
            stage["state"] = "failed"
            stage["error"] = str(body.get("error") or "执行失败")
            _ticket_run_write(run)
            return {"ok": True, "state": "failed"}
        paths = body.get("artifacts")
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not os.path.isabs(p) or not os.path.isfile(p) for p in paths):
            return {"ok": False, "reason": "必须提交真实存在的产物绝对路径"}
        stage["state"] = "done"
        stage["artifacts"] = paths
        stage["completed_at"] = time.time()
        _ticket_run_write(run)
    return ticket_dispatch_stage(run["ticket_id"])

def ticket_set_state_natural_review(tid):
    # All explicit stages are complete; no synthetic acceptance paragraph required.
    with _project_lock:
        path = _ticket_path(tid)
        text = open(path, encoding="utf-8").read()
        if "## 7." not in text:
            text += "\n## 7. 状态与时间线\n"
        text += "\n- system：review（执行、审查、验证阶段已完成，待用户确认）\n"
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        return {"ok": True, "id": tid, "state": "review"}


def ticket_redispatch(tid):
    """驳回新单的重派入口：对指定工单重跑 Argon 草案 + GPT 分工 + 分发。"""
    try:
        txt = open(_ticket_path(tid), encoding="utf-8", errors="ignore").read()
    except OSError:
        return {"ok": False, "reason": "工单不存在"}
    if _ticket_status_of(tid) in ("done", "archived"):
        return {"ok": False, "reason": "已结工单不能重派"}
    goal = _kanban_section(txt, 2) or _kanban_section(txt, 1)
    if not (goal or "").strip():
        return {"ok": False, "reason": "缺少原始需求，无法重派"}
    try:
        threading.Thread(target=ticket_autopilot, args=(tid, goal), daemon=True).start()
    except Exception:
        return {"ok": False, "reason": "重派线程启动失败"}
    return {"ok": True, "id": tid, "redispatched": True}


def ticket_autopilot(tid, goal):
    """建单后的自动流程：Argon 草案 → GPT 定稿方案+分工 → 写 3/4 栏 → 按人分发。"""
    try:
        activity("arch", "running", "工单方案草案", phase="running")
        draft = _arch_text(
            cfg.expand('真实环境：前端是单文件 ${CONFLUX_ROOT}\\data\\hub\\index.html（无框架），后端 ${CONFLUX_ROOT}\\scripts\\hub.py，禁止虚构工程。你是 CTO Argon。给下面这个需求出一版技术方案草案（落地路径、涉及文件、风险），250 字以内，不要客套：\n') + (goal or "")[:800])
        activity("arch", "idle", "草案已出", phase="done")
        activity("codex", "running", "工单方案定稿", phase="running")
        prompt = (
            cfg.expand('【真实工作区，禁止虚构】前端 ${CONFLUX_ROOT}\\data\\hub\\index.html（单文件 HTML+JS，无框架无构建）；后端 ${CONFLUX_ROOT}\\scripts\\hub.py（Python 标准库 http.server）；文档 ${CONFLUX_WORKSPACE}。仓库里没有 Vue/React/npm 工程，方案必须落在上述文件中。\n【这是一张全新工单，与之前任何会话无关，不要沿用旧的看板/其他需求；只针对下面这条需求】\n需求：%s\n\n你是总架构师（GPT/Codex）。Argon 的草案是：\n%s\n\n请结合它产出最终技术方案与分工决策。只输出一个 JSON 对象，格式：\n{"plan": "最终方案，300字内，写清落地路径与涉及文件", "roles": [{"agent": "%s", "work": "这个人要做的具体事，含产物路径"}]}\n规则：\n1. 分工必须是可交付的、能验证的；不要给不参与的人分配。\n2. 执行人（Worker）优先指派 MiMo，尽量降低消耗；非必要不调用 Gemini（ag）。\n3. 仅在任务确需 Gemini 专有能力（如多模态视觉核验、超长复杂分析等）时才指派 ag，且其 work 必须注明「Gemini必要原因：具体能力与必要性」。')
            % ((goal or "")[:600], draft or "（无）", "|".join(sorted(_routing.sender_ids()))))
        prompt += "\n当前可分配角色与显示名：" + json.dumps(
            {p["id"]: p["display_name"] for p in _routing.load_providers()
             if p["id"] in _routing.sender_ids()}, ensure_ascii=False)
        data, raw = _assignment_plan(prompt)
        plan = (data.get("plan") or "").strip()
        roles = [r for r in (data.get("roles") or []) if isinstance(r, dict) and r.get("agent")]
        activity("codex", "idle", "方案已定", phase="done")
        if not plan:
            ticket_update(tid, "plan", "（GPT 两次方案校验失败，待人工补）%s" % (raw or "")[:200], "dsh")
            ticket_update(tid, "status", "方案生成失败，待人工处理", "dsh")
            return
        ticket_update(tid, "plan", plan, "gpt")
        if draft:
            ticket_update(tid, "plan", "Argon 草案要点：%s" % draft[:300], "arch")
        for r in roles:
            agent = str(r.get("agent")).strip().lower()
            work = str(r.get("work") or "").strip()
            if agent not in _routing.sender_ids() or not work:
                continue
            ticket_update(tid, "log", "【%s】%s" % (agent, work), "gpt")
        # 排队资格检查：已有 doing 单时只能排队，不能靠 autopilot 抢跑（审计：串行约束失效）
        _busy = False
        try:
            for _f in os.listdir(TICKETS_DIR):
                if _f.endswith('.md') and not _f.startswith('_') and _f[:-3] != tid \
                        and _ticket_status_of(_f[:-3]) == 'doing':
                    _busy = True
                    break
        except OSError:
            _busy = False
        if _busy:
            ticket_update(tid, "status", "queued（方案与分工已定，等前一张执行完）", "gpt")
        else:
            ticket_update(tid, "status", "doing（方案与分工已定）", "gpt")
        ticket_register_stages(tid, roles)
        # 仅释放当前依赖已满足的一个执行阶段。
        if not _busy:
            ticket_dispatch_stage(tid)
    except Exception as e:
        try:
            ticket_update(tid, "status", "自动流程异常：%s" % str(e)[:80], "dsh")
        except Exception:
            pass






def _is_test_ticket(tid):
    try:
        return "<!-- test -->" in open(_ticket_path(tid), encoding="utf-8", errors="ignore").read()
    except Exception:
        return False


def ticket_delete(tid):
    """删掉工单（测试单用）。"""
    p = _ticket_path(tid)
    try:
        if os.path.exists(p):
            os.remove(p)
        return {"ok": True, "id": tid, "deleted": True}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:60]}




# ================= 工单收尾：已全面由四栏规约(name/goal/plan/delivery)接管，旧5/6栏已废弃 =================


def ticket_finalize(tid, force=False):
    """四栏规约兼容：直接转自然流转 review。"""
    try:
        return ticket_set_state_natural_review(tid)
    except Exception as e:
        return {"ok": False, "reason": str(e)[:80]}


def ticket_finalize_all():
    return []


def ticket_archive(tid):
    path = _ticket_path(tid)
    if not os.path.exists(path):
        return {"ok": False, "reason": "工单不存在"}
    text = open(path, encoding="utf-8").read()
    status = text.split("## 7.", 1)[-1]
    if _ticket_status_of(tid) != "done" or "验收通过" not in text.split("## 5.", 1)[-1].split("## 6.", 1)[0]:
        return {"ok": False, "reason": "仅允许归档已验收完成工单"}
    directory = os.path.join(TICKETS_DIR, "archive")
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, tid + ".md")
    if os.path.exists(target):
        return {"ok": False, "reason": "归档已存在"}
    os.rename(path, target)
    return {"ok": True, "id": tid, "path": target, "state": "archived"}


def ticket_update(tid, section, text, who="dsh"):
    """往某一栏追加内容（section 取 goal/context/plan/log/acceptance/risk/status）。"""
    if section not in TICKET_SECTIONS:
        return {"ok": False, "reason": "section 必须是 %s" % ",".join(TICKET_SECTIONS)}
    # 项目 5-1：plan/acceptance 空文本直接拒绝，缺项不能靠空值混进 review
    if section in ("plan", "acceptance") and not (text or "").strip():
        return {"ok": False, "reason": "%s 不接受空文本" % section}
    # 项目 5-1：空方案/验收/缺项不允许靠 status 直接混进 review（转 review 前阶段校验）
    if section == "status" and re.search(r"review|待审|待验|awaiting", text or "", re.I) \
            and not re.search(r"驳回|退回|reject|blocked", text or "", re.I):
        try:
            if validate_stage is not None:
                _cur = open(_ticket_path(tid), encoding="utf-8", errors="ignore").read()
                _ok2, _miss2 = validate_stage(_cur, "review")
                if not _ok2:
                    return {"ok": False, "reason": "阶段校验未过，缺项：%s" % "、".join(_miss2)}
        except Exception as _ve:
            # 项目 5-1 fail-closed：校验异常即拒绝，不再吞异常放行
            return {"ok": False, "reason": "阶段校验异常，拒绝进入 review：%s" % str(_ve)[:120]}
    p = _ticket_path(tid)
    # 直接写 status 到 review/done 也必须过阶段校验，防止绕过 ticket_accept 的检查
    if section == "status" and callable(validate_stage):
        # 状态可能写成 "review" 或 "*→review（dsh）"：取文中出现的目标态，两个入口都必须过闸
        _m = re.findall(r"\b(review|done)\b", (text or ""))
        _st = ("done" if "done" in _m else ("review" if "review" in _m else ""))
        if _st in ("review", "done") and os.path.exists(p):
            try:
                _ok, _missing = validate_stage(open(p, encoding="utf-8", errors="ignore").read(), _st)
            except Exception as _ve:
                return {"ok": False, "reason": "阶段校验异常，拒绝进入 %s：%s" % (_st, str(_ve)[:120])}
            if not _ok:
                return {"ok": False, "reason": "缺项，不能进入 %s：%s" % (_st, "、".join(_missing))}
    if not os.path.exists(p):
        return {"ok": False, "reason": "工单不存在"}
    heads = {"goal": "## 1.", "context": "## 2.", "plan": "## 3.", "log": "## 4.",
             "acceptance": "## 5.", "risk": "## 6.", "status": "## 7."}
    content = open(p, encoding="utf-8").read()
    marker = heads[section]
    idx = content.find(marker)
    if idx < 0:
        return {"ok": False, "reason": "工单缺少字段：%s" % section}
    # 插到该栏下一个 ## 之前
    nxt = content.find("\n## ", idx + len(marker))
    insert_at = nxt if nxt > 0 else len(content)
    line = "\n- %s：%s\n" % (who, text.strip())
    content = content[:insert_at] + line + content[insert_at:]
    # 项目 5-2：同目录临时文件 + os.replace 原子提交，写失败不损坏原文、不报成功
    tmp_p = p + ".tmp"
    try:
        with open(tmp_p, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_p, p)
    except Exception as _exc:
        try:
            if os.path.exists(tmp_p):
                os.remove(tmp_p)
        except Exception:
            pass
        return {"ok": False, "reason": "写入失败，原文未改：%s" % str(_exc)[:140]}
    if section == "status" and any(k in text for k in ("review", "待审批", "待验收", "awaiting")):
        try:
            _title = ""
            try:
                _t = open(p, encoding="utf-8", errors="ignore").read()
                _m = re.search(r"#\s*工单[^｜]*｜(.+)", _t)
                _title = _m.group(1).strip() if _m else ""
            except Exception:
                pass
            qq_private("【待审批】%s%s 已完成，等你审批。" % (_title or tid, "（%s）" % tid))
        except Exception:
            pass
    if section in ("acceptance", "status"):
        try:
            close_ticket_task(tid, text)
        except Exception:
            pass
        # 15-1：DS 不再往群里发项目/工单情况消息（status/验收/排队/完成汇报），状态照常落盘
    return {"ok": True, "id": tid, "section": section}



def _kanban_section(text, number):
    match = re.search(r'^## ' + str(number) + r'\.[^\n]*\n(.*?)(?=^## \d+\.|\Z)', text, re.M | re.S)
    return match.group(1).strip() if match else ''


def ticket_kanban_list(limit=100):
    """Compact read-only view; legacy ticket_list and its callers stay intact."""
    limit = int(limit)
    if not 1 <= limit <= 500:
        raise ValueError('limit must be 1..500')
    states = ('draft', 'ready', 'queued', 'doing', 'blocked', 'review', 'done', 'archived')
    names = {p['id']: p['display_name'] for p in _routing.load_providers() if p['id'] in _routing.sender_ids()}
    aliases = _routing.alias_map()
    files = []
    try:
        entries = os.listdir(TICKETS_DIR)
    except FileNotFoundError:
        entries = []
    for fn in entries:
        if not re.fullmatch(r'(?:T[A-Za-z0-9_-]+|\d+)\.md', fn):
            continue
        path = os.path.join(TICKETS_DIR, fn)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            files.append((os.path.getmtime(path), fn, path))
        except FileNotFoundError:
            continue
    with _project_lock:
        display_numbers = {str(t['id']): '%s-%s' % (str(p['id']).removeprefix('P'), t.get('seq_id', 1))
                           for p in _project_load().get('projects', []) for t in p.get('tickets', [])}
    cards = []
    total = 0
    for _, fn, path in sorted(files, reverse=True):
        try:
            with open(path, encoding='utf-8-sig', errors='replace') as stream:
                text = stream.read()
        except FileNotFoundError:
            continue
        history = re.findall(r'(?<![A-Za-z])(' + '|'.join(states) + r')(?![A-Za-z])', _kanban_section(text, 7))
        state = history[-1] if history else 'draft'
        if state == 'archived':
            continue
        total += 1
        if len(cards) >= limit:
            continue
        titles = [re.sub(r'^-\s*[^：:]+[：:]\s*', '', line).strip() for line in _kanban_section(text, 1).splitlines() if line.strip()]
        assignees = []
        for agent in re.findall(r'【([^】\n]+)】', _kanban_section(text, 4)):
            agent = aliases.get(agent.lower(), agent.lower())
            if agent in names and agent not in [item['id'] for item in assignees]:
                assignees.append({'id': agent, 'name': names[agent]})
        _qf = _ticket_quality_fields(fn[:-3], text)
        cards.append({'id': fn[:-3], 'display_no': display_numbers.get(fn[:-3]), 'title': (titles[-1] if titles else fn[:-3])[:200],
                      'status': state, 'assignees': assignees,
                      'missing_fields': _qf['missing_fields'], 'title_status': _qf['title_status'], 'title_error': _qf.get('title_error', ''),
                      'overdue': _qf['overdue']})
    return {'tickets': cards, 'total': total, 'limit': limit, 'truncated': total > len(cards)}


def ticket_list(limit=20):
    # 状态递推：无 doing 单时自动提升最早的 queued/ready，避免永远「排队中」
    try:
        ticket_promote_next()
    except Exception:
        pass
    out = []
    _reg = {}
    try:
        with _project_lock:
            for _p in _project_load().get('projects', []):
                for _t in _p.get('tickets', []):
                    _reg[str(_t['id'])] = (_p['id'], _t.get('seq_id', 1), _t.get('round', 0))
    except Exception:
        _reg = {}
    try:
        for fn in sorted(os.listdir(TICKETS_DIR), reverse=True):
            if not fn.endswith(".md") or fn.startswith("_"):
                continue
            p = os.path.join(TICKETS_DIR, fn)
            txt = open(p, encoding="utf-8", errors="ignore").read()
            title = txt.splitlines()[0].lstrip("# ").strip() if txt else fn
            # 标题优先用第 1 栏「项目名」，编号（1-3 这种）只当标签
            try:
                _seg1 = txt.split("## 1.", 1)[1].split("\n", 1)[1].split("## 2.")[0]
                _name = re.sub(r'^\s*-\s*[^：:]+[：:]\s*', '', _seg1.strip(), flags=re.M).strip()
                _name = _name.splitlines()[0].strip() if _name else ''
                if _name and _name not in ('待命名', '{标题}'):
                    title = _name[:60]
            except Exception:
                pass
            _assignees = []
            try:
                seg4 = txt.split("## 4.")[1].split("## 5.")[0]
                for am in re.finditer(_ticket_agent_pattern(), seg4):
                    a = normalize_agent_id(am.group(1))
                    if a not in _assignees:
                        _assignees.append(a)
            except Exception:
                pass
            _pid, _seq, _rnd = _reg.get(fn[:-3], (None, None, None))
            _qf = _ticket_quality_fields(fn[:-3], txt)
            _miss = _qf["missing"]
            _naming = {"state": "idle", "tries": 0}
            try:
                if name_state is not None:
                    _naming = name_state(fn[:-3])
            except Exception:
                pass
            out.append({"id": fn[:-3], "title": title[:60],
                        "state": _ticket_status_of(fn[:-3]) or "draft",
                        "status": _ticket_status_of(fn[:-3]) or "draft",
                        "assignees": _assignees,
                        "missing": _miss,
                        "missing_fields": _qf["missing_fields"],
                        "title_status": _qf["title_status"], "title_error": _qf.get("title_error", ""),
                        "overdue": _qf["overdue"],
                        "naming": _naming,
                        "project_id": _pid, "seq_id": _seq, "round": _rnd,
                        "display_no": ((str(_pid)[1:] + "-" + str(_seq)) if _pid is not None and _seq is not None else None),
                        "mtime": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p))),
                        "content": txt})
            if len(out) >= limit:
                break
        arch_dir = os.path.join(TICKETS_DIR, "archive")
        if os.path.exists(arch_dir):
            for fn in sorted(os.listdir(arch_dir), reverse=True):
                if not fn.endswith(".md") or fn.startswith("_"):
                    continue
                p = os.path.join(arch_dir, fn)
                txt = open(p, encoding="utf-8", errors="ignore").read()
                title = txt.splitlines()[0].lstrip("# ").strip() if txt else fn
                _pid, _seq, _rnd = _reg.get(fn[:-3], (None, None, None))
                out.append({"id": fn[:-3], "title": title[:60], "state": "archived",
                            "project_id": _pid, "seq_id": _seq, "round": _rnd,
                            "display_no": ((str(_pid)[1:] + "-" + str(_seq)) if _pid is not None and _seq is not None else None),
                            "mtime": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p))),
                            "content": txt})
                if len(out) >= limit * 2:
                    break
    except Exception:
        pass
    return out




def _settle_project_rounds(tid):
    """项目交付结算：同一项目里仍有 pending 状态的其它轮次一起收口。
    每完成一张写一行 accept-journal.jsonl，中断后重跑会跳过已收口的，不会半完成。"""
    jpath = os.path.join(DATA_DIR, 'accept-journal.jsonl')
    with _project_lock:
        data = _project_load()
        proj = next((p for p in data['projects'] if any(t['id'] == tid for t in p['tickets'])), None)
        if proj is None:
            return []
        done = []
        for item in sorted(proj['tickets'], key=lambda t: t.get('seq_id') or 0):
            rid = item['id']
            if rid == tid:
                continue
            # blocked=历史驳回轮次也算关联工单：项目交付时一并收口（4-1 根因：跳过 blocked
            # 等于「项目交付只更新了最后一张」，旧轮次滞留排队区）；done/archived/missing 仍跳过保幂等
            if _ticket_status_of(rid) in ('done', 'archived', 'missing'):
                continue
            res = _ticket_update_base(rid, 'status',
                                      'done（项目 %s 交付，历史轮次同步收口）' % proj['id'], 'dsh(结算)')
            # 写失败必须抛出：不能失败也记成「已结算」，否则重试会被当成已完成
            if not (isinstance(res, dict) and res.get('ok')):
                raise RuntimeError('结算写入失败 tid=%s res=%s' % (rid, str(res)[:120]))
            if _ticket_status_of(rid) != 'done':
                raise RuntimeError('结算后状态未变成 done tid=%s 当前=%s' % (rid, _ticket_status_of(rid)))
            done.append(rid)
            try:
                with open(jpath, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'ts': time.time(), 'project': proj['id'], 'settled': rid},
                                       ensure_ascii=False) + '\n')
            except OSError:
                pass
        return done


def ticket_accept(tid, passed, reason=""):
    """第 5 栏验收：DeepSeek 写结论。无产物路径/无复现条件一律打回「待补证据」。

    5-2 修复：先把「验收结论 + done 流转」在内存拼成候选文本，对候选做 done 校验，
    再同目录临时文件 + os.replace 一次提交；任何失败都不改原文、不报成功、不发通知。
    """
    if _ticket_status_of(tid) in ('done', 'archived'):
        try:
            _settle_project_rounds(tid)
        except Exception as exc:
            return {"ok": False, "id": tid, "repeated": True,
                    "reason": "项目结算未完成：%s" % str(exc)[:160]}
        return {"ok": True, "id": tid, "repeated": True, "reason": "该轮次已验收，项目结算已补齐"}
    p = _ticket_path(tid)
    if not os.path.exists(p):
        _tk = _tickets.load(tid)
        if _tk:
            _res = _tickets.user_accept(tid, passed, reason)
            if not _res.get("ok"):
                return {"ok": False, "id": tid, "reason": _res.get("error") or "验收未通过校验"}
            if _res.get("redispatch_stage"):
                try:
                    _dispatch_stage(_res["ticket"], _res["redispatch_stage"], _res.get("agents"))
                except Exception:
                    pass
            return {"ok": True, "id": tid, "ticket": _res.get("ticket")}
        return {"ok": False, "reason": "工单不存在"}
    # 11-1：通过改为内联确认，不再依赖验收结论（空 reason 允许）；驳回仍必须带真实理由
    if not passed and not (reason or "").strip():
        return {"ok": False, "id": tid,
                "reason": "驳回理由不能为空：必须填写真实驳回原因"}
    if _ticket_status_of(tid) != "review":
        return {"ok": False, "id": tid, "reason": "工单当前不在待验收状态，请刷新"}
    txt = open(p, encoding="utf-8", errors="ignore").read()
    # 14-1：待验收通过不再被「产物路径/缺项」等完整性校验阻断，缺失历史栏目也不拦截；写入仍原子提交

    def _ins(text, marker, line):
        i = text.find(marker)
        if i < 0:
            return text.rstrip() + "\n\n" + marker + "\n" + line + "\n"
        j = text.find("\n## ", i + len(marker))
        at = j if j > 0 else len(text)
        return text[:at] + "\n" + line + "\n" + text[at:]

    verdict = "验收通过" if passed else "验收未过"
    cand = _ins(txt, "## 5.", "- dsh(验收)：%s%s" % (verdict, ("：" + reason) if reason else ""))
    if cand is None:
        return {"ok": False, "id": tid, "reason": "工单缺少第 5 栏，无法写入验收结论"}
    if passed:
        cand = _ins(cand, "## 7.", "- dsh：验收通过 → done")
        if cand is None:
            return {"ok": False, "id": tid, "reason": "工单缺少第 7 栏，无法流转 done"}
    tmp_p = p + ".tmp"
    try:
        with open(tmp_p, "w", encoding="utf-8", newline="") as f:
            f.write(cand)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_p, p)
    except Exception as _exc:
        try:
            if os.path.exists(tmp_p):
                os.remove(tmp_p)
        except Exception:
            pass
        return {"ok": False, "id": tid, "reason": "验收写入失败，原文未改：%s" % str(_exc)[:140]}
    res = {"ok": True, "id": tid, "section": "acceptance"}
    if passed:
        try:
            close_ticket_task(tid, "验收通过")
        except Exception:
            pass
        if _is_test_ticket(tid):
            ticket_delete(tid)
            return {"ok": True, "id": tid, "deleted": True, "reason": "测试单已删除"}
        if _ticket_status_of(tid) != "done":
            return {"ok": False, "id": tid, "reason": "验收写入后状态未变为 done"}
        try:
            _settle_project_rounds(tid)
        except Exception as _e:
            try:
                with open(os.path.join(DATA_DIR, 'accept-journal.jsonl'), 'a', encoding='utf-8') as _jf:
                    _jf.write(json.dumps({'ts': time.time(), 'tid': tid, 'error': str(_e)[:200]}, ensure_ascii=False) + "\n")
            except Exception:
                pass
            try:
                _ticket_update_base(tid, 'status', 'review（项目结算失败，待重试：%s）' % str(_e)[:120], 'dsh')
            except Exception:
                pass
            return {"ok": False, "id": tid, "settled": False,
                    "reason": "项目结算未完成：%s" % str(_e)[:160]}
        try:
            ticket_promote_next()
        except Exception:
            pass
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import notify as _nf
            _proj = ""
            try:
                _txt = open(p, encoding="utf-8", errors="ignore").read()
                _m = re.search(r"#\s*工单[^｜]*｜(.+)", _txt)
                _proj = _m.group(1).strip() if _m else tid
            except Exception:
                _proj = tid
            _nf.notify_completion(_proj, tid)
        except Exception:
            pass
    return res


def ticket_set_state(tid, state, who="dsh"):
    """状态流转守卫：draft→ready 只能 Argon；done 只能由验收通过触发。"""
    p = _ticket_path(tid)
    if not os.path.exists(p):
        return {"ok": False, "reason": "工单不存在"}
    txt = open(p, encoding="utf-8", errors="ignore").read()
    if state not in ("draft", "ready", "queued", "doing", "blocked", "review", "done", "archived"):
        return {"ok": False, "reason": "非法工单状态"}
    if state == "archived":
        return {"ok": False, "reason": "归档必须走archive入口"}
    if state == "ready" and who != "arch":
        return {"ok": False, "reason": "只有 Argon 能置 ready（需先补齐项目名、技术方案与最终效果）"}
    if state == "done":
        return {"ok": False, "reason": "done 由 ticket_accept 通过后自动流转"}
    return ticket_update(tid, "status", "%s→%s（%s）" % ("*", state, who), who=who)


# Project registry serializes operations in the single hub process.
_project_lock = threading.RLock()
# One reentrant lifecycle lock prevents project/run lock-order inversions.
_ticket_run_lock = _project_lock


def _project_write(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, 'projects.json')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _project_load():
    path = os.path.join(DATA_DIR, 'projects.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)  # Corrupt metadata must not silently remap IDs.
    else:
        data = {'next_id': 1, 'projects': []}
    known = {item['id'] for project in data['projects'] for item in project['tickets']}
    files = []
    for directory in (TICKETS_DIR, os.path.join(TICKETS_DIR, 'archive')):
        if os.path.isdir(directory):
            files.extend(fn[:-3] for fn in os.listdir(directory)
                         if re.fullmatch(r'(?:T[A-Za-z0-9_-]+|\d+)\.md', fn))
    for tid in sorted(set(files), key=lambda x: (0, int(x)) if x.isdigit() else (1, x)):
        if tid not in known:
            # 孤儿工单：驳回产物按「上一轮」标记回挂原项目，绝不自动开新项目
            _txt = ''
            for _d in (TICKETS_DIR, os.path.join(TICKETS_DIR, 'archive')):
                _p = os.path.join(_d, tid + '.md')
                if os.path.exists(_p):
                    try:
                        _txt = open(_p, encoding='utf-8', errors='ignore').read()
                    except OSError:
                        _txt = ''
                    break
            _prev = re.search(r'上一轮(?:工单)?[：:]\s*([A-Za-z0-9_-]+)', _txt)
            host = None
            if _prev:
                host = next((p for p in data['projects']
                             if any(t['id'] == _prev.group(1) for t in p['tickets'])), None)
            if host is None and '本轮修正要求' in _txt:
                # 旧格式驳回产物（无「上一轮」标记）：按项目名回挂原项目，同样不许开新项目
                _name = _kanban_section(_txt, 1).strip()[:40]
                if _name:
                    for _proj in data['projects']:
                        for _it in _proj['tickets']:
                            _pp = _ticket_path(_it['id'])
                            if not os.path.exists(_pp):
                                _pp = os.path.join(TICKETS_DIR, 'archive', _it['id'] + '.md')
                            try:
                                _has = os.path.exists(_pp) and _name in open(_pp, encoding='utf-8', errors='ignore').read()
                            except OSError:
                                _has = False
                            if _has:
                                host = _proj
                                break
                        if host:
                            break
            if host is not None:
                if any(t.get('id') == tid for t in host['tickets']):
                    continue
                _seq = max([int(t.get('seq_id') or 0) for t in host['tickets']] or [0]) + 1
                host['tickets'].append({'id': tid, 'seq_id': _seq,
                                        'round': len(host['tickets'])})
            elif '本轮修正要求' in _txt:
                # 驳回产物找不到宿主：隔离，绝不兜底挂首项目（审计：会污染项目一）
                _orph = data.setdefault('orphans', [])
                if tid not in _orph:
                    _orph.append(tid)
            else:
                # 正常新单（无驳回标记）：按新项目登记
                pid = 'P' + str(data['next_id'])
                data['next_id'] += 1
                data['projects'].append({'id': pid, 'project_id': pid, 'tickets': [{'id': tid, 'seq_id': 1, 'round': 0}]})
    _project_write(data)
    return data


def _retitle(tid, project_id, seq_id):
    """把首行标题统一成「项目号-工单号」，例如 # 工单 1-3。"""
    pid_no = str(project_id)[1:] if str(project_id).startswith('P') else str(project_id)
    disp = '%s-%s' % (pid_no, seq_id)
    p = _ticket_path(tid)
    if not os.path.exists(p):
        return
    try:
        txt = open(p, encoding='utf-8').read()
        lines = txt.split('\n')
        if lines and lines[0].startswith('# 工单'):
            # 保留「｜标题」后缀，与 _TEMPLATE.md 首行格式一致
            suffix = ''
            if '｜' in lines[0]:
                suffix = '｜' + lines[0].split('｜', 1)[1]
            lines[0] = '# 工单 ' + disp + suffix
            open(p, 'w', encoding='utf-8').write('\n'.join(lines))
    except OSError:
        pass


def _project_card(item, pid):
    path = _ticket_path(item['id'])
    archived = not os.path.exists(path)
    if archived:
        path = os.path.join(TICKETS_DIR, 'archive', item['id'] + '.md')
    _pid_no = str(pid)[1:] if str(pid).startswith('P') else str(pid)
    _display = '%s-%d' % (_pid_no, item.get('seq_id') or 1)
    if not os.path.exists(path):
        return dict(item, project_id=pid, display_no=_display, state='missing', status='missing', content='', title=item['id'], assignees=[])
    with open(path, encoding='utf-8-sig') as stream:
        content = stream.read()
    section = _kanban_section(content, 1)
    title = re.sub(r'^\s*-\s*[^：:]+[：:]\s*', '', section, flags=re.M).strip() or item['id']
    history = re.findall(r'(?<![A-Za-z])(draft|ready|queued|doing|blocked|review|done|archived)(?![A-Za-z])', _kanban_section(content, 7))
    state = 'archived' if archived else (history[-1] if history else 'draft')
    assignees = list(dict.fromkeys(normalize_agent_id(a) for a in re.findall(_ticket_agent_pattern(), _kanban_section(content, 4))))
    _qf = _ticket_quality_fields(item['id'], content)
    return dict(item, project_id=pid, display_no=_display, title=title[:200], state=state, status=state, content=content,
                assignees=assignees, mtime=time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(path))),
                missing=_qf['missing'], missing_fields=_qf['missing_fields'],
                title_status=_qf['title_status'], title_error=_qf.get('title_error', ''), overdue=_qf['overdue'],
                type=('首次交付' if item['round'] == 0 else ('二三四五六七八九十'[item['round'] - 1] + '次投递') if item['round'] <= 9 else '%d次投递' % (item['round'] + 1)))


def _project_snapshot(project):
    with _tasks_lock:
        tasks = read_tasks()
        key = 'project:' + project['id']
        if any(t.get('id') == key for t in tasks):
            return
        tasks.append({'id': key, 'kind': 'project_snapshot', 'project_id': project['id'],
                      'name': project['title'], 'goal': project['title'], 'status': 'done',
                      'start': time.time(), 'end': time.time(), 'owner': 'human', 'roles': [],
                      'tickets': project['tickets'], 'ticket_ids': [t['id'] for t in project['tickets']]})
        # Keep all completed project snapshots; ordinary task retention is unchanged.
        path = TASKS_FILE
        tmp = path + '.project.tmp'
        with open(tmp, 'w', encoding='utf-8') as stream:
            json.dump(tasks, stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)


def project_list():
    with _project_lock:
        data = _project_load()
        out = []
        for project in data['projects']:
            cards = [_project_card(item, project['id']) for item in sorted(project['tickets'], key=lambda t: t['seq_id'])]
            latest = cards[-1]
            state = 'archived' if all(t['state'] == 'archived' for t in cards) else ('done' if latest['state'] in ('done', 'archived') else 'pending')
            result = dict(project, title=cards[0]['title'], state=state, status=state, tickets=cards)
            if state in ('done', 'archived'):
                _project_snapshot(result)
            out.append(result)
        return {'projects': out}


def _project_reject(tid, reason):
    with _project_lock:
        data = _project_load()
        project = next((p for p in data['projects'] if any(t['id'] == tid for t in p['tickets'])), None)
        if project is None:
            return {'ok': False, 'reason': '工单不存在'}
        pos = next(i for i, t in enumerate(project['tickets']) if t['id'] == tid)
        if pos < len(project['tickets']) - 1:
            return dict(ok=True, id=tid, project_id=project['id'], next_ticket_id=project['tickets'][pos+1]['id'], repeated=True)
        old = _project_card(project['tickets'][pos], project['id'])
        if old['state'] in ('done', 'archived', 'missing'):
            return {'ok': False, 'reason': '已完成或不存在工单不能驳回'}
        next_id = str(_next_ticket_no())
        seq = len(project['tickets']) + 1
        content = '# 工单 ' + next_id + '\n\n'
        for number, label in enumerate(('项目名', '原始需求', '技术方案', '分工与执行记录', '验收', '项目交付说明', '状态与驳回'), 1):
            value = _kanban_section(old['content'], number) if number in (1, 2, 3) else ''
            if number == 2:
                value += '\n\n本轮修正要求：' + reason
            if number == 7:
                value = '- human：queued\n- 上一轮：' + tid
            content += '## %d. %s\n\n%s\n\n' % (number, label, value)
        # 先登记并落盘，再建文件：任何中断都不会留下"有 md 没登记"的孤儿
        project['tickets'].append({'id': next_id, 'seq_id': seq, 'round': seq-1})
        _project_write(data)
        with open(_ticket_path(next_id), 'x', encoding='utf-8') as stream:
            stream.write(content)
        try:
            _retitle(next_id, project['id'], seq)
        except Exception:
            pass
        _ticket_update_base(tid, 'acceptance', '验收未过：' + reason, 'human')
        _ticket_update_base(tid, 'status', 'blocked', 'human')
        # 状态递推收敛：执行头被驳回后没有 doing 单了，把最早排队的单提升为可执行
        try:
            ticket_promote_next()
        except Exception:
            pass
        # 驳回新单的重派入口：新单建好后自动重跑草案/分工/分发流程
        try:
            threading.Thread(target=ticket_redispatch, args=(next_id,), daemon=True).start()
        except Exception:
            pass
        return dict(ok=True, id=tid, project_id=project['id'], next_ticket_id=next_id, seq_id=seq, round=seq-1)


# Preserve established entry points while adding project lifecycle hooks.
_ticket_create_base = ticket_create
_ticket_accept_base = ticket_accept
_ticket_update_base = ticket_update


def ticket_create(title, goal='', owner='dsh', test=False, request_id=None):
    if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 96):
        return {'ok': False, 'reason': 'request_id须为1..96字符'}
    payload = {'title': title, 'goal': goal, 'owner': owner, 'test': bool(test)}
    with _project_lock:
        if request_id:
            data = _project_load()
            cached = data.get('create_requests', {}).get(request_id)
            if cached:
                if cached.get('payload') != payload:
                    return {'ok': False, 'error': 'idempotency_conflict', 'reason': 'request_id已使用，内容不同或旧记录缺少请求摘要'}
                return dict(cached['result'], repeated=True)
        result = _ticket_create_base(title, goal, owner, test)
        if result.get('ok'):
            data = _project_load()
            project = next(p for p in data['projects'] if any(t['id'] == result['id'] for t in p['tickets']))
            result.update(project_id=project['id'], seq_id=1, round=0)
            try:
                _retitle(result['id'], project['id'], 1)
            except Exception:
                pass
            if request_id:
                data.setdefault('create_requests', {})[request_id] = {'payload': payload, 'result': dict(result)}
                _project_write(data)
        return result


def ticket_accept(tid, passed, reason=''):
    if type(passed) is not bool:
        return {'ok': False, 'reason': 'passed必须为JSON布尔值'}
    # 11-1：通过走内联确认，不再依赖验收结论（允许空 reason）；驳回必须带真实理由（空理由拦截）
    if not passed and not (reason or '').strip():
        return {'ok': False, 'id': tid, 'reason': '驳回理由不能为空：必须填写真实驳回原因'}
    with _project_lock:
        if not passed:
            return _project_reject(tid, reason)
        data = _project_load()
        project = next((p for p in data['projects'] if any(t['id'] == tid for t in p['tickets'])), None)
        if project and project['tickets'][-1]['id'] != tid:
            return {'ok': False, 'reason': '只能验收项目最新一轮工单'}
        result = _ticket_accept_base(tid, passed, reason)
        project_list()
        return result


def ticket_update(tid, section, text, who='dsh'):
    with _project_lock:
        result = _ticket_update_base(tid, section, text, who)
        if result.get('ok'):
            if section == 'status':
                project_list()
            try:
                _compressed_store().sync_projects(TICKETS_DIR, _routing.alias_map(), os.path.join(DATA_DIR, 'projects.json'))
            except Exception as error:
                _memory_failure('project', error)
        return result


def read_tasks():
    try:
        with open(TASKS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def write_tasks(tasks):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = TASKS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([t for t in tasks if t.get('kind') == 'project_snapshot'] + [t for t in tasks if t.get('kind') != 'project_snapshot'][-200:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, TASKS_FILE)
    except Exception:
        pass


def upsert_task(name, owner, roles=None, status="running", evidence=""):
    """派发时建单；同一 owner 的未结任务直接收口，避免堆叠。"""
    now = time.time()
    with _tasks_lock:
        tasks = read_tasks()
        tasks.append({"id": uuid.uuid4().hex[:8], "name": (name or "")[:80],
                      "status": status, "start": now, "end": None,
                      "owner": owner, "roles": roles or [], "evidence": evidence})
        write_tasks(tasks)
        return tasks[-1]




def close_ticket_task(tid, evidence=""):
    """工单推进（转review/done）时，自动结清 tasks.json 中关联的任务卡"""
    if _ticket_status_of(tid) not in ("done", "archived", "blocked"):
        return False
    now = time.time()
    with _tasks_lock:
        tasks = read_tasks()
        hit = False
        for t in tasks:
            if str(t.get("ticket_id") or "") == str(tid) and (t.get("status") or "") in ACTIVE_STATES:
                t["status"] = "done"
                t["end"] = now
                if evidence:
                    t["evidence"] = evidence[:200]
                hit = True
        if hit:
            write_tasks(tasks)
        return hit


_PROC_CACHE = {}




def health_snapshot():
    """真实健康检查：通道是否活着，联动 client_probe，兼顾桌面端直连与 API 降级。"""
    out = {}
    global _PROBE_MISS
    try:
        import client_probe as _cp
        probes = _cp.probe_all_clients()
    except Exception:
        probes = {}

    for cid in ("codex", "ag", "doubao", "workbuddy", "dsh"):
        st = probes.get(cid) or {}
        r_status = st.get("runtime_status", "offline")
        if r_status == "desktop_online":
            out[cid] = {"channel_ok": True, "detail": "桌面端直连", "runtime_status": "desktop_online"}
        elif r_status == "api_online":
            out[cid] = {"channel_ok": True, "detail": "API 降级在线", "runtime_status": "api_online"}
        elif st.get("process_running"):
            out[cid] = {"channel_ok": True, "detail": "本地进程运行中", "runtime_status": "desktop_online"}
        else:
            # 探针平滑防抖：放宽至 6 次（约 12 秒）探测失败才改判离线，避免 UI 瞬态闪烁「离线」
            _prev = _PROBE_MISS.get(cid) or {}
            _last_ok = _prev.get("ok_state")
            _miss = (_prev.get("miss") or 0) + 1
            if _miss < 6 and _last_ok:
                out[cid] = {"channel_ok": True, "detail": "上次在线（探针重试中）",
                            "runtime_status": _last_ok}
            else:
                out[cid] = {"channel_ok": False, "detail": st.get("detail") or "离线/未启动",
                            "runtime_status": r_status}
            _PROBE_MISS[cid] = {"miss": _miss, "ok_state": _last_ok}
        if r_status in ("desktop_online", "api_online") or st.get("process_running"):
            _PROBE_MISS[cid] = {"miss": 0, "ok_state": "desktop_online" if st.get("process_running") else r_status}
        for key in ("readiness_state", "process_running", "authenticated"):
            out[cid][key] = st.get(key)
        if r_status == "desktop_online" and st.get("detail"):
            out[cid]["detail"] = st["detail"]

    napcat_ok = bool(((probes.get("dsh") or {}).get("qq_bridge") or {}).get("channel_ok"))
    out["dsh"]["qq_bridge"] = {
        "napcat": napcat_ok,
        "channel_ok": napcat_ok,
        "last_inject_age_s": round(time.time() - _LAST_INJECT["ts"], 1) if _LAST_INJECT["ts"] else None
    }
    for api_id in ("arch", "mimo"):
        api_ok = api_provider_ready({"id": api_id})
        out[api_id] = {
            "channel_ok": api_ok,
            "detail": "云端 API 在线" if api_ok else "缺少 API 密钥",
            "runtime_status": "api_online" if api_ok else "offline"
        }
    for provider in providers_read().get("providers", []):
        pid = provider.get("id")
        if pid in _routing.sender_ids() and pid not in out:
            ready = api_provider_ready(provider)
            out[pid] = {"channel_ok": ready, "detail": "云端 API 在线" if ready else "API 未就绪",
                        "runtime_status": "api_online" if ready else "offline"}
    return out




_codex_srv_lock = threading.Lock()


HEADLESS_ID = os.path.join(DATA_DIR, "codex-headless-id.txt")


def _parse_exec_reply(out):
    lines = [l for l in (out or "").splitlines()]
    reply = []
    grab = False
    for l in lines:
        t = l.strip()
        if t.lower() == "codex":
            grab = True
            reply = []
            continue
        if grab:
            if t.startswith("tokens used") or t == "--------":
                break
            reply.append(l)
    text = "\n".join(reply).strip()
    # 去掉模型把同一句重复两遍的情况
    parts = [x.strip() for x in text.splitlines() if x.strip()]
    if len(parts) >= 2 and parts[0] == parts[-1]:
        text = parts[0]
    return text




HEADLESS_MAX_BYTES = 14 * 1024 * 1024


def headless_rotate_if_needed():
    """会话文件超过阈值就换新：删掉 id 文件，下次自动新建，不额外花 token。"""
    try:
        if not os.path.exists(HEADLESS_ID):
            return
        sid = open(HEADLESS_ID, encoding="utf-8").read().strip()
        if not sid:
            return
        import glob as _g
        pat = os.path.expanduser("~/.codex/sessions/**/rollout-*" + sid + "*.jsonl")
        files = _g.glob(pat, recursive=True)
        if not files:
            return
        size = os.path.getsize(files[0])
        if size > HEADLESS_MAX_BYTES:
            return sid  # Keep old pointer until replacement initialization succeeds.
    except Exception:
        pass


def _ask_codex_inner(text):
    """优先常驻 app-server（不用每次起进程，快）；不行再走 headless exec。"""
    global _CS, _codex_down_until
    if time.time() >= _codex_down_until:
        with _codex_srv_lock:
            srv = get_codex_server()
            if srv is not None:
                try:
                    _t0 = time.time()
                    reply = srv.ask(text, timeout=CODEX_TIMEOUT)
                    _dt = round(time.time() - _t0, 1)
                    try:
                        activity("codex", "idle", "上一轮 %ss" % _dt, phase="idle")
                    except Exception:
                        pass
                    if not (reply and reply.strip()):
                        append_msg("codex", "（这一轮跑了 %s 秒，没有产出内容）" % _dt)
                    if reply and reply.strip():
                        _codex_down_until = 0
                        if _codex_seen is not None:
                            _codex_seen.add(codex_key(reply))
                        append_msg("codex", reply)
                        return
                except Exception as e:
                    # 把完整堆栈落盘：WinError 32 的截断路径查不出是哪个文件，以后有据可查
                    try:
                        import traceback as _tb
                        _p = cfg.root_path('data/hub/appserver-fallback.log')
                        with open(_p, "a", encoding="utf-8") as _f:
                            _f.write("=== %s ===\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), _tb.format_exc()))
                    except Exception:
                        pass
                    append_msg("codex", "（常驻通道异常，转 headless：%s）" % str(e)[:60])
                    try:
                        srv.stop()
                    except Exception:
                        pass
                    _CS = None
    headless_ask(text)




CONTEXT_FILE = cfg.expand('${CONFLUX_WORKSPACE}/CONTEXT.md')


def _hidden_startup():
    """给控制台程序创建一个「隐藏但存在」的新控制台：子进程继承它，不再各自弹窗。"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return si
    except Exception:
        return None




def _run_headless_process(args, text, cwd, sid, first_timeout=45, stall_timeout=180):
    import glob
    import queue
    events = queue.Queue()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    rollout = None
    signature = None
    if sid:
        matches = glob.glob(os.path.join(CODEX_SESSIONS, "**", "rollout-*" + sid + "*.jsonl"), recursive=True)
        if matches:
            rollout = matches[0]
            stat = os.stat(rollout)
            signature = (stat.st_mtime_ns, stat.st_size)
    started = last_signal = time.monotonic()
    received = False
    next_discovery = 0
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd=cwd, startupinfo=_hidden_startup(),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def read_stream(label, stream):
        try:
            while True:
                chunk = stream.read1(4096)
                if not chunk:
                    break
                events.put((label, chunk))
        finally:
            events.put((label, None))
            stream.close()

    def write_input():
        try:
            process.stdin.write(text.encode("utf-8"))
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    readers = [threading.Thread(target=read_stream, args=(label, stream), daemon=True)
               for label, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for reader in readers:
        reader.start()
    writer = threading.Thread(target=write_input, daemon=True)
    writer.start()
    closed = set()
    try:
        while process.poll() is None or len(closed) < 2:
            grew = False
            try:
                label, chunk = events.get(timeout=0.2)
                if chunk is None:
                    closed.add(label)
                else:
                    output[label].extend(chunk)
                    grew = True
            except queue.Empty:
                pass
            if not sid:
                header = bytes(output["stdout"][:32768] + output["stderr"][:32768]).decode("utf-8", "ignore")
                match = re.search(r"session id:\s*([0-9a-f-]{36})", header)
                if match:
                    sid = match.group(1)
            now = time.monotonic()
            if sid and not rollout and now >= next_discovery:
                # New sessions live in today's shard. Resumed sessions were resolved once above.
                shard = time.strftime("%Y/%m/%d")
                matches = glob.glob(os.path.join(CODEX_SESSIONS, shard, "rollout-*" + sid + "*.jsonl"))
                rollout = matches[0] if matches else None
                next_discovery = now + 1
            if rollout:
                try:
                    stat = os.stat(rollout)
                    current = (stat.st_mtime_ns, stat.st_size)
                    if current != signature:
                        grew = True
                        signature = current
                except OSError:
                    pass
            if grew:
                received = True
                last_signal = now
                track_beat("codex", "本轮输出 / rollout 增长")
                activity("codex", "running", "本轮输出 / rollout 增长", phase="streaming")
            limit = stall_timeout if received else first_timeout
            if process.poll() is None and now - last_signal >= limit:
                raise TimeoutError("Codex %s超时（静默 %.0fs）" % ("输出中断" if received else "未响应", limit))
            if process.poll() is None and now - started >= 1800:
                raise TimeoutError("Codex 总执行超时（1800s）")
    finally:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.poll() is None:
                process.kill()
        process.wait(timeout=10)
        writer.join(timeout=2)
        for reader in readers:
            reader.join(timeout=2)
    return subprocess.CompletedProcess(args, process.returncode, bytes(output["stdout"]), bytes(output["stderr"]))


def headless_ask(text):
    rotation_from = headless_rotate_if_needed()
    exe = find_codex()
    cwd = cfg.WORKSPACE
    sid = ""
    try:
        if os.path.exists(HEADLESS_ID):
            with open(HEADLESS_ID, encoding="utf-8") as session_file:
                sid = session_file.read().strip()
    except Exception:
        sid = ""
    if rotation_from:
        sid = ""
    # 控制台程序用 SW_HIDE + CREATE_NEW_CONSOLE 起：控制台存在但不可见，子进程继承它，不再闪窗
    slot = {}
    ensure_client_session("codex", sid or "new-headless",
                          lambda prompt: slot.update(prompt=prompt) or True)
    system = slot.get("prompt") or bootstrap.snapshot("codex")["prompt"]
    args = [exe, "exec", "-c", 'sandbox_mode="workspace-write"',
            "-c", "developer_instructions=" + json.dumps(system, ensure_ascii=False), "--skip-git-repo-check"]
    if sid:
        args += ["resume", "--skip-git-repo-check", sid, "-"]
    else:
        args += ["-"]
    p = _run_headless_process(args, text, cwd, sid)
    out = (p.stdout or b"").decode("utf-8", "ignore")
    err = (p.stderr or b"").decode("utf-8", "ignore")
    final_stdout = out.strip()
    if not sid:
        import re as _re
        m = _re.search(r"session id:\s*([0-9a-f-]{36})", out + err)
        if m and p.returncode == 0 and final_stdout:
            try:
                with open(HEADLESS_ID + ".tmp", "w", encoding="utf-8") as f:
                    f.write(m.group(1))
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(HEADLESS_ID + ".tmp", HEADLESS_ID)
            except Exception:
                pass
    if p.returncode != 0:
        raise RuntimeError("Codex 初始化/执行失败；原会话指针保留，待重试")
    reply = final_stdout or _parse_exec_reply(out + "\n" + err)
    if reply:
        if _codex_seen is not None:
            _codex_seen.add(codex_key(reply))
        append_msg("codex", reply)
    else:
        append_msg("codex", "（这一轮没拿到回复，稍后重试）")




NETEASE_BRIDGE = "http://127.0.0.1:8788"
_netease_lock = threading.Lock()


def _netease_ensure():
    """确保网易云本地桥在跑（node netease_svc.mjs），首次按需拉起。"""
    def alive():
        try:
            s = _sock_boot.socket(_sock_boot.AF_INET, _sock_boot.SOCK_STREAM)
            s.settimeout(0.4)
            try:
                return s.connect_ex(("127.0.0.1", 8788)) == 0
            finally:
                s.close()
        except Exception:
            return False
    if alive():
        return True
    with _netease_lock:
        if alive():
            return True
        node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
        try:
            subprocess.Popen([node, os.path.join(os.path.dirname(os.path.abspath(__file__)), "netease_svc.mjs")],
                             startupinfo=_hidden_startup(),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            return False
    for _ in range(24):
        time.sleep(0.25)
        if alive():
            return True
    return False


_music_lock = threading.Lock()
_MUSIC_PLAYER = {"seq": 0, "cmd": None,
                 "state": {"seq": 0, "name": "", "artists": "", "playing": False, "ts": 0}}


# ── provider 注册 / 能力探针（P0-2 接线点，只有 hub 进程写 providers.json） ──
PROVIDERS_FILE = os.path.join(DATA_DIR, "providers.json")
_providers_lock = threading.Lock()


def providers_read():
    try:
        pfile = globals().get("PROVIDERS_FILE") or PROVIDERS_FILE
        with open(pfile, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = {"version": 1, "providers": data}
        data.setdefault("providers", [])
        return data
    except Exception:
        return {"version": 1, "providers": []}


def providers_write(data):
    bootstrap.refresh(data.get("providers", []))
    os.makedirs(os.path.dirname(PROVIDERS_FILE), exist_ok=True)
    tmp = PROVIDERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROVIDERS_FILE)
    _routing._CACHE["key"] = None


SECRET_DIRS = (os.path.abspath(os.path.join(DATA_DIR, "secrets")) + os.sep,
               os.path.abspath(cfg.root_path("data/secrets")) + os.sep)


def _secret_from_ref(ref):
    """api_key_ref 支持 env:VAR 和 file:路径（仅限 secrets 目录），绝不把明文写进 providers.json。

    只允许读 secrets 目录，是为了堵住「注册一个 endpoint，再用 file: 指向任意文件，
    让探针把内容塞进请求头外带」这条路（Argon 09:29 review）。
    """
    ref = str(ref or "").strip()
    if not ref:
        return ""
    try:
        if ref.startswith("env:"):
            return os.environ.get(ref[4:].strip(), "")
        if ref.startswith("file:"):
            path = os.path.realpath(ref[5:].strip())
            if not any(path.lower().startswith(os.path.realpath(d).lower() + os.sep) for d in SECRET_DIRS):
                return ""
            if not os.path.isfile(path):
                return ""
            from provider_adapter import read_secret_file
            return read_secret_file(path)
    except OSError:
        return ""
    return ""


def _host_is_internal(host):
    """回环/私网/链路本地/保留地址一律视为内网目标（探针防 SSRF）。"""
    import ipaddress as _ip
    import socket as _sk
    host = str(host or "").strip()
    if not host or host.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        infos = _sk.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = _ip.ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
    return False


ADMIN_TOKEN_FILE = os.path.join(DATA_DIR, "admin.token")
_ADMIN_TOKEN = ""
_ADMIN_TOKEN_LOCK = threading.Lock()


def _admin_token():
    """内存态管理令牌：启动时随机生成，同时落一份到 data\admin.token 供本机脚本使用；
    agent 的工具环已把该文件列入只读禁止，普通模型读不到。"""
    global _ADMIN_TOKEN
    with _ADMIN_TOKEN_LOCK:
        if not _ADMIN_TOKEN:
            import secrets as _secrets
            _ADMIN_TOKEN = _secrets.token_hex(32)
            try:
                os.makedirs(os.path.dirname(ADMIN_TOKEN_FILE), exist_ok=True)
                tmp = ADMIN_TOKEN_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(_ADMIN_TOKEN)
                os.replace(tmp, ADMIN_TOKEN_FILE)
            except OSError:
                pass
        return _ADMIN_TOKEN


def _admin_ok(handler):
    """接口鉴权：显式传错必拦截；本地缺省畅通免登。"""
    token_hdr = (handler.headers.get("X-Admin-Token") or "").strip()
    cookie = (handler.headers.get("Cookie") or "")
    got = token_hdr
    if not got:
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "conflux_admin":
                got = v.strip()
                break

    expected = _admin_token()
    if got:
        if got == expected:
            return True, ""
        return False, "令牌无效"

    try:
        peer = handler.client_address[0]
    except Exception:
        peer = ""
    is_loopback = peer in ("127.0.0.1", "::1", "localhost", "")
    is_local_net = (
        is_loopback or
        peer.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
                         "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
                         "172.28.", "172.29.", "172.30.", "172.31."))
    )
    if is_local_net:
        return True, ""

    origin = (handler.headers.get("Origin") or "").strip().lower()
    if ":8777" in origin or not origin:
        return True, ""

    return False, "需要管理权限"


def _adapter_protocol(p):
    return re.sub(r"[^a-z]", "", str(p or "").lower())


def _build_api_headers(proto, secret, endpoint=""):
    """Build standard auth headers for OpenAI/Anthropic/Gemini/Azure endpoints."""
    headers = {}
    if not secret:
        return headers
    low_ep = (endpoint or "").lower()
    if proto == "anthropicmessages":
        headers["x-api-key"] = secret
    elif proto == "gemininative":
        headers["x-goog-api-key"] = secret
    elif ".openai.azure.com" in low_ep or ".cognitiveservices.azure.com" in low_ep:
        headers["api-key"] = secret
        headers["Authorization"] = "Bearer " + secret
    else:
        headers["Authorization"] = "Bearer " + key if False else "Bearer " + secret
    return headers


def _normalize_endpoint_url(endpoint, proto, model=""):
    """Normalize base URLs into full inference endpoints while keeping query parameters (e.g., Azure api-version)."""
    ep = str(endpoint or "").strip()
    if not ep:
        return ep
    parsed = urlparse(ep)
    path = (parsed.path or "").rstrip("/")
    query = ("?" + parsed.query) if parsed.query else ""
    base = "%s://%s" % (parsed.scheme, parsed.netloc)
    if proto == "openaichat":
        if not path:
            path = "/v1/chat/completions"
        elif path in ("/v1", "/api/v3", "/api/paas/v4", "/compatible-mode/v1", "/openai/v1", "/v1beta/openai"):
            path = path + "/chat/completions"
    elif proto == "anthropicmessages":
        if not path:
            path = "/v1/messages"
        elif path == "/v1":
            path = "/v1/messages"
    elif proto == "gemininative":
        if not path:
            path = "/v1beta/models/{model}:generateContent"
        elif path in ("/v1beta", "/v1"):
            path = path + "/models/{model}:generateContent"
    return base + path + query


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _json(self, obj, code=200):
        try:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def _register_provider(self, body):
        fields = {"id", "endpoint_url", "protocol", "model", "api_key_ref", "api_key", "name",
                  "display_name", "aliases", "enabled", "role", "color", "name_color", "bubble_color", "avatar", "system_prompt", "mode"}
        controls = {"action", "upsert", "force"}
        if not isinstance(body, dict) or set(body) - fields - controls:
            self._json({"ok": False, "reason": "存在不支持的配置字段"}, 400)
            return
        for key, value in body.items():
            if key == "aliases":
                valid = isinstance(value, list) and all(isinstance(a, str) and a.strip() for a in value)
            elif key in ("enabled", "upsert", "force"):
                valid = type(value) is bool
            else:
                valid = isinstance(value, str)
            if not valid:
                self._json({"ok": False, "reason": key + " 类型无效"}, 400)
                return
        pid = body.get("id", "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", pid) or pid == "human":
            self._json({"ok": False, "reason": "id 必须为字母开头的英文、数字、下划线或短横线，human 受保护"}, 400)
            return
        with _providers_lock:
            data = providers_read()
            previous = next((p for p in data["providers"] if p.get("id") == pid), None)
        updating = body.get("action") == "update" or body.get("mode") == "update" or body.get("upsert") is True
        if previous is not None and not updating:
            self._json({"ok": False, "reason": "provider 已存在，请使用 action=update 或 upsert=true"}, 409)
            return
        if previous is None and (body.get("action") == "update" or body.get("mode") == "update"):
            self._json({"ok": False, "reason": "provider 不存在"}, 404)
            return
        if previous is None and pid in _routing.RESERVED_IDS:
            self._json({"ok": False, "reason": "id 为保留身份"}, 403)
            return
        provider = dict(previous or {})
        provider.update({k: v for k, v in body.items() if k in fields - {"api_key", "name", "mode"}})
        if body.get("mode") not in (None, "update"):
            provider["mode"] = body["mode"]
        provider["id"] = pid
        provider["display_name"] = body.get("display_name") or body.get("name") or provider.get("display_name") or pid
        provider.setdefault("enabled", True)
        for _ck in ("color", "name_color", "bubble_color"):
            if provider.get(_ck) and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(provider[_ck])):
                self._json({"ok": False, "reason": "%s 必须是 #RRGGBB 形式" % _ck}, 400)
                return
        if provider.get("color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", provider["color"]):
            self._json({"ok": False, "reason": "color 必须是 #RRGGBB"}, 400)
            return
        avatar = provider.get("avatar", "")
        if avatar and not (re.fullmatch(r"/avatar/[A-Za-z0-9_.-]+", avatar) or avatar.startswith("https://")):
            self._json({"ok": False, "reason": "avatar 必须是头像库路径或 HTTPS 图片 URL"}, 400)
            return
        mine = {pid, provider["display_name"].strip().lower(), *(a.strip().lower() for a in provider.get("aliases", []))}
        def clashes(entries):
            for entry in entries:
                if entry.get("id") == pid:
                    continue
                taken = {str(entry.get("id", "")).lower(), str(entry.get("display_name", "")).lower(),
                         *(str(a).strip().lower() for a in entry.get("aliases", []))}
                if (mine - {""}) & taken:
                    return True
            return False
        if clashes(data["providers"]) or ((mine & _routing.RESERVED_IDS) - {pid}):
            self._json({"ok": False, "reason": "名字或别名与其他角色冲突"}, 409)
            return
        key = body.get("api_key", "").strip()
        api_fields = {"endpoint_url", "protocol", "model", "api_key_ref"}
        test_required = bool(key) or any(k in body and body[k] != (previous or {}).get(k) for k in api_fields)
        test_required = test_required or (previous is None and bool(provider.get("endpoint_url")))
        status_val, detail = "", ""
        encrypted = None
        if test_required:
            endpoint = str(provider.get("endpoint_url") or "").strip()
            proto = _adapter_protocol(provider.get("protocol"))
            try:
                parsed = urlparse(endpoint)
                valid = parsed.scheme in ("http", "https") and parsed.hostname and not (
                    parsed.username or parsed.password or parsed.fragment)
                if parsed.query and any(bad in parsed.query.lower() for bad in ("key=", "api_key=", "token=", "secret=")):
                    valid = False
                parsed.port
            except ValueError:
                valid = False
            if not valid or proto not in ("openaichat", "anthropicmessages", "gemininative") or not provider.get("model"):
                self._json({"ok": False, "reason": "请提供完整 Endpoint、支持的 Protocol 和 Model"}, 400)
                return
            endpoint = _normalize_endpoint_url(endpoint, proto, str(provider.get("model")))
            provider["endpoint_url"] = endpoint
            if os.environ.get("HUB_PROBE_ALLOW_PRIVATE", "") != "1" and _host_is_internal(parsed.hostname):
                self._json({"ok": False, "status": "UNREACHABLE", "reason": "Endpoint 无法连接、DNS 解析失败或指向受限内网地址"}, 400)
                return
            ref = provider.get("api_key_ref", "")
            if not key and ref and not ((ref.startswith("env:") and ref[4:].strip()) or (
                    ref.startswith("file:") and any(os.path.realpath(ref[5:]).lower().startswith(
                        os.path.realpath(d).lower() + os.sep) for d in (os.path.join(DATA_DIR, "secrets"), cfg.root_path("data/secrets"))))):
                self._json({"ok": False, "reason": "api_key_ref 无效或超出 secrets 目录"}, 400)
                return
            import provider_adapter as pa
            secret = key or _secret_from_ref(ref)
            headers = _build_api_headers(proto, secret, endpoint)
            try:
                result = pa.probe_capability(proto, endpoint, str(provider["model"]), headers=headers, timeout=3.0)
                status_val, detail = result.status.value, result.detail
                verified = status_val in ("NATIVETOOLS", "NONATIVETOOLS")
                if not verified and body.get("force") is not True:
                    self._json({"ok": False, "status": status_val, "reason": detail}, 400)
                    return
                provider["protocol"] = proto
                provider["probe_status"] = status_val
                provider["probe_detail"] = detail
                provider["capabilities"] = ({"native_tools": status_val == "NATIVETOOLS",
                    "xml_react": status_val == "NONATIVETOOLS", "probe_detail": detail,
                    "probed_at": time.strftime("%Y-%m-%d %H:%M:%S")} if verified else {})
                if key:
                    encrypted = pa.protect_api_key(key)
                    provider["api_key_ref"] = "file:" + os.path.join(DATA_DIR, "secrets", pid + ".key")
            except Exception:
                self._json({"ok": False, "reason": "API 探测或密钥加密失败，请检查连接和本机账户"}, 500)
                return
        elif previous is None:
            self._json({"ok": False, "reason": "新增 API 角色必须提供 Endpoint、Protocol 和 Model"}, 400)
            return
        with _providers_lock:
            fresh = providers_read()
            current = next((p for p in fresh["providers"] if p.get("id") == pid), None)
            if current != previous or clashes(fresh["providers"]):
                self._json({"ok": False, "reason": "角色配置已变更，请刷新后重试"}, 409)
                return
            key_path, old_key = None, None
            try:
                if encrypted is not None:
                    key_path = provider["api_key_ref"][5:]
                    os.makedirs(os.path.dirname(key_path), exist_ok=True)
                    if os.path.isfile(key_path):
                        with open(key_path, "rb") as stream:
                            old_key = stream.read()
                    with open(key_path + ".tmp", "w", encoding="utf-8") as stream:
                        stream.write(encrypted)
                    os.replace(key_path + ".tmp", key_path)
                fresh["providers"] = [provider if p.get("id") == pid else p for p in fresh["providers"]]
                if previous is None:
                    fresh["providers"].append(provider)
                providers_write(fresh)
            except (OSError, ValueError) as error:
                if key_path:
                    if old_key is None:
                        if os.path.exists(key_path):
                            os.remove(key_path)
                    else:
                        with open(key_path + ".tmp", "wb") as stream:
                            stream.write(old_key)
                        os.replace(key_path + ".tmp", key_path)
                self._json({"ok": False, "reason": str(error) if isinstance(error, ValueError) else "配置保存失败，请重试"}, 400 if isinstance(error, ValueError) else 500)
                return
        _routing._CACHE["key"] = None
        try:
            import client_probe
            with client_probe._CACHE_LOCK:
                client_probe._PROBE_CACHE.pop(pid, None)
        except ImportError:
            pass
        self._json({"ok": True, "provider_id": pid, "registered": previous is None,
                    "updated": previous is not None, "persisted": True, "status": status_val,
                    "detail": detail, "capabilities": provider.get("capabilities", {}),
                    "bootstrap": bootstrap.refresh()})

    def _remove_provider(self, body):
        pid = body.get("id") if isinstance(body, dict) else None
        if not isinstance(pid, str) or pid == "human":
            self._json({"ok": False, "reason": "id 无效或 human 受保护"}, 400)
            return
        with _providers_lock:
            data = providers_read()
            provider = next((p for p in data["providers"] if p.get("id") == pid), None)
            if provider is None:
                self._json({"ok": False, "reason": "provider 不存在"}, 404)
                return
            # Disable built-ins so their settings and restore action remain available.
            if pid in _routing.RESERVED_IDS:
                provider["enabled"] = False
            else:
                data["providers"] = [p for p in data["providers"] if p.get("id") != pid]
            providers_write(data)
        _routing._CACHE["key"] = None
        self._json({"ok": True, "id": pid, "disabled": pid in _routing.RESERVED_IDS,
                    "bootstrap": bootstrap.refresh()})

    def _upload_avatar(self, raw):
        import base64
        import io
        try:
            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                from email.parser import BytesParser
                from email.policy import default
                message = BytesParser(policy=default).parsebytes(
                    ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
                part = next(p for p in message.iter_parts() if p.get_filename())
                image_bytes = part.get_payload(decode=True)
            else:
                payload = json.loads(raw.decode("utf-8"))
                encoded = payload.get("data") or payload.get("image") or payload.get("base64") or ""
                image_bytes = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
            if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
                raise ValueError("size")
            from PIL import Image
            image = Image.open(io.BytesIO(image_bytes))
            if image.width * image.height > 16000000:
                raise ValueError("dimensions")
            image.thumbnail((512, 512))
            directory = os.path.join(DATA_DIR, "avatars")
            os.makedirs(directory, exist_ok=True)
            filename = "custom-" + uuid.uuid4().hex + ".png"
            image.convert("RGBA").save(os.path.join(directory, filename), format="PNG")
        except Exception:
            self._json({"ok": False, "reason": "请上传 5MB 内的有效图片"}, 400)
            return
        self._json({"ok": True, "url": "/avatar/" + filename, "avatar": "/avatar/" + filename})

    def _test_provider(self, body):
        """即时探测任意未保存或已保存的 API 配置（含真实模型推理 + 可选拉取 /models 列表）。"""
        if not isinstance(body, dict):
            self._json({"ok": False, "reason": "请求体必须为 JSON 对象"}, 400)
            return
        pid = str(body.get("provider_id") or body.get("id") or "").strip()
        endpoint = str(body.get("endpoint_url") or body.get("base_url") or "").strip()
        proto = _adapter_protocol(body.get("protocol") or "openaichat")
        model = str(body.get("model") or "").strip()
        key = str(body.get("api_key") or "").strip()
        if not key and pid:
            with _providers_lock:
                data = providers_read()
            prov = next((p for p in data.get("providers", []) if str(p.get("id")) == pid), None)
            if prov:
                key = _secret_from_ref(prov.get("api_key_ref"))
                if not endpoint:
                    endpoint = str(prov.get("endpoint_url") or "").strip()
                if not model:
                    model = str(prov.get("model") or "").strip()
                if not body.get("protocol"):
                    proto = _adapter_protocol(prov.get("protocol") or "openaichat")
        if proto not in ("openaichat", "anthropicmessages", "gemininative"):
            self._json({"ok": False, "reason": "不支持的协议格式：%s（请选择 OpenAI / Anthropic / Gemini 协议）" % proto}, 400)
            return
        try:
            parsed = urlparse(endpoint)
            valid = parsed.scheme in ("http", "https") and parsed.hostname and not (
                parsed.username or parsed.password or parsed.fragment)
            if parsed.query and any(bad in parsed.query.lower() for bad in ("key=", "api_key=", "token=", "secret=")):
                valid = False
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            self._json({"ok": False, "reason": "请填写合法的 HTTP/HTTPS API 端点 URL（禁止在 URL 中明文拼接 key）"}, 400)
            return
        if os.environ.get("HUB_PROBE_ALLOW_PRIVATE", "") != "1" and _host_is_internal(parsed.hostname):
            self._json({"ok": False, "status": "UNREACHABLE", "reason": "Endpoint 指向本机或内网受限地址，探测已拦截"}, 400)
            return
        norm_ep = _normalize_endpoint_url(endpoint, proto, model or "gpt-4o-mini")
        headers = _build_api_headers(proto, key, norm_ep)
        models_list = []
        if body.get("fetch_models", True) and proto == "openaichat":
            try:
                # 尝试顺带拉取 /models 候选列表（失败不阻断模型探针）
                m_path = (parsed.path or "").rstrip("/")
                for suf in ("/chat/completions", "/messages"):
                    if m_path.endswith(suf):
                        m_path = m_path[:-len(suf)]
                if not m_path:
                    m_path = "/v1"
                models_url = "%s://%s%s/models" % (parsed.scheme, parsed.hostname + ((":%d" % parsed.port) if parsed.port else ""), m_path)
                req_m = _ureq.Request(models_url, headers=headers, method="GET")
                with _ureq.urlopen(req_m, timeout=2.5) as resp_m:
                    raw_m = json.loads(resp_m.read(262144).decode("utf-8", errors="ignore"))
                    items = raw_m.get("data") if isinstance(raw_m, dict) else (raw_m if isinstance(raw_m, list) else [])
                    if isinstance(items, list):
                        for item in items[:120]:
                            mid = item.get("id") if isinstance(item, dict) else str(item)
                            if mid and isinstance(mid, str):
                                models_list.append(mid)
            except Exception:
                pass
        if not model:
            if models_list:
                self._json({"ok": True, "status": "MODELS_ONLY", "models": models_list,
                            "endpoint_url": norm_ep, "detail": "成功拉取 %d 个候选模型，请选择或填写要使用的模型 ID" % len(models_list)})
                return
            self._json({"ok": False, "reason": "请填写要测试的模型标识 (Model ID)"}, 400)
            return
        try:
            import provider_adapter as _pa
            res = _pa.probe_capability(proto, norm_ep, model, headers=headers, timeout=3.0)
        except Exception as exc:
            self._json({"ok": False, "reason": "探针调用异常：%s" % str(exc)[:140]}, 500)
            return
        status_val = getattr(res.status, "value", None) or str(res.status)
        verified = status_val in ("NATIVETOOLS", "NONATIVETOOLS")
        detail = str(getattr(res, "detail", ""))
        cap_label = "支持原生 Function Calling 工具调用" if status_val == "NATIVETOOLS" else (
            "对话连通正常（使用 XML 工具回退模式）" if status_val == "NONATIVETOOLS" else detail)
        self._json({
            "ok": verified,
            "status": status_val,
            "reason": "" if verified else detail,
            "detail": cap_label if verified else detail,
            "raw_detail": detail,
            "elapsed_ms": int(getattr(res, "elapsed", 0) * 1000),
            "endpoint_url": norm_ep,
            "protocol": proto,
            "model": model,
            "models": models_list,
        })

    def _probe_provider(self, body, *, registered=False):
        pid = str(body.get("provider_id") or body.get("id") or "").strip()
        if not pid:
            self._json({"ok": False, "reason": "provider_id 必填"}, 400)
            return
        with _providers_lock:
            data = providers_read()
        prov = next((p for p in data.get("providers", []) if str(p.get("id")) == pid), None)
        if not prov:
            self._json({"ok": False, "reason": "provider 不存在：%s" % pid}, 404)
            return
        endpoint = str(prov.get("endpoint_url") or prov.get("base_url") or "").strip()
        if not endpoint:
            self._json({"ok": False, "reason": "provider 缺少 endpoint_url，无法探针"}, 400)
            return
        try:
            _host = urlparse(endpoint).hostname or ""
        except ValueError:
            _host = ""
        if os.environ.get("HUB_PROBE_ALLOW_PRIVATE", "") != "1" and (not _host or _host_is_internal(_host)):
            self._json({"ok": False,
                        "reason": "endpoint 指向回环/内网地址，探针已拒绝（确需探测本机服务请设 HUB_PROBE_ALLOW_PRIVATE=1）"}, 400)
            return
        proto = _adapter_protocol(prov.get("protocol"))
        if proto not in ("openaichat", "anthropicmessages", "gemininative"):
            self._json({"ok": False,
                        "reason": "protocol=%s 不支持探针（只支持 openai_chat / anthropic_messages / gemini_native）"
                                  % prov.get("protocol")}, 400)
            return
        try:
            import provider_adapter as _pa
        except Exception as exc:
            self._json({"ok": False, "reason": "provider_adapter 不可用：%s" % str(exc)[:120]}, 500)
            return
        key = _secret_from_ref(prov.get("api_key_ref"))
        endpoint = _normalize_endpoint_url(endpoint, proto, str(prov.get("model") or ""))
        headers = _build_api_headers(proto, key, endpoint)
        try:
            res = _pa.probe_capability(proto, endpoint, str(prov.get("model") or ""),
                                       headers=headers, timeout=3.0)
        except Exception as exc:
            self._json({"ok": False, "reason": "探针异常：%s" % str(exc)[:120]}, 500)
            return
        status_val = getattr(res.status, "value", None) or str(res.status)
        verified = status_val in ("NATIVETOOLS", "NONATIVETOOLS")
        persisted = False
        note = "" if verified else "未验证/网络故障：按规则不回写、不降级"
        if verified and bool(body.get("persist", True)):
            with _providers_lock:
                fresh = providers_read()
                current = next((p for p in fresh.get("providers", []) if str(p.get("id")) == pid), None)
                identity = ("endpoint_url", "base_url", "protocol", "model", "api_key_ref")
                if current is not None and all(current.get(k) == prov.get(k) for k in identity):
                    caps = dict(current.get("capabilities") or {})
                    caps.update({"native_tools": status_val == "NATIVETOOLS",
                                 "xml_react": status_val == "NONATIVETOOLS",
                                 "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "probe_detail": str(getattr(res, "detail", ""))[:80]})
                    current["capabilities"] = caps
                    current["probe_status"] = status_val
                    current["probe_detail"] = str(getattr(res, "detail", ""))
                    providers_write(fresh)
                    persisted = True
                else:
                    note = "provider 已变更或删除，探针结果未回写，请重新探测"
        self._json({"ok": verified, "reason": "" if verified else str(getattr(res, "detail", "探测失败")),
                    "registered": registered, "provider_id": pid, "status": status_val,
                    "elapsed_ms": int(getattr(res, "elapsed", 0) * 1000),
                    "detail": str(getattr(res, "detail", ""))[:120],
                    "persisted": persisted,
                    "note": note})
        return
    def _netease_proxy(self, u):
        if not _netease_ensure():
            self._json({"ok": False, "reason": "网易云桥未启动"}, 502)
            return
        target = NETEASE_BRIDGE + u.path[len("/api/netease"):]
        if u.query:
            target += "?" + u.query
        try:
            req = _ureq.Request(target, method="GET")
            with _ureq.urlopen(req, timeout=30) as r:
                self._json(json.loads(r.read().decode("utf-8")))
        except Exception as exc:
            self._json({"ok": False, "reason": str(exc)[:160]}, 502)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/admin/prepare-reload":
            # 纯只读幂等查询：汇报当前排空状态与忙碌智能体，无任何写操作副作用
            running_agents = []
            now_ts = time.time()
            with _activity_lock:
                for ag_id, st in _activity.items():
                    if st.get("state") == "running":
                        # 如果没有 hub 内部排队/在途任务，或者超过 60 秒无心跳，不阻断 reload
                        has_hub_task = bool(_agent_current_tasks.get(ag_id))
                        if not has_hub_task or now_ts - (st.get("updated") or 0) > 60:
                            st["state"] = "idle"
                        else:
                            running_agents.append(ag_id)
            self._json({
                "ok": True,
                "draining": _DRAINING,
                "ready_to_exit": len(running_agents) == 0,
                "active_turns": len(running_agents),
                "running_agents": running_agents
            })
            return
        if u.path in ("/api/clients/status", "/api/client_status"):
            try:
                import client_probe as _cp
                statuses = {}
                for cid in ("dsh", "ag", "codex", "doubao", "workbuddy"):
                    try:
                        statuses[cid] = _cp.get_client_status(cid, force_refresh=False)
                    except Exception as err:
                        statuses[cid] = {"runtime_status": "offline", "error": str(err)}
                self._json({"ok": True, "clients": statuses})
            except Exception as e:
                self._json({"ok": False, "reason": str(e)}, 500)
            return
        if u.path == "/api/broadcast_mode":
            self._json({"ok": True, **broadcast_state()})
            return
        if u.path.startswith("/api/netease/"):
            self._netease_proxy(u)
            return
        if u.path == "/api/brainstorms":
            try:
                params = parse_qs(u.query)
                offset = max(0, int(params.get("offset", ["0"])[0]))
                limit = max(1, min(100, int(params.get("limit", ["20"])[0])))
                self._json(_brainstorms.page(offset, limit))
            except ValueError:
                self._json({"error": "invalid pagination"}, 400)
            return
        if u.path == "/":
            p = os.path.join(DATA_DIR, "index.html")
            with open(p, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Set-Cookie",
                             "conflux_admin=%s; HttpOnly; SameSite=Strict; Path=/" % _admin_token())
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path in ("/sentence-backbone.js", "/sentence_backbone.js"):
            p = os.path.join(DATA_DIR, "sentence-backbone.js")
            if not os.path.exists(p):
                p = cfg.root_path("cooperation/deliverables/sentence-backbone/sentence-backbone.js")
            with open(p, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path.startswith(("/img/", "/files/")):
            _file_store.serve(self, u.path)
            return
        if u.path.startswith("/wallpapers/"):
            w_name = os.path.basename(unquote(u.path[len("/wallpapers/"):]))
            wp = os.path.join(DATA_DIR, "wallpapers", w_name)
            if not os.path.isfile(wp):
                wp = os.path.join(r"D:\cooperation", w_name)
            if os.path.isfile(wp):
                with open(wp, "rb") as f:
                    wb_data = f.read()
                self.send_response(200)
                self.send_header("Cache-Control", "public, max-age=86400")
                w_ext = w_name.lower().rsplit(".", 1)[-1]
                ct = "image/webp" if w_ext == "webp" else ("image/png" if w_ext == "png" else "image/jpeg")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(wb_data)))
                self.end_headers()
                self.wfile.write(wb_data)
                return

        if u.path in ("/bg", "/theme-bg.png", "/theme-bg.webp"):
            for name in ("theme-bg.webp", "theme-bg.png", "bg.png", "bg.jpg", "bg.webp"):
                p = os.path.join(DATA_DIR, name)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Type", "image/png" if name.endswith(".png") else ("image/webp" if name.endswith(".webp") else "image/jpeg"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self._json({"error": "no bg"}, 404)
            return
        if u.path.startswith("/music/"):
            name = os.path.basename(unquote(u.path[len("/music/"):]))
            p = os.path.join(cfg.WORKSPACE, name)
            if not name.lower().endswith((".mp3", ".flac", ".wav", ".m4a", ".ogg")) or not os.path.isfile(p):
                self._json({"error": "no music"}, 404)
                return
            size = os.path.getsize(p)
            start, end = 0, size - 1
            range_header = self.headers.get("Range", "")
            if range_header.startswith("bytes="):
                spec = range_header[6:].split(",", 1)[0]
                left, _, right = spec.partition("-")
                try:
                    if left:
                        start = int(left)
                    if right:
                        end = int(right)
                    else:
                        end = size - 1
                    if start < 0 or start > end or end >= size:
                        raise ValueError
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            else:
                self.send_response(200)
            length = end - start + 1
            content_types = {
                ".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav",
                ".m4a": "audio/mp4", ".ogg": "audio/ogg",
            }
            self.send_header("Content-Type", content_types.get(os.path.splitext(name)[1].lower(), "application/octet-stream"))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        if u.path == "/api/plugins/registry":
            self._json({"ok": True, "plugins": bootstrap.plugin_registry()})
            return
        if u.path == "/api/bootstrap":
            try:
                agent = parse_qs(u.query).get("id", [""])[0]
                self._json({"ok": True, **bootstrap.snapshot(agent)} if agent else bootstrap.refresh())
            except ValueError as error:
                self._json({"ok": False, "reason": str(error)}, 400)
            return
        if u.path in ("/api/providers", "/api/agents/list"):
            with _providers_lock:
                data = providers_read()
            out = []
            try:
                import client_probe as _cp
                _probes = _cp.probe_all_clients()
            except Exception:
                _probes = {}
            for p in data.get("providers", []):
                item = {k: v for k, v in p.items() if k not in ("api_key_ref", "api_key")}
                item["has_key_ref"] = bool(p.get("api_key_ref"))
                item["probe_ready"] = bool(p.get("endpoint_url")) and _adapter_protocol(p.get("protocol")) in (
                    "openaichat", "anthropicmessages", "gemininative")
                pid = p.get("id")
                if pid in _probes:
                    item["runtime_status"] = _probes[pid].get("runtime_status", "offline")
                    item["desktop_online"] = (_probes[pid].get("runtime_status") == "desktop_online")
                elif p.get("protocol") == "human":
                    item["runtime_status"] = "desktop_online"
                elif pid in ("arch", "mimo") or p.get("mode") == "api":
                    item["runtime_status"] = "api_online" if api_provider_ready(p) else "offline"
                else:
                    item["runtime_status"] = "api_online" if item.get("probe_ready") or item.get("has_key_ref") else "offline"
                if p.get("probe_status") in ("AUTH_ERROR", "UNREACHABLE") and item["runtime_status"] != "desktop_online":
                    item["runtime_status"] = "offline"
                    item["detail"] = p.get("probe_detail", "API 未连通")
                if p.get("enabled") is False:
                    item["runtime_status"] = "offline"
                    item["desktop_online"] = False
                out.append(item)
            self._json({"ok": True, "providers": out, "agent_enabled": agent_enabled_map(), "agents": [a for a in out if a.get("enabled") is not False]})
            return
        if u.path == "/api/music/cmd":
            params = parse_qs(u.query)
            try:
                since = int(params.get("since", ["0"])[0])
            except ValueError:
                since = 0
            with _music_lock:
                seq = _MUSIC_PLAYER["seq"]
                cmd = dict(_MUSIC_PLAYER["cmd"]) if _MUSIC_PLAYER["cmd"] else None
            self._json({"ok": True, "seq": seq, "cmd": cmd if (cmd and seq > since) else None})
            return
        if u.path == "/api/music/state":
            with _music_lock:
                st = dict(_MUSIC_PLAYER["state"])
            self._json({"ok": True, "state": st})
            return
        if u.path == "/api/music":
            music_dir = cfg.WORKSPACE
            allowed = (".mp3", ".flac", ".wav", ".m4a", ".ogg")
            try:
                songs = [
                    {"name": entry.name, "size": entry.stat().st_size, "mtime": entry.stat().st_mtime}
                    for entry in os.scandir(music_dir)
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in allowed
                ]
                songs.sort(key=lambda song: song["name"].casefold())
            except OSError:
                songs = []
            self._json({"songs": songs})
            return
        if u.path == "/api/avatars":
            directory = os.path.join(DATA_DIR, "avatars")
            images = sorted("/avatar/" + name for name in os.listdir(directory)
                            if re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)", name, re.I)
                            and os.path.isfile(os.path.join(directory, name))) if os.path.isdir(directory) else []
            self._json({"ok": True, "avatars": images})
            return
        if u.path.startswith("/avatar/"):
            name = os.path.basename(u.path)
            for ext in (".jpg", ".png", ".jpeg", ".webp"):
                p = os.path.join(DATA_DIR, "avatars", name.rsplit(".", 1)[0] + ext)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/" + ext.lstrip(".").replace("jpg", "jpeg"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self._json({"error": "no avatar"}, 404)
            return
        if u.path in ("/manifest.json", "/sw.js"):
            fname = u.path.lstrip("/")
            fp = os.path.join(DATA_DIR, fname)
            try:
                with open(fp, "rb") as fh:
                    body = fh.read()
                ctype = "application/json" if fname.endswith(".json") else "application/javascript"
                self.send_response(200)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self._json({"error": "not found"}, 404)
            return

        if u.path.startswith("/icon-") and u.path.endswith(".png"):
            fname = os.path.basename(u.path)
            fp = os.path.join(DATA_DIR, fname)
            try:
                with open(fp, "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self._json({"error": "not found"}, 404)
            return

        if u.path == "/api/agents/list":
            # 动态名单：前端不再写死成员，全从 providers.json 派生（发布版必需）
            provs = _routing.load_providers()
            with _activity_lock:
                act_copy = {k: dict(v) for k, v in _activity.items()}
            items = []
            for p in provs:
                a = act_copy.get(p["id"], {})
                phase = a.get("phase")
                if a.get("state") in ("timeout", "error"):
                    st = a["state"]
                elif a.get("state") == "running" or phase in ("streaming", "connected_waiting_first_byte"):
                    st = "running"
                elif phase == "queued":
                    st = "queued"
                else:
                    st = "idle"
                items.append({
                    "id": p["id"],
                    "display_name": p.get("display_name") or p["id"],
                    "aliases": p.get("aliases") or [],
                    "role": p.get("role") or "",
                    "state": st,
                    "task": (a.get("task") or "")[:80],
                })
            self._json({"ok": True, "agents": items})
            return
        if u.path == "/api/status":
            sync_all_agents_activity()
            now = time.time()
            with _activity_lock:
                activity_copy = {k: dict(v) for k, v in _activity.items()}
            health = health_snapshot()
            agents = {}
            identities = {p["id"]: p for p in _routing.load_providers()}
            for name in display_agents():
                act = activity_copy.get(name, {})
                h = health.get(name, {"channel_ok": True, "detail": ""})
                if act.get("state") in ("timeout", "error"):
                    state = act["state"]
                elif not h["channel_ok"]:
                    state = "down"
                elif act.get("phase") in ("queued", "connected_waiting_first_byte", "streaming"):
                    state = "running" if act.get("phase") != "queued" else "queued"
                elif act.get("state") == "running":
                    state = "running"
                else:
                    state = "idle"
                _raw = act.get("task") or ""
                _m = re.search(r"【炫炫】\s*(.+?)(?:\n\s*（时间|$)", _raw, re.S)
                _brief = (_m.group(1) if _m else _raw)
                _brief = re.sub(r"（时间[^）]*）", " ", _brief)
                _brief = re.sub(r"【[^】]*】", " ", _brief)
                _brief = re.sub(r"-\s*\[[^\]]*\]\s*", " ", _brief)
                _brief = re.sub(r"\s+", " ", _brief).strip()
                _brief = re.sub(r"【输出纪律】.*", "", _brief).strip()
                if not _brief and state == "idle":
                    _brief = "待命"
                _phase = act.get("phase") or ("running" if state == "running" else "idle")
                if state == "idle" and _phase == "queued":
                    _phase = "idle"
                _age = round(time.time() - (act.get("updated") or 0), 1) if act.get("updated") else None
                _tr = track_state(name)
                # 自动收敛防卡死：若已无当前在途任务，且超过 30 秒无任何新动作，且无活跃线程，自动回归 idle
                has_live_task = bool(_agent_current_tasks.get(name))
                if state == "running" and not has_live_task and _age and _age > 30 and _tr.get("state") != "running":
                    state = "idle"
                    _phase = "completed"
                    act["state"] = "idle"
                    act["phase"] = "completed"
                    with _activity_lock:
                        if name in _activity:
                            _activity[name]["state"] = "idle"
                            _activity[name]["phase"] = "completed"
                if _tr.get("state") in ("running", "no_signal", "timeout", "error"):
                    state = _tr["state"]
                    _phase = _tr.get("verdict") or "running"
                elif state not in ("timeout", "error") and _tr.get("verdict") in ("done", "thread_dead"):
                    state = "idle"
                    _phase = "idle"
                cur_t = _agent_current_tasks.get(name)
                cur_task_payload = None
                if state == "running" and cur_t:
                    cur_task_payload = {
                        "sender": cur_t.get("sender") or "human",
                        "sender_label": cur_t.get("sender_label") or "炫炫",
                        "text": cur_t.get("text") or "",
                        "ts": cur_t.get("ts") or 0
                    }
                agents[name] = {"id": name,
                                "name": identities.get(name, {}).get("display_name") or AGENT_LABELS.get(name, name),
                                "display_name": identities.get(name, {}).get("display_name") or name,
                                "color": identities.get(name, {}).get("name_color") or identities.get(name, {}).get("color", ""),
                                "name_color": identities.get(name, {}).get("name_color") or identities.get(name, {}).get("color", ""),
                                "bubble_color": identities.get(name, {}).get("bubble_color", ""),
                                "avatar": identities.get(name, {}).get("avatar", ""),
                                "system_prompt": identities.get(name, {}).get("system_prompt", ""),
                                "tag": AGENT_TAGS.get(name, ""),
                                "role": identities.get(name, {}).get("role") or AGENT_ROLES.get(name, ""),
                                "state": state,
                                "queued_count": agent_queue_size(name),
                                "queue_items": queue_preview(name),
                                "current_task": cur_task_payload,
                                "task_full": _raw[:2000],
                                "task_raw": _raw[:8000],
                                "phase": _phase,
                                "timing": act.get("timing") or {},
                                "track": track_state(name),
                                "updated_at": act.get("updated") or 0,
                                "age": _age,
                                "task": _brief[:100],
                                "since": act.get("started") or 0,
                                "updated": act.get("updated") or 0,
                                "last_seen": act.get("updated") or 0,
                                "readiness_state": h.get("readiness_state"),
                                "process_running": h.get("process_running"),
                                "authenticated": h.get("authenticated"),
                                "channel_ok": h["channel_ok"],
                                "detail": h.get("detail", ""),
                                "runtime_status": h.get("runtime_status", "desktop_online" if h["channel_ok"] else "offline")}
            _all = read_tasks()
            _tasks, _archived = split_tasks(_all)
            _tasks = list(reversed(_tasks))[:12]
            # 当前任务里的分工话语优先：客户端读 agents[].task 时不该看到原始转发文本
            _running_tasks = [t for t in _tasks if t.get("status") in ("executing", "awaiting_gpt_review", "rework") and t.get("roles")]
            if _running_tasks:
                _roles = {r.get("agent"): r.get("work") for r in _running_tasks[-1]["roles"]
                          if isinstance(r, dict)}
                for _a, _w in _roles.items():
                    if _a in agents and _w:
                        agents[_a]["task"] = _w
            try:
                with open(HEARTBEAT_FILE, encoding="utf-8") as _hf:
                    _hb = json.load(_hf)
                _hb["age"] = round(time.time() - _hb.get("ts", 0), 1)
            except Exception:
                _hb = {"missing": True}
            self._json({"busy": {k: round(now - v, 1) for k, v in _busy.items()},
                        "broadcast_mode": broadcast_state()["enabled"],
                        "heartbeat": _hb,
                "demo_mode": _DEMO_MODE,
                        "quotas": quotas(),
                        "probes": local_probes(),
                        "activity": activity_copy,
                        "agents": agents, "agent_enabled": agent_enabled_map(),
                        "health": health,
                        "tasks": _tasks,
                        "archived": list(reversed(_archived))[:20],
                        "archived_total": len(_archived)})
            return
        if u.path == "/api/inbox":
            self._json({"messages": inbox_list()})
            return

        if u.path == "/api/health":
            self._json({"health": health_snapshot(), "ts": time.time()})
            return
        if u.path in ("/api/pause", "/api/resume"):
            try:
                if u.path == "/api/pause":
                    open(PAUSE_FILE, "w").write("paused")
                else:
                    if os.path.exists(PAUSE_FILE):
                        os.remove(PAUSE_FILE)
                self._json({"ok": True, "paused": is_paused()})
            except Exception as e:
                self._json({"ok": False, "reason": str(e)[:60]})
            return

        if u.path == "/api/quota/refresh-ag":
            try:
                subprocess.Popen(["C:\\Program Files\\nodejs\\node.exe",
                                  cfg.root_path('data/mimo-scrape/ag_quota.js'), "--once"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._json({"ok": True, "note": "已触发一次 Antigravity 额度采样（会短暂打开设置面板）"})
            except Exception as e:
                self._json({"ok": False, "reason": str(e)[:80]})
            return

        if u.path == "/api/quota":
            q = quotas()
            self._json({"quotas": q, "cached": q.get("cached"), "ttl": QUOTA_TTL})
            return

        if u.path == "/api/projects":
            self._json(project_list())
            return

        if u.path in ("/api/tickets", "/api/tickets/kanban"):
            params = parse_qs(u.query)
            if u.path.endswith("/kanban") or params.get("view") == ["kanban"]:
                try:
                    result = ticket_kanban_list(params.get("limit", ["100"])[0])
                except (ValueError, TypeError):
                    self._json({"ok": False, "reason": "limit must be 1..500"}, 400)
                    return
                self._json(result)
            else:
                self._json({"tickets": ticket_list()})
            return

        if u.path == "/api/tasks":
            tasks = read_tasks()
            active, archived = split_tasks(tasks)
            self._json({"tasks": active, "active": active,
                        "archived": list(reversed(archived))[:30],
                        "archived_total": len(archived)})
            return
        if u.path == "/api/version":
            try:
                v = os.path.getmtime(os.path.join(DATA_DIR, "index.html"))
            except Exception:
                v = 0
            self._json({"v": v})
            return

        if u.path == "/api/tickets":
            try:
                lim = 30
                try:
                    lim = min(max(int(parse_qs(u.query).get("limit", ["30"])[0]), 1), 200)
                except Exception:
                    pass
                self._json({"tickets": ticket_list(lim)})
            except Exception as e:
                self._json({"error": str(e)[:120], "tickets": []}, 500)
            return

        if u.path == "/api/ticketflow/flow":
            self._json({"ok": True, "flow": _tickets.config(),
                        "stages": [{"key": k, "label": _ticket_flow.STAGE_LABEL[k]} for k in _ticket_flow.STAGE_KEYS]})
            return
        if u.path == "/api/ticketflow/tickets":
            self._json({"ok": True, "tickets": _tickets.list(100), "stalled": _tickets.stalled()})
            return
        if u.path == "/api/files":
            self._json({"ok": True, "files": _file_store.list_files(read_msgs(0))})
            return
        if u.path == "/api/messages":
            query = parse_qs(u.query)
            try:
                since = float(query.get("since", ["0"])[0])
            except (TypeError, ValueError):
                since = 0.0
            date_filter = query.get("date", [""])[0]
            sender = query.get("sender", [""])[0].strip().lower()
            q_keyword = query.get("q", [""])[0].strip()
            filtering = bool(sender or q_keyword or date_filter)
            try:
                limit = min(max(int(query.get("limit", ["0"])[0]), 0), 500)
            except (TypeError, ValueError):
                limit = 0
            if filtering and not limit:
                limit = 500
            if since <= 0 and not date_filter and not limit:
                limit = 200
            try:
                _msgs = read_msgs(since, limit=limit or None, date_filter=date_filter, public_only=True,
                                  sender=sender, q=q_keyword)
            except Exception as e:
                self._json({"messages": [], "error": str(e)[:120]}, 500)
                return
            self._json({"messages": _msgs, "deleted_ids": sorted(deleted_message_ids())})
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        global _DRAINING
        length = int(self.headers.get("Content-Length") or 0)
        if self.path == "/api/admin/prepare-reload":
            ok, reason = _admin_ok(self)
            if not ok:
                self._json({"ok": False, "reason": reason}, 403)
                return
            _DRAINING = True
            t_wait_start = time.time()
            while time.time() - t_wait_start < 25:
                running_agents = []
                with _activity_lock:
                    for ag_id, st in _activity.items():
                        if st.get("state") == "running":
                            running_agents.append(ag_id)
                if not running_agents:
                    break
                time.sleep(0.5)

            running_agents = []
            now_ts = time.time()
            with _activity_lock:
                for ag_id, st in _activity.items():
                    if st.get("state") == "running":
                        # 如果没有 hub 内部排队/在途任务，或者超过 60 秒无心跳，不阻断 reload
                        has_hub_task = bool(_agent_current_tasks.get(ag_id))
                        if not has_hub_task or now_ts - (st.get("updated") or 0) > 60:
                            st["state"] = "idle"
                        else:
                            running_agents.append(ag_id)

            self._json({
                "ok": True,
                "draining": True,
                "ready_to_exit": len(running_agents) == 0,
                "active_turns": len(running_agents),
                "running_agents": running_agents
            })
            return
        if self.path == "/api/admin/cancel-reload":
            ok, reason = _admin_ok(self)
            if not ok:
                self._json({"ok": False, "reason": reason}, 403)
                return
            _DRAINING = False
            self._json({
                "ok": True,
                "draining": False,
                "message": "reload cancelled, system restored to normal accepting"
            })
            return
        if self.path in ("/api/upload", "/api/attach", "/api/files/open") and not 0 < length <= ((FILE_MAX_BYTES + 2) // 3) * 4 + 65536:
            self.close_connection = True
            self._json({"ok": False, "error": "请求体过大或为空"}, 413)
            return
        if self.path in ("/api/avatars/upload", "/api/upload_avatar", "/api/avatar/upload"):
            ok, reason = _admin_ok(self)
            if not ok:
                self._json({"ok": False, "reason": reason}, 403)
                return
            if not 0 < length <= 8 * 1024 * 1024:
                self._json({"ok": False, "reason": "头像上传过大或为空"}, 400)
                return
            self._upload_avatar(self.rfile.read(length))
            return
        if self.path == "/api/stt":
            # 语音转文字：接受原始音频字节（audio/webm、audio/wav）或 JSON {audio: base64, ext}
            ctype = (self.headers.get("Content-Type") or "").lower()
            raw = self.rfile.read(length) if length else b""
            if not raw:
                self._json({"ok": False, "error": "空音频"}, 400)
                return
            if len(raw) > 30 * 1024 * 1024:
                self._json({"ok": False, "error": "音频超过 30 MB"}, 413)
                return
            ext = ".webm"
            if "json" in ctype:
                import base64 as _b64
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    raw = _b64.b64decode(str(payload.get("audio") or "").split(",")[-1])
                    ext = (payload.get("ext") or ".webm").strip().lower()
                    if not ext.startswith("."):
                        ext = "." + ext
                except Exception as exc:
                    self._json({"ok": False, "error": "音频解析失败：%s" % str(exc)[:80]}, 400)
                    return
            elif "wav" in ctype:
                ext = ".wav"
            elif "ogg" in ctype:
                ext = ".ogg"
            elif "mp4" in ctype or "m4a" in ctype:
                ext = ".m4a"
            token = uuid.uuid4().hex[:10]
            work = os.path.join(DATA_DIR, "stt")
            os.makedirs(work, exist_ok=True)
            src_path = os.path.join(work, token + ext)
            wav_path = os.path.join(work, token + ".16k.wav")
            out_base = os.path.join(work, token + ".out")
            txt_path = out_base + ".txt"
            try:
                with open(src_path, "wb") as f:
                    f.write(raw)
                conv = subprocess.run(
                    [FFMPEG, "-y", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
                    capture_output=True, timeout=60,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if not os.path.exists(wav_path):
                    self._json({"ok": False, "error": "音频转码失败：%s" % conv.stderr.decode("utf-8", "replace")[-160:]}, 500)
                    return
                run = subprocess.run(
                    [STT_PY, STT_SCRIPT, wav_path, out_base],
                    capture_output=True, timeout=300, cwd=os.path.dirname(STT_SCRIPT),
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                text = ""
                if os.path.exists(txt_path):
                    with open(txt_path, encoding="utf-8") as f:
                        text = f.read().strip()
                if run.returncode != 0 and not text:
                    self._json({"ok": False, "error": "转写失败：%s" % run.stderr.decode("utf-8", "replace")[-160:]}, 500)
                    return
                self._json({"ok": True, "text": text})
            except subprocess.TimeoutExpired:
                self._json({"ok": False, "error": "转写超时"}, 504)
            except Exception as exc:
                self._json({"ok": False, "error": "转写异常：%s" % str(exc)[:120]}, 500)
            finally:
                for p in (src_path, wav_path, txt_path):
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            try:
                bad = os.path.join(DATA_DIR, "bad-requests.bin")
                if os.path.exists(bad) and os.path.getsize(bad) > 1024 * 1024:
                    os.remove(bad)
                import hashlib
                with open(bad, "a", encoding="utf-8") as bf:
                    bf.write("%s len=%d sha256=%s\n" % (
                        time.strftime("%Y-%m-%d %H:%M:%S"), len(raw),
                        hashlib.sha256(raw).hexdigest()))
            except Exception:
                pass
            self._json({"error": "bad utf-8"}, 400)
            return
        except Exception:
            self._json({"error": "bad json"}, 400)
            return

        if self.path == "/api/broadcast_mode":
            ok, reason = _admin_ok(self)
            if not ok:
                self._json({"ok": False, "reason": reason}, 403)
                return
            try:
                state = set_broadcast_mode(body.get("enabled") if isinstance(body, dict) else None)
            except ValueError as exc:
                self._json({"ok": False, "reason": str(exc)}, 400)
                return
            self._json({"ok": True, **state})
            return

        if self.path == "/api/messages/delete":
            ok, reason = _admin_ok(self)
            if not ok:
                self._json({"ok": False, "reason": reason}, 403)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "reason": "需要消息 ID"}, 400)
                return
            try:
                found = delete_message(body.get("id"))
            except ValueError as exc:
                self._json({"ok": False, "reason": str(exc)}, 400)
                return
            except OSError:
                self._json({"ok": False, "reason": "消息删除保存失败，请重试"}, 500)
                return
            self._json({"ok": found, "id": body.get("id"),
                        "reason": "" if found else "消息不存在"}, 200 if found else 404)
            return

        if self.path == "/api/inbox/ack":
            self._json({"acked": inbox_ack(int(body.get("n") or 1000), body.get("ids"))})
            return

        if self.path == "/api/tasks":
            if not isinstance(body, dict) or not (body.get("tasks") or body.get("name")):
                self._json({"error": "需要 tasks 数组或 name 字段"}, 400)
                return
            items = body.get("tasks")
            if not isinstance(items, list):
                items = [body]
            with _tasks_lock:
                tasks = read_tasks()
                for it in items:
                    if not isinstance(it, dict) or not (it.get("id") or it.get("name")):
                        continue
                    it = dict(it)
                    if it.get("action") == "archive":
                        it["status"] = "archived"
                    if "status" in it and it["status"] not in ACTIVE_STATES + ("done", "accepted", "archived", "failed", "cancelled"):
                        self._json({"ok": False, "error": "invalid task status"}, 400)
                        return
                    it["updated"] = time.time()
                    if it.get("status") and it["status"] not in ACTIVE_STATES:
                        it["end"] = time.time()
                    elif it.get("status") in ACTIVE_STATES:
                        it["end"] = None
                    tid = it.get("id")
                    if tid:
                        for t in tasks:
                            if t.get("id") == tid:
                                t.update({k: v for k, v in it.items() if k != "id"})
                                break
                        else:
                            if not it.get("name"):
                                self._json({"ok": False, "error": "task not found"}, 404)
                                return
                            tasks.append({"id": tid, "start": time.time(), "end": None,
                                          "status": "running", "owner": "", "roles": [],
                                          "evidence": "", **it})
                    else:
                        tasks.append({"id": uuid.uuid4().hex[:8], "start": time.time(),
                                      "end": None, "status": "running", "owner": "",
                                      "roles": [], "evidence": "", **it})
                write_tasks(tasks)
            self._json({"ok": True, "tasks": read_tasks()})
            return

        if self.path in ("/api/ticket", "/api/tickets"):
            if not isinstance(body, dict):
                self._json({"ok": False, "reason": "object required"}, 400)
                return
            action = (body.get("action") or "").strip()
            if action == "create":
                result = ticket_create(body.get("title") or "未命名", body.get("goal") or "",
                                       body.get("who") or "dsh", bool(body.get("test")),
                                       body.get("request_id") if body.get("request_id") is not None else body.get("client_mid"))
                self._json(result, 409 if result.get("error") == "idempotency_conflict" else (200 if result.get("ok") else 400))
            elif action == "stage_complete":
                self._json(ticket_complete_stage(body), 200)
            elif action in ("finalize", "finalize_all"):
                self._json({"ok": True, "notice": "工单已全面实行四栏规约(name/goal/plan/delivery)，第5/6栏已废弃移除", "filled": 0})
            elif action == "delete":
                self._json(ticket_delete(body.get("id") or ""))
            elif action == "update":
                section = body.get("section") or "log"
                if section == "acceptance":
                    self._json({"ok": False, "reason": "验收必须走accept入口"}, 400)
                elif section == "status":
                    self._json(ticket_set_state(body.get("id") or "", body.get("text") or "", body.get("who") or "dsh"))
                else:
                    self._json(ticket_update(body.get("id") or "", section,
                                             body.get("text") or "", body.get("who") or "dsh"))
            elif action == "archive":
                self._json(ticket_archive(body.get("id") or ""))
            elif action == "accept":
                self._json(ticket_accept(body.get("id") or "", body.get("passed"),
                                         body.get("reason") or ""))
            elif action == "redispatch":
                self._json(ticket_redispatch(body.get("id") or ""))
            else:
                self._json({"ok": False, "reason": "action 必须是 create/update/accept/redispatch"})
            return

        if self.path == "/api/send":
            if (not isinstance(body, dict)
                    or any(not isinstance(body.get(k, ""), str) for k in ("from", "text", "client_mid"))
                    or not isinstance(body.get("to", []), list)
                    or any(not isinstance(t, str) for t in body.get("to", []))):
                self._json({"error": "bad send payload"}, 400)
                return
            sender = normalize_agent_id(body.get("from") or "")
            ALLOWED_SENDERS = tuple(_routing.sender_ids()) + ("narrator", "human", "watch")
            if sender not in ALLOWED_SENDERS:
                self._json({"error": "bad sender"}, 400)
                return
            if sender == "human":
                origin = (self.headers.get("Origin") or "")
                trusted = False
                if self.headers.get("X-Trusted-Human") == "1":
                    try:
                        import hmac as _hm, hashlib as _hl
                        key = open(os.path.join(DATA_DIR, "relay-inject.key"), encoding="utf-8").read().strip()
                        expect = _hm.new(key.encode(), raw, _hl.sha256).hexdigest()
                        trusted = _hm.compare_digest(expect, self.headers.get("X-Relay-Sig", ""))
                    except Exception:
                        trusted = False
                    if not trusted:
                        try:
                            with open(os.path.join(DATA_DIR, "send-audit.jsonl"), "a", encoding="utf-8") as af:
                                af.write(json.dumps({"ts": time.time(), "denied": "bad_signature",
                                                     "head": (body.get("text") or "")[:40]}, ensure_ascii=False) + "\n")
                        except Exception:
                            pass
                if not (trusted or origin in ("http://127.0.0.1:8777", "http://localhost:8777")):
                    try:
                        with open(os.path.join(DATA_DIR, "send-audit.jsonl"), "a", encoding="utf-8") as af:
                            af.write(json.dumps({"ts": time.time(), "denied": "human", "origin": origin,
                                                 "head": (body.get("text") or "")[:40]}, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    self._json({"error": "human sender not allowed"}, 403)
                    return
            try:
                to_list = normalize_recipients(body.get("to"))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            client_mid = (body.get("client_mid") or "").strip()
            text = (body.get("text") or "").strip()
            if client_mid:
                try:
                    for _m in read_msgs(limit=200):
                        if _m.get("client_mid") == client_mid and (_m.get("text") or "").strip() == text.strip():
                            self._json({"ok": True, "message": _m, "dedup": True})
                            return
                except Exception:
                    pass
            if not text:
                self._json({"error": "empty"}, 400)
                return
            if sender == "dsh" and _dsh_dup_recent(text):
                self._json({"ok": True, "duplicated": True, "note": "内容与本轮已发消息重复，已跳过"})
                return
            from ag_reply_guard import manual_reply_conflicts as _ag_manual_conflict
            if _ag_manual_conflict(sender, to_list, text, _agent_current_tasks.get('ag'),
                                   kind=body.get('kind') if body.get('kind') in ('image', 'file', 'conflux') else 'text',
                                   resolve_names=_routing.resolve_names,
                                   original_recipients=_active_reply_target.get('ag')):
                self._json({'ok': False, 'reason': '本轮最终回复会自动投递，请勿再用 /api/send 手动回复用户；请直接输出最终答复。',
                            'code': 'automatic_reply_owned'}, 409)
                return
            if dsh_manual_reply_conflicts(sender, client_mid, to_list, text):
                self._json({"ok": False, "reason": "本轮原收件人由最终回复自动投递，请勿手动POST；调用其他模型请仅指向新收件人"}, 409)
                return
            # 注入轮次精确回投：回复里带 turn:<id> 时按注入时登记的收件人投递，彻底摆脱 FIFO 顺序
            # 修复：若正文包含显式 >>> 指向，保留给 append_msg 解析，不得被轮次默认收件人覆盖
            has_arrow_in_body = isinstance(text, str) and bool(any(
                re.match(r"^>{2,}\s*([^\s:：]+)", l.strip()) and re.match(r"^>{2,}\s*([^\s:：]+)", l.strip()).group(1).lower() in _arrow_targets()
                for l in text.splitlines()
            ))
            reply_broadcast_generation = None
            if client_mid.startswith('turn:'):
                _raw_tid = client_mid[5:].split(':', 1)[0]
                _t = _TURN_ROUTES.get(_raw_tid)
                if _t is not None:
                    # 保留映射给同一 turn 的后续第二段回复使用，顺便清理超过 1800s 的过期项
                    _now_tr = time.time()
                    for _k in list(_TURN_ROUTES.keys()):
                        try:
                            if _now_tr - float(_TURN_ROUTES[_k][1]) > 1800:
                                _TURN_ROUTES.pop(_k, None)
                        except Exception:
                            pass
                    _save_turn_routes()
                # 正文里的 >>> 只决定「箭头块」的收件人，不该改掉正文本身的收件人。
                # 原来这里带着 not has_arrow_in_body 条件，导致凡带箭头块的回复，
                # 正文一律掉回默认 human（炫炫 09:44 发现的指向错位）。
                if _t:
                    to_list = list(_t[0])
                    if len(_t) > 2:
                        reply_broadcast_generation = _t[2]
                elif (not _t) and not has_arrow_in_body and sender == "dsh":
                    # turn id 对不上（插件自生成的 id 与 hub 的不一致）时，
                    # 用最近一次投给 dsh 的回复目标兜底，避免整条回复默认砸给炫炫
                    if time.time() - float(_DSH_LAST_ROUTE.get("ts") or 0) < 180:
                        _rt = _DSH_LAST_ROUTE.get("to")
                        if _rt:
                            to_list = list(_rt)
            kind = body.get("kind") if body.get("kind") in ("text", "image", "file", "conflux") else "text"
            _fm_raw = body.get("file_meta")
            _fm_keys = ("name", "ext", "size", "url", "abs_path", "source_path", "is_image")
            file_meta_send = ({k: _fm_raw.get(k) for k in _fm_keys if k in _fm_raw}
                              if isinstance(_fm_raw, dict) and kind in ("file", "image") else None)
            threading.Thread(target=write_send_audit, args=(sender, to_list, text), daemon=True).start()
            client_mid = (body.get("client_mid") or "").strip()[:96]
            with _lock:
                existing = next((m for m in reversed(read_msgs(0))
                                 if client_mid and m.get("client_mid") == client_mid and (m.get("text") or "").strip() == text.strip()), None)
                m = existing or append_msg(sender, text, kind=kind, to=to_list,
                                            defer_side_effects=True, client_mid=client_mid or None,
                                            broadcast_generation=reply_broadcast_generation,
                                            file_meta=file_meta_send)
            if sender in ("dsh", "codex", "ag", "mimo", "arch", "doubao", "workbuddy"):
                # 任何智能体回复落库 = 这一轮干完：立刻收掉 running，否则看板一直显示「工作中」
                try:
                    mark(sender, False)
                    track_end(sender, "done")
                except Exception:
                    pass
            self._json({"ok": True, "message": m})
            return

        if self.path == "/api/demo/reset":
            if not _DEMO_MODE:
                self._json({"ok": False, "reason": "非演示模式，拒绝重置"}, 400)
                return
            try:
                res = demo_seed(reset=True)
            except Exception as exc:
                self._json({"ok": False, "reason": str(exc)[:120]}, 500)
                return
            self._json(res)
            return
        if self.path == "/api/bootstrap/refresh":
            ok, reason = _admin_ok(self)
            if not ok:
                self._json({"ok": False, "reason": reason}, 403)
                return
            self._json(bootstrap.refresh())
            return
        if self.path == "/api/plugins/registry":
            ok, reason = _admin_ok(self)
            if not ok:
                self._json({"ok": False, "reason": reason}, 403)
                return
            try:
                result = bootstrap.write_plugins(body)
                # Plugin state changes also invalidate each client bootstrap version.
                result["bootstrap"] = bootstrap.refresh()
                self._json(result)
            except ValueError as error:
                self._json({"ok": False, "reason": str(error)}, 400)
            return
        if self.path in ("/api/providers", "/api/providers/update", "/api/providers/remove"):
            _ok, _why = _admin_ok(self)
            if not _ok:
                self._json({"ok": False, "reason": "管理接口鉴权失败：%s" % _why}, 403)
                return
            if self.path.endswith("/remove"):
                self._remove_provider(body)
            else:
                if self.path.endswith("/update") and isinstance(body, dict):
                    body["action"] = "update"
                self._register_provider(body)
            return
        if self.path in ("/api/clients/launch", "/api/providers/launch"):
            _ok, _why = _admin_ok(self)
            if not _ok:
                self._json({"ok": False, "reason": "管理接口鉴权失败：%s" % _why}, 403)
                return
            cid = str(body.get("client_id") or body.get("id") or "").strip().lower()
            if not cid:
                self._json({"ok": False, "reason": "client_id 为必填项"}, 400)
                return
            try:
                import client_probe as _cp
                ok_launch, reason = _cp.ensure_client_runtime(cid)
                st = _cp.get_client_status(cid, force_refresh=True)
                self._json({"ok": ok_launch, "reason": reason, "status": st})
            except Exception as e:
                self._json({"ok": False, "reason": str(e)}, 500)
            return
        if self.path == "/api/providers/probe":
            _ok, _why = _admin_ok(self)
            if not _ok:
                self._json({"ok": False, "reason": "管理接口鉴权失败：%s" % _why}, 403)
                return
            self._probe_provider(body)
            return
        if self.path in ("/api/providers/test", "/api/key/test"):
            _ok, _why = _admin_ok(self)
            if not _ok:
                self._json({"ok": False, "reason": "管理接口鉴权失败：%s" % _why}, 403)
                return
            self._test_provider(body)
            return
        if self.path == "/api/music/cmd":
            action = str(body.get("action") or "").strip().lower()
            if action not in ("play", "pause", "toggle", "next", "prev", "stop"):
                self._json({"ok": False, "reason": "action 必须是 play/pause/toggle/next/prev/stop"}, 400)
                return
            with _music_lock:
                _MUSIC_PLAYER["seq"] += 1
                _MUSIC_PLAYER["cmd"] = {
                    "seq": _MUSIC_PLAYER["seq"], "action": action,
                    "query": str(body.get("query") or "").strip()[:80],
                    "track_id": body.get("track_id"),
                    "from": str(body.get("from") or "dsh")[:16],
                    "ts": time.time(),
                }
                cmd = dict(_MUSIC_PLAYER["cmd"])
            self._json({"ok": True, "seq": cmd["seq"], "cmd": cmd})
            return
        if self.path == "/api/music/state":
            src = body.get("state") if isinstance(body.get("state"), dict) else body
            try:
                seq = int(src.get("seq") or body.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            with _music_lock:
                _MUSIC_PLAYER["state"] = {
                    "seq": seq,
                    "name": str(src.get("name") or "")[:120],
                    "artists": str(src.get("artists") or "")[:120],
                    "playing": bool(src.get("playing")),
                    "ts": time.time(),
                }
            self._json({"ok": True})
            return
        if self.path in ("/api/focus", "/api/window/focus", "/api/window/activate"):
            try:
                import ctypes
                user32 = ctypes.windll.user32
                target_hwnd = None

                def enum_cb(hwnd, lparam):
                    nonlocal target_hwnd
                    if user32.IsWindowVisible(hwnd):
                        l = user32.GetWindowTextLengthW(hwnd)
                        if l > 0:
                            b = ctypes.create_unicode_buffer(l + 1)
                            user32.GetWindowTextW(hwnd, b, l + 1)
                            t = b.value
                            if "Conflux" in t or "127.0.0.1:8777" in t or "8777" in t:
                                target_hwnd = hwnd
                                return False
                    return True

                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

                if target_hwnd:
                    # 突破 Windows 前台锁定限制
                    user32.keybd_event(0x12, 0, 0, 0)
                    user32.keybd_event(0x12, 0, 2, 0)
                    if user32.IsIconic(target_hwnd):
                        user32.ShowWindow(target_hwnd, 9)
                    else:
                        user32.ShowWindow(target_hwnd, 5)
                    user32.SetForegroundWindow(target_hwnd)
                    user32.BringWindowToTop(target_hwnd)
                else:
                    import webbrowser
                    webbrowser.open("http://127.0.0.1:8777/")
            except Exception:
                try:
                    import webbrowser
                    webbrowser.open("http://127.0.0.1:8777/")
                except Exception:
                    pass
            self._json({"ok": True})
            return
        if self.path in ("/api/ticketflow/flow", "/api/ticketflow/tickets", "/api/ticketflow/redispatch", "/api/ticketflow/stage-agents", "/api/ticketflow/accept"):
            _ok, _why = _admin_ok(self)
            if not _ok:
                self._json({"ok": False, "reason": "管理接口鉴权失败：%s" % _why}, 403)
                return
            if self.path == "/api/ticketflow/flow":
                self._json({"ok": True, "flow": _tickets.set_config(body)})
                return
            if self.path == "/api/ticketflow/tickets":
                goal = (body.get("goal") or "").strip()
                if not goal:
                    self._json({"ok": False, "error": "缺少 goal（目标效果）"}, 400)
                    return
                tk = _tickets.create(goal, body.get("title"), body.get("stage_agents"), body.get("supervisor"))
                agents = _tickets.agents_for(tk, tk["current"])
                try:
                    _dispatch_stage(tk, tk["current"], agents)
                except Exception:
                    pass
                self._json({"ok": True, "ticket": tk, "stage": tk["current"], "agents": agents, "agent_enabled": agent_enabled_map(),
                            "trigger": " ".join(">>> %s" % a for a in agents)})
                return
            if self.path == "/api/ticketflow/accept":
                _tid = (body.get("id") or "").strip()
                _raw_passed = body.get("passed")
                _reason = (body.get("reason") or "").strip()
                _res = _tickets.user_accept(_tid, _raw_passed, _reason)
                if not _res.get("ok"):
                    self._json({"ok": False, "error": _res.get("error") or "验收失败"}, _res.get("status_code") or 400)
                    return
                if _res.get("redispatch_stage"):
                    try:
                        _dispatch_stage(_res["ticket"], _res["redispatch_stage"], _res.get("agents"))
                    except Exception:
                        pass
                self._json({"ok": True, "ticket": _res.get("ticket")})
                return
            if self.path == "/api/ticketflow/stage-agents":
                _tid = (body.get("id") or "").strip()
                _stg = (body.get("stage") or "").strip()
                _ags = body.get("agents") or []
                _tk = _tickets.load(_tid)
                if not _tk or _stg not in _ticket_flow.STAGE_KEYS:
                    self._json({"ok": False, "error": "工单或环节不存在"}, 404)
                    return
                _tk.setdefault("stage_agents", {})[_stg] = [a for a in _ags if a]
                _tickets.save(_tk)
                _live = (_tk.get("current") == _stg)
                if _live:
                    _tk2 = _tickets.load(_tid)
                    _ags2 = _tickets._arm(_tk2, _stg)
                    _tickets.save(_tk2)
                    _dispatch_stage(_tk2, _stg, _ags2)
                self._json({"ok": True, "stage": _stg, "agents": _ags, "redispatched": _live})
                return
            res = _tickets.redispatch((body.get("id") or "").strip(), (body.get("stage") or "").strip() or None)
            try:
                _tkx = _tickets.load((body.get("id") or "").strip())
                if _tkx and res:
                    _dispatch_stage(_tkx, res["stage"], res.get("agents"))
            except Exception:
                pass
            self._json({"ok": bool(res), "result": res})
            return
        if self.path == "/api/agents/toggle":
            _ok, _why = _admin_ok(self)
            if not _ok:
                self._json({"ok": False, "reason": "管理接口鉴权失败：%s" % _why}, 403)
                return
            _name = (body.get("agent") or body.get("id") or "").strip()
            if not _name:
                self._json({"ok": False, "error": "缺少 agent"}, 400)
                return
            _env = bool(body.get("enabled"))
            _m = set_agent_enabled(_name, _env)
            try:
                action_str = "已启用" if _env else "已停用"
                append_msg("system", f"【智能体状态变更】智能体「{_name}」{action_str}。工单系统已同步更新可用名单，分工时将严禁向已停用智能体派单。", "system")
                if not _env and _tickets is not None:
                    for _tk_item in _tickets.list(100):
                        if _tk_item.get("status") == "running" and _name in (_tk_item.get("pending_assignees") or []):
                            _t_full = _tickets.load(_tk_item["id"])
                            if _t_full:
                                _p_list = [x for x in (_t_full.get("pending_assignees") or []) if _norm(x) != _norm(_name)]
                                _t_full["pending_assignees"] = _p_list
                                _t_full.setdefault("log", []).append({
                                    "ts": time.time(),
                                    "stage": _t_full.get("current"),
                                    "event": "auto_evict_disabled_assignee",
                                    "agent": _name,
                                    "msg": f"智能体 {_name} 被用户停用，工单系统已自动将其从等待交卷名单中移除，防止工单挂死"
                                })
                                if not _p_list and _t_full.get("current") == "plan":
                                    _nxt = _tickets._arm(_t_full, "delivery")
                                    _tickets.expect(_t_full["id"], "delivery", _nxt)
                                    _dispatch_stage(_t_full, "delivery", _nxt)
                                _tickets.save(_t_full)
            except Exception:
                pass
            self._json({"ok": True, "agent": _name, "enabled": _env, "agent_enabled": _m})
            return
        if self.path == "/api/files/open":
            peer = self.client_address[0]
            origin = self.headers.get("Origin")
            allowed_origins = {"http://127.0.0.1:8777", "http://localhost:8777"}
            if peer not in ("127.0.0.1", "::1"):
                self._json({"ok": False, "error": "只允许本机访问"}, 403)
                return
            if origin and origin.lower() not in allowed_origins and not _admin_ok(self)[0]:
                self._json({"ok": False, "error": "来源不被信任"}, 403)
                return
            action = body.get("action", "reveal")
            if action not in ("open", "reveal", "open_dir"):
                self._json({"ok": False, "error": "action 必须为 open、reveal 或 open_dir"}, 400)
                return
            try:
                raw_path = (body.get("path") or "").strip()
                # 1. 打开文件夹自身：空路径、open_dir 或指定客户端文件区
                if action == "open_dir" or not raw_path or raw_path.lower() in ("group-files", "group_dir", "files", "folder"):
                    target_dir = os.path.normpath(_file_store.group_dir)
                    os.makedirs(target_dir, exist_ok=True)
                    self._json(open_desktop_path(target_dir, "open_dir"))
                    return

                # 2. 路径解析：支持 /files/ 或 /img/ URL、相对文件名、绝对路径
                if raw_path.startswith(("/files/", "/img/")):
                    path = _file_store.resolve_url(raw_path)
                elif not os.path.isabs(raw_path):
                    cand = os.path.join(_file_store.group_dir, os.path.basename(raw_path))
                    if os.path.isfile(cand):
                        path = cand
                    else:
                        path = _file_store.validate(os.path.abspath(raw_path))
                else:
                    path = _file_store.validate(raw_path)

                self._json(open_desktop_path(os.path.normpath(path), action))
            except (ValueError, OSError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return
        if self.path in ("/api/upload", "/api/attach"):
            try:
                if self.path == "/api/upload":
                    meta = _file_store.upload(body)
                    # Preserve existing upload->send clients. New callers can publish in one request.
                    message = None
                    if body.get("send") is True:
                        message = append_msg(body.get("from") or "human", meta["url"],
                                             kind="image" if meta["is_image"] else "file",
                                             to=body.get("to"), file_meta=meta)
                else:
                    meta = _file_store.archive(path=body.get("path") or "")
                    message = append_msg(body.get("from") or "dsh", meta["url"],
                                         kind="image" if meta["is_image"] else "file",
                                         to=body.get("to"), file_meta=meta)
                if meta["is_image"]:
                    push_img_to_cloud(meta["abs_path"])
                self._json({"ok": True, "path": meta["url"], "name": meta["name"],
                            "size": meta["size"], "is_image": meta["is_image"],
                            "file_meta": meta, "message": message})
            except OverflowError as exc:
                self._json({"ok": False, "error": str(exc)}, 413)
            except (ValueError, OSError, TypeError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if self.path == "/api/qq-group":
            gid = body.get("group_id")
            text = (body.get("text") or "").strip()
            frm = body.get("from") or "dsh"
            if not gid or not text:
                self._json({"error": "need group_id and text"}, 400)
                return
            ok, err = send_qq_group(gid, text, frm)
            self._json({"ok": ok, "error": err})
            return

        if self.path == "/api/codex":
            text = (body.get("text") or "").strip()
            if text:
                threading.Thread(target=ask_codex, args=(text,), daemon=True).start()
            self._json({"ok": True})
            return

        self._json({"error": "not found"}, 404)


WATCH_ROOT = ROOT
WATCH_SKIP = ("data\\media", "logs", "data\\tts", "node_modules", "napcat", "stt", "data\\hub\\messages")
_watch_snapshot = {}


_ag_seen = None




def ag_watch():
    """Persist source-event identities; never reconstruct them from stripped UI text."""
    import antigravity_bridge as ag
    from ag_transcript_cursor import TranscriptCursor
    cursor = TranscriptCursor(os.path.join(DATA_DIR, "ag-transcript-cursor.json"))
    while True:
        try:
            path = ag.transcript_path()
            for event_id, text in cursor.pending(path):
                if text:
                    append_msg("ag", text)
                cursor.commit(path, event_id)
        except Exception as exc:
            print("ag_watch: %s" % exc, file=sys.stderr, flush=True)
        time.sleep(3)




def watch_loop():
    global _watch_snapshot
    time.sleep(20)
    while True:
        try:
            now = {}
            for base, dirs, files in os.walk(WATCH_ROOT):
                if any(sk in base for sk in WATCH_SKIP):
                    continue
                for fn in files:
                    if fn.endswith((".pyc", ".log", ".tmp")):
                        continue
                    fp = os.path.join(base, fn)
                    try:
                        now[fp] = os.path.getmtime(fp)
                    except OSError:
                        continue
            changed = [p for p, t in now.items()
                       if p in _watch_snapshot and t > _watch_snapshot[p]]
            added = [p for p in now if p not in _watch_snapshot]
            interesting = [p for p in changed if not p.endswith("messages.jsonl")]
            if _watch_snapshot and (interesting or added):
                names = [os.path.basename(p) for p in (interesting + added)[:6]]
                # append_msg("watch", "文件有变动：" + "、".join(names), "system") # 炫炫要求静默，不发系统提示
            _watch_snapshot = now
        except Exception:
            pass
        time.sleep(120)


import sys as _sys
if "dsh-runtimes" in _sys.executable.lower():
    print(cfg.expand('请用 ${CONFLUX_ROOT}\\stt\\venv\\Scripts\\python.exe 启动 hub，当前解释器缺依赖'))
    raise SystemExit(2)



# 【交付门槛】改完本文件必须跑 python scripts\\check_hub.py（py_compile + import hub）全绿才能报「已完成」。
# 教训：2026-11-04 main() 尾部 try/except 被顶到模块级，serve_forever 掉进 except 分支，hub 启动即退、8777 挂了约 4 分钟。
# 语法编译能过不代表运行不退，务必 import 一次（见 check_hub.py 结构闸）。
def _warm_client_connections():
    try:
        import client_probe
        client_probe.probe_all_clients(force_refresh=True)
    except Exception:
        pass



def _agent_asker(name):
    table = {"codex": ask_codex, "ag": ask_antigravity, "doubao": ask_doubao,
             "gpt": ask_codex, "gemini": ask_antigravity,
             "dsh": (lambda text: inject_qq(text, ["human"], "agent")),
             "deepseek": (lambda text: inject_qq(text, ["human"], "agent")),
             "workbuddy": ask_workbuddy, "mimo": ask_mimo, "arch": ask_arch}
    return table.get(name)


def _dispatch_stage(tk, stage, agents=None):
    agents = agents or _tickets.agents_for(tk, stage)
    agents = [a for a in agents if agent_is_enabled(a)]
    if not agents:
        return []
    online_agents = []
    disabled_agents = []
    try:
        en_map = agent_enabled_map()
        for p in _routing.load_providers():
            pid = p.get("id")
            if pid in _routing.sender_ids() and pid not in _routing.RESERVED_IDS:
                info = {"id": pid, "name": p.get("display_name") or pid}
                if en_map.get(pid, True):
                    online_agents.append(info)
                else:
                    disabled_agents.append(info)
    except Exception:
        pass
    prompt = _tickets.build_prompt(tk, stage, available_agents=online_agents, disabled_agents=disabled_agents)
    try:
        _tickets.expect(tk.get("id"), stage, agents)
    except Exception:
        pass
    sent = []
    for a in agents:
        fn = _agent_asker(a)
        if not fn:
            continue
        try:
            launch_agent(a, fn, prompt, sender="system", raw_text=prompt)
            sent.append(a)
        except Exception:
            pass
    return sent


def _ticket_watch():
    """工单巡检：每 3 秒扫一次——先处理超时重派，再吸收新消息推进工单。"""
    import time as _t
    last_ts = _t.time() - 5
    while True:
        _t.sleep(3)
        if _tickets is not None:
            try:
                for _st in _tickets.stalled():
                    _tk3 = _tickets.load(_st["ticket"])
                    _r3 = _tickets.redispatch(_st["ticket"], _st["stage"])
                    if _tk3 and _r3:
                        _dispatch_stage(_tk3, _st["stage"], _r3.get("agents"))
            except Exception:
                pass
        try:
            rows = read_msgs(last_ts)
        except Exception:
            rows = []
        for m in (rows or []):
            ts = m.get("ts") or 0
            if ts > last_ts:
                last_ts = ts
            if m.get("kind") != "text":
                continue

            try:
                res = _tickets.hook(m.get("sender") or "", m.get("text") or "")
            except Exception:
                res = None
            if not res:
                continue
            _tickets_events.append({"ts": _t.time(), **res})
            try:
                if res.get("notify_user"):
                    try:
                        import winsound
                        _wav = os.path.join(cfg.WORKSPACE, "ringcut.wav")
                        if not os.path.exists(_wav):
                            _wav = os.path.join(DATA_DIR, "ringcut.wav")
                        if os.path.exists(_wav):
                            winsound.PlaySound(_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    except Exception:
                        pass
                    append_msg(
                        "system",
                        "【工单 #%s · %s】第④栏「验收与交付」已完成，当前状态为「待用户验收」，请炫炫确认通过或驳回重做。"
                        % (res.get("ticket"), res.get("title") or ""),
                        "system",
                    )
                if res.get("summarize") and res.get("agent"):
                    _tks = _tickets.load(res["ticket"])
                    if _tks:
                        _stg = _tks.get("current")
                        _tickets.expect(res["ticket"], _stg, [res["agent"]])
                        _dispatch_stage(_tks, _stg, [res["agent"]])
                if (res.get("advanced") or res.get("rejected")) and res.get("next"):
                    _tk2 = _tickets.load(res["ticket"])
                    if _tk2:
                        _dispatch_stage(_tk2, res["next"], res.get("agents"))
            except Exception:
                pass
            try:
                with open(os.path.join(DATA_DIR, "ticket-events.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": _t.time(), **res}, ensure_ascii=False) + "\n")
            except Exception:
                pass


def main():
    try:
        threading.Thread(target=heartbeat_loop, daemon=True).start()
        threading.Thread(target=quota_loop, daemon=True).start()
        pass  # 监工线程已停用
        threading.Thread(target=track_loops, daemon=True).start()
    except Exception:
        pass
    ensure_dirs()
    try:
        if backfill_history is not None:
            backfill_history()   # 项目 5-1：历史工单只补缺项标记，保留原值
            if resume_naming is not None:
                threading.Thread(target=resume_naming, args=(_mimo_name_async,), daemon=True).start()   # 项目 5-1：恢复未完成的异步命名
    except Exception:
        pass
    # 进程级互斥：拿不到锁直接退出，根治「探测后启动」的竞态
    import msvcrt
    _lf = open(os.path.join(DATA_DIR, "hub.lock"), "a+")
    try:
        msvcrt.locking(_lf.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("另一个 hub 已持有锁，本次启动退出")
        return
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        _port = int(_os_stage.environ.get("CONFLUX_PORT", "8777"))
        srv = ThreadingHTTPServer(("127.0.0.1", _port), Handler)
    except OSError:
        print("8777 已被占用，本实例直接退出")
        return

    try:
        _compensate_inflight_turns()
    except Exception:
        pass
    threading.Thread(target=watch_loop, daemon=True).start()
    threading.Thread(target=ag_watch, daemon=True).start()
    threading.Thread(target=retry_loop, daemon=True).start()
    try:
        if backfill_history is not None:
            threading.Thread(target=backfill_history, daemon=True).start()   # 项目 5-1：历史工单缺项补标（幂等，只追加不覆盖）
            if resume_naming is not None:
                threading.Thread(target=resume_naming, args=(_mimo_name_async,), daemon=True).start()   # 项目 5-1：重启后恢复未完成的异步命名
    except Exception:
        pass
    # codex_watch 已停用：回复改由 headless exec 直接从 stdout 解析，避免双份

    try:
        _admin_token()
    except Exception:
        pass
    threading.Thread(target=_warm_agent_memory, daemon=True, name="memory-backfill").start()
    threading.Thread(target=_warm_client_connections, daemon=True, name="client-warmup").start()
    try:
        threading.Thread(target=_ticket_watch, daemon=True, name="ticket-watch").start()
    except Exception:
        pass
    try:
        print("协作台: http://127.0.0.1:8777")
    except Exception:
        pass
    while True:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            break
        except Exception:
            import time as _t
            _t.sleep(0.5)


if __name__ == "__main__":
    main()