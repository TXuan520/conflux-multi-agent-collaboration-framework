# -*- coding: utf-8 -*-
"""通用 agent 工具循环：同一套工具，两个后端（MiMo / Argon）。

用法：
    python agent_loop.py --provider mimo  --task "把 x 改成 y" --cwd ${CONFLUX_WORKSPACE}\\workbench
    python agent_loop.py --provider arch   --task "把这份决策单写进 ${CONFLUX_WORKSPACE}\\tickets\\T1.md" --cwd ${CONFLUX_WORKSPACE}

设计：
- 工具：list_dir / read_file / grep / write_file / patch_file / run_cmd
- 权限：根目录白名单（mimo=D:\\，arch=${CONFLUX_WORKSPACE}），密钥与 napcat 永久禁入
- 行为：先动手；改完自验；只有撞权限墙才报卡点
"""
import confluxconfig as cfg
import argparse
import hashlib
from agent_activity import reported
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import threading
import queue

MAX_ROUNDS = 80
MAX_FILE_WRITES = 8

def _request_json(request, opener=None):
    """Bound the entire attempt (including response reads), not just socket inactivity."""
    deadline = time.monotonic() + 160
    for attempt, limit in enumerate((60, 90)):
        budget = min(limit, deadline - time.monotonic())
        if budget <= 0:
            raise TimeoutError("API total window exhausted")
        result = queue.Queue(maxsize=1)
        def worker(result=result, budget=budget):
            try:
                open_request = opener.open if opener is not None else urllib.request.urlopen
                with open_request(request, timeout=budget) as response:
                    value = json.load(response)
                result.put((True, value))
            except BaseException as error:
                result.put((False, error))
        threading.Thread(target=worker, daemon=True).start()
        try:
            ok, value = result.get(timeout=budget)
        except queue.Empty:
            ok, value = False, TimeoutError("API attempt timed out")
        if ok:
            return value
        if isinstance(value, urllib.error.HTTPError):
            code = value.code
            value.close()
            raise RuntimeError("provider HTTP %s" % code) from None
        if not isinstance(value, Exception):
            raise value
        if attempt == 1:
            raise value


DATA_DIR = cfg.root_path('data')
# P0-4：工具输出瘦身（4000 字上限 + 哈希去重落盘 + 落盘前脱敏）
try:
    from tool_output import condense as _condense_tool_out
    from tool_output import sanitize as _sanitize_tool_out
except Exception:  # 模块缺失时退化为原行为，不能把工具环拖死
    _condense_tool_out = None
    _sanitize_tool_out = None
TOOL_OUTPUT_MAX = int(os.environ.get("TOOL_OUTPUT_MAX", "4000"))


def _fit_tool_output(text):
    """超长工具输出落盘只回头尾；脱敏在落盘前完成。"""
    if _condense_tool_out is None:
        return (text or "")[:12000]
    try:
        return _condense_tool_out(text, limit=TOOL_OUTPUT_MAX, label="工具输出")
    except Exception:
        return (text or "")[:12000]
# 7-1：切余额模式——用按量计费的 api 通道（sk- 凭证），订阅通道 token-plan 已停用
MIMO_KEY = os.path.join(DATA_DIR, "secrets", "mimo.key")
MIMO_BASE = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL = "mimo-v2.6-flash"   # 2026-11-04：pro 单轮常 400-600s 甚至超时，改 flash 提速（质量下降再切回）
LOG = cfg.root_path('data/hub/agent-loop.log')

DENY_DIRS = (cfg.root_path('data/hub/secrets'), cfg.root_path('data/secrets'), cfg.root_path('napcat'), "d:\\$recycle.bin")
# 只读保护：这些文件可以读，但任何模型都不许通过工具环改写（名单是路由的唯一事实源）
DENY_WRITE_FILES = (cfg.root_path('data/hub/providers.json'),
                    cfg.root_path('data/hub/proxy.txt'),
                    cfg.root_path('data/hub/admin.token'),
                    cfg.root_path('data/admin.token'))
DENY_READ_FILES = (cfg.root_path('data/hub/admin.token'),
                   cfg.root_path('data/admin.token'))
DENY_CMD = ("admin.token", "providers.json", "format", "diskpart", "shutdown", "restart-computer", "remove-item -recurse", "rm -rf",
            "del /f", "del /s", "rd /s", "rmdir /s", "start-process",
            "net user", "reg add", "reg delete", "bcdedit")

ROOTS = {name: (os.path.normcase(os.path.splitdrive(cfg.ROOT)[0] + os.sep),) for name in ("mimo", "arch")}

TOOLS = [
    {"type": "function", "function": {"name": "list_dir", "description": "列出目录内容",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "按字符分段读取文本文件，局部内容不要用于覆盖文件",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8000, "default": 4000}},
                    "required": ["path"]}}},
    {"type": "function", "function": {"name": "grep", "description": "在目录/文件里搜关键词",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}},
                    "required": ["path", "pattern"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "写入（覆盖）文本文件",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "patch_file", "description": "唯一匹配的 old→new 替换",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                     "new": {"type": "string"}},
                    "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {"name": "run_cmd", "description": "执行任务所需 PowerShell 命令，包括构建、验证与接口调用",
     "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
]


def log(obj):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass


BUILTIN_API_DEFAULTS = {
    "dsh": ("openaichat", "https://api.deepseek.com/chat/completions", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "codex": ("openaichat", "https://api.openai.com/v1/chat/completions", "gpt-6-luna", "OPENAI_API_KEY"),
    "ag": ("gemininative", "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
           "gemini-2.5-flash", "GEMINI_API_KEY"),
    "doubao": ("openaichat", "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
               "doubao-seed-1-6-250615", "ARK_API_KEY"),
    "workbuddy": ("openaichat", "https://api.hunyuan.cloud.tencent.com/v1/chat/completions",
                  "hunyuan-turbos-latest", "HUNYUAN_API_KEY"),
}


def custom_provider_config(provider_id, provider):
    """Resolve desktop fallback defaults while retaining explicit API registrations."""
    result = dict(provider)
    if provider_id not in BUILTIN_API_DEFAULTS:
        return result
    protocol, endpoint, model, standard_key = BUILTIN_API_DEFAULTS[provider_id]
    prefix = "CONFLUX_" + provider_id.upper()
    configured_protocol = re.sub(r"[^a-z]", "", str(result.get("protocol", "")).lower())
    if configured_protocol not in ("openaichat", "anthropicmessages", "gemininative"):
        result["protocol"] = protocol
    result["model"] = cfg.optional_env(prefix + "_MODEL") or result.get("model") or model
    result["endpoint_url"] = (cfg.optional_env(prefix + "_ENDPOINT") or result.get("endpoint_url")
                              or endpoint.replace("{model}", result["model"]))
    key_names = (prefix + "_KEY", provider_id.upper() + "_API_KEY", standard_key)
    key_name = next((name for name in key_names if cfg.optional_env(name)), None)
    if cfg.optional_env(prefix + "_KEY") or not result.get("api_key_ref"):
        result["api_key_ref"] = "env:" + key_name if key_name else ""
    if not result.get("capabilities"):
        result["capabilities"] = {"native_tools": False, "xml_react": True}
    return result


class Runner:
    def __init__(self, provider, task, cwd, rounds=40):
        self.provider = provider
        self.task = task
        self.cwd = os.path.abspath(cwd)
        self.rounds = rounds
        self.roots = ROOTS.get(provider, tuple(os.path.normcase(os.path.abspath(p)) + os.sep
                                             for p in (cfg.ROOT, cfg.WORKSPACE)))
        self._read_ranges = {}

    # ---------- 后端 ----------
    def chat(self, msgs, allow_tools=True):
        if self.provider in ("mimo", "arch"):
            try:
                with open(cfg.root_path("data/hub/providers.json"), encoding="utf-8") as stream:
                    registry = json.load(stream)
                entries = registry.get("providers", []) if isinstance(registry, dict) else registry
                provider = next((p for p in entries if p.get("id") == self.provider), {})
            except (OSError, ValueError):
                provider = {}
            if provider.get("enabled") is False:
                raise ValueError("provider 已禁用")
            if provider.get("endpoint_url"):
                return self.chat_custom(msgs, allow_tools)
        if self.provider not in ("mimo", "arch"):
            return self.chat_custom(msgs, allow_tools)
        if self.provider == "mimo":
            key = open(MIMO_KEY, encoding="utf-8").read().strip()
            body = {"model": MIMO_MODEL, "messages": msgs, "max_tokens": 4000}
            if allow_tools:
                body.update(tools=TOOLS, tool_choice="auto")
            req = urllib.request.Request(MIMO_BASE, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": "Bearer " + key})
            response = _request_json(req)
            if True:
                choice = response["choices"][0]
                message = choice["message"]
                log({"ts": time.time(), "event": "model_response", "provider": self.provider,
                     "finish_reason": choice.get("finish_reason"),
                     "content_chars": len(message.get("content") or ""),
                     "tool_calls": len(message.get("tool_calls") or []),
                     "usage": response.get("usage")})
                return message
        # arch = Vertex gemini-4-argon
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import vertex_client as vc
        contents = msgs_to_vertex(msgs)
        options = {"tools": [t["function"] for t in TOOLS]} if allow_tools else {}
        cand = vc.chat_contents(contents, **options,
                                model="gemini-4-argon", max_tokens=2000, timeout=180)
        parts = (cand.get("content") or {}).get("parts") or []
        calls = [p["functionCall"] for p in parts if p.get("functionCall")]
        text = "".join(p.get("text", "") for p in parts).strip()
        return {"content": text, "tool_calls": [
            {"id": "c%d" % i, "function": {"name": c.get("name"),
                                           "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
            for i, c in enumerate(calls)]}

    def chat_custom(self, msgs, allow_tools=True):
        from provider_adapter import (StandardMessage, ToolCallIntent, encode_request,
                                      decode_response, _NoRedirect)
        try:
            with open(cfg.root_path("data/hub/providers.json"), encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {"providers": []}
        providers = data.get("providers", []) if isinstance(data, dict) else data
        provider = next((p for p in providers if p.get("id") == self.provider), None)
        if provider is not None and provider.get("enabled") is False:
            raise ValueError("provider 不存在或已禁用")
        if provider is None and self.provider not in BUILTIN_API_DEFAULTS:
            raise ValueError("provider 不存在或已禁用")
        provider = custom_provider_config(self.provider, provider or {})
        protocol = re.sub(r"[^a-z]", "", str(provider.get("protocol", "")).lower())
        endpoint = provider.get("endpoint_url")
        model = provider.get("model")
        if not endpoint or not model:
            raise ValueError("provider 缺少 endpoint_url 或 model")
        capabilities = provider.get("capabilities") or {}
        native = capabilities.get("native_tools") is True
        fallback = bool(capabilities.get("xml_react", capabilities.get("xmlreact", False)))
        if not native and not fallback:
            raise ValueError("provider 能力未确认，请重跑探针")
        ref = provider.get("api_key_ref") or ""
        key = ""
        if ref.startswith("env:"):
            key = os.environ.get(ref[4:].strip(), "")
        elif ref.startswith("file:"):
            path = os.path.realpath(cfg.expand(ref[5:].strip()))
            secret_dirs = [os.path.normcase(os.path.realpath(cfg.root_path(d)))
                           for d in ("data/secrets", "data/hub/secrets")]
            if not any(os.path.commonpath([os.path.normcase(path), d]) == d for d in secret_dirs):
                raise ValueError("api_key_ref 超出 secrets 目录")
            from provider_adapter import read_secret_file
            key = read_secret_file(path)
        elif ref:
            raise ValueError("api_key_ref 格式无效")
        if ref and not key:
            raise ValueError("api_key_ref 未配置密钥")
        history, call_names = [], {}
        for msg in msgs:
            calls = [ToolCallIntent(c["id"], c["function"]["name"],
                     json.loads(c["function"]["arguments"])) for c in msg.get("tool_calls", [])]
            call_names.update((c.id, c.name) for c in calls)
            role = msg.get("role", "assistant")
            if role == "tool":
                parts = [{"type": "tool_result", "tool_call_id": msg["tool_call_id"],
                          "name": call_names.get(msg["tool_call_id"], "tool"),
                          "content": msg.get("content") or ""}]
                if not native:
                    role, parts = "user", [{"type": "text", "text": "工具结果\n" + (msg.get("content") or "")}]
            else:
                parts = [{"type": "text", "text": msg.get("content") or ""}]
            standard = StandardMessage(role, parts, calls if native else [])
            if native and msg.get("_native_parts") is not None:
                standard.native_protocol = protocol
                standard.native_parts = msg["_native_parts"]
            history.append(standard)
        if not native and allow_tools:
            history.insert(0, StandardMessage("system", [{"type": "text", "text":
                "可用工具 " + json.dumps([t["function"] for t in TOOLS], ensure_ascii=False) +
                '\n调用格式 <tool_call><function=工具名><parameter=参数名>值</parameter></function></tool_call>。每次调用后等待工具结果。'}]))
        body = encode_request(protocol, model, history,
                              [t["function"] for t in TOOLS] if native and allow_tools else [],
                              max_tokens=4000)
        headers = {"Content-Type": "application/json"}
        if protocol == "anthropicmessages":
            headers["anthropic-version"] = "2023-06-01"
        if key:
            low_ep = (endpoint or "").lower()
            if protocol == "anthropicmessages":
                headers["x-api-key"] = key
            elif protocol == "gemininative":
                headers["x-goog-api-key"] = key
            elif ".openai.azure.com" in low_ep or ".cognitiveservices.azure.com" in low_ep:
                headers["api-key"] = key
                headers["Authorization"] = "Bearer " + key
            else:
                headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            decoded = decode_response(protocol, _request_json(request, urllib.request.build_opener(_NoRedirect())))
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise RuntimeError("provider HTTP %s" % code) from None
        except Exception as exc:
            if re.search(r"provider HTTP \d{3}", str(exc)):
                raise
            raise RuntimeError("provider 请求失败（%s）" % type(exc).__name__) from None
        return {"role": "assistant", "content": "\n".join(p["text"] for p in decoded.parts),
                "tool_calls": [{"id": c.id, "type": "function", "function": {
                    "name": c.name, "arguments": json.dumps(c.arguments)}} for c in decoded.toolcalls],
                "_native_parts": decoded.native_parts}

    # ---------- 工具 ----------
    def _safe(self, p):
        full = os.path.abspath(os.path.join(self.cwd, p))
        low = full.lower()
        for d in DENY_DIRS:
            if low.startswith(os.path.normcase(d)):
                raise ValueError("拒绝访问受保护目录：%s" % p)
        for f in DENY_READ_FILES:
            if low == os.path.normcase(f):
                raise ValueError("拒绝访问受保护文件：%s" % p)
        if not any(low == r.rstrip(os.sep) or low.startswith(r) for r in self.roots):
            raise ValueError("越界：允许范围 %s" % ", ".join(self.roots))
        return full

    def _safe_write(self, p):
        full = self._safe(p)
        low = full.lower()
        for f in DENY_WRITE_FILES:
            if low == os.path.normcase(f) or low.endswith(os.sep + os.path.basename(f)) and low.startswith(os.path.normcase(cfg.root_path('data/hub'))):
                raise ValueError("拒绝改写受保护文件：%s" % p)
        return full

    def _verify(self, path):
        """按文件类型做一次语法自验；失败要记进未解决状态，最终出口会拦住「报成功」。"""
        low = (path or '').lower()
        fails = getattr(self, "_verify_failed", None)
        if fails is None:
            fails = self._verify_failed = []
        try:
            if low.endswith('.py'):
                r = subprocess.run([sys.executable, '-c',
                                    'import py_compile,sys;py_compile.compile(sys.argv[1],doraise=True)', path],
                                   capture_output=True, timeout=60, encoding='utf-8', errors='ignore',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if r.returncode:
                    fails.append('%s 语法错误' % path)
                    return '；自验失败：' + ((r.stderr or r.stdout or '')[-300:])
                return '；自验通过'
            if low.endswith(('.js', '.mjs')):
                r = subprocess.run(['node', '--check', path], capture_output=True, timeout=60,
                                   encoding='utf-8', errors='ignore',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if r.returncode:
                    fails.append('%s 语法错误' % path)
                    return '；自验失败：' + ((r.stderr or '')[-300:])
                return '；自验通过'
        except Exception as exc:
            return '；自验异常：%s' % str(exc)[:120]
        return ''

    def _note_written(self, path):
        self._wrote = True
        paths = getattr(self, "_written_paths", None)
        if paths is None:
            paths = self._written_paths = []
        if path and path not in paths:
            paths.append(path)

    def _file_hash(self, p):
        """内容哈希：只有内容真的变了才算改动，mtime/size 变化不算。"""
        h = hashlib.sha256()
        total = 0
        try:
            with open(p, 'rb') as f:
                for chunk in iter(lambda: f.read(262144), b''):
                    h.update(chunk)
                    total += len(chunk)
                    if total > 4 * 1024 * 1024:
                        break
        except OSError:
            return None
        return h.hexdigest()

    def _fs_snapshot(self, cmd=''):
        """记录命令可能触达的文件内容哈希：命令里显式路径 + 工作目录树（有上限，超限记 truncated）。"""
        snap = {}
        truncated = False
        for p in set(re.findall(r'[A-Za-z]:\\[^\s"\'`<>|]+', cmd or '')):
            if os.path.isfile(p):
                snap[p] = self._file_hash(p)
        try:
            for dp, dns, fns in os.walk(self.cwd):
                dns[:] = [d for d in dns if d.lower() not in ('node_modules', '.git', '__pycache__', 'venv')]
                for fn in fns:
                    fp = os.path.join(dp, fn)
                    if len(snap) > 4000:
                        truncated = True
                        break
                    snap[fp] = self._file_hash(fp)
                if truncated:
                    break
        except OSError:
            pass
        self._snap_truncated = truncated
        return snap

    def _fs_changed(self, before, cmd=''):
        """对比前后内容哈希；排除日志/缓存/临时产物，避免只读命令产生的日志被当成交付。"""
        after = self._fs_snapshot(cmd)
        ignore = re.compile(r'(\\logs?\\|\\cache\\|\\tmp\\|\\temp\\|__pycache__|\.pyc$|\.log$|\.tmp$|\.lock$)', re.I)
        changed = []
        for p, h in after.items():
            if before.get(p) == h:
                continue
            if ignore.search(p):
                continue
            changed.append(p)
        return changed

    def _deliver(self, text):
        text = (text or '').strip()
        result = text
        if getattr(self, "_modify", False):
            fails = getattr(self, "_verify_failed", [])
            if not getattr(self, "_wrote", False):
                if getattr(self, "_unverified", False):
                    result = "未完成：命令报成功但没有核实到文件变化（修改未核实）。" + (("原因：" + text[:120]) if text else "")
                else:
                    result = "未完成：整轮没有落盘任何文件。" + (("原因：" + text[:160]) if text else "")
            elif fails:
                result = "未完成：已改文件但自验没通过（%s）。已改：%s" % (
                    "; ".join(fails)[:200], "; ".join(getattr(self, "_written_paths", []))[:200])
            else:
                result = text or "已修改：%s" % "; ".join(getattr(self, "_written_paths", []))
        self._save_turn_memory(result)
        return result

    def _read_state(self, path, text):
        # Resolve aliases and invalidate coverage whenever the file content changes.
        key = os.path.normcase(os.path.realpath(path))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        state = self._read_ranges.get(key)
        if state is None or state["digest"] != digest:
            state = {"digest": digest, "ranges": []}
            self._read_ranges[key] = state
        return state

    def run_tool(self, name, a):
        record = getattr(self, "_activity_record", None)
        if record is not None:
            record["task"] = (name + ": " + str(a.get("path") or a.get("cmd") or ""))[:120]
            record["tool"] = name
            record["round"] = getattr(self, "_round", 0)
        try:
            if name == "list_dir":
                p = self._safe(a.get("path") or self.cwd)
                return "\n".join(("[dir] " if e.is_dir() else "[file] ") + e.name
                                 for e in sorted(os.scandir(p), key=lambda x: x.name)[:300]) or "(空)"
            if name == "read_file":
                offset, limit = a.get("offset", 0), a.get("limit", 4000)
                # XML tool-call fallback supplies parameter values as strings.
                if isinstance(offset, str) and offset.isdecimal():
                    offset = int(offset)
                if isinstance(limit, str) and limit.isdecimal():
                    limit = int(limit)
                if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
                    return "[BLOCKED] offset 必须为非负整数，limit 必须为正整数"
                limit = min(limit, 8000)
                p = self._safe(a.get("path") or "")
                with open(p, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                start = min(offset, len(text))
                end = min(start + limit, len(text))
                state = self._read_state(p, text)
                ranges = sorted(state["ranges"] + [(start, end)])
                merged = []
                for left, right in ranges:
                    if merged and left <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], right))
                    else:
                        merged.append((left, right))
                state["ranges"] = merged
                page = text[start:end]
                if _sanitize_tool_out is not None:
                    page = _sanitize_tool_out(page)
                if start > 0 or end < len(text):
                    next_offset = end if end < len(text) else (merged[0][1] if merged[0][0] == 0 else 0)
                    page += ("\n[WARNING: PARTIAL FILE! offset=%d, end=%d, total=%d, next offset=%d%s. "
                             "不要拿局部内容去 writefile（write_file）；小改请用 patch_file。]" % (
                                 start, end, len(text), next_offset, " (EOF)" if end == len(text) else ""))
                return page
            if name == "grep":
                base = self._safe(a.get("path") or self.cwd)
                rx = re.compile(a.get("pattern") or "")
                files = [base] if os.path.isfile(base) else [
                    os.path.join(dp, fn) for dp, dns, fns in os.walk(base) for fn in fns
                    if not any(x in dp.lower() for x in ("\\node_modules", "\\.git", "__pycache__"))][:400]
                hits = []
                for fp in files:
                    try:
                        fp = self._safe(fp)
                        for i, line in enumerate(open(fp, encoding="utf-8", errors="ignore"), 1):
                            if rx.search(line):
                                hits.append("%s:%d: %s" % (fp, i, line.strip()[:130]))
                                if len(hits) >= 100:
                                    return "\n".join(hits)
                    except Exception:
                        pass
                return "\n".join(hits) or "(无命中)"
            if name == "write_file":
                p = self._safe_write(a.get("path") or "")
                os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
                c = a.get("content") or ""
                old_txt = ""
                if os.path.exists(p):
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        old_txt = f.read()
                    state = self._read_state(p, old_txt)
                    complete = state["ranges"] == [(0, len(old_txt))]
                    if len(c) < len(old_txt) and not complete:
                        return ("[BLOCKED] 文件仍有未读区间，禁止将 %d 字符覆盖为 %d 字符。"
                                "请读完当前版本全部内容，或用 patch_file 做局部修改。" % (len(old_txt), len(c)))
                if old_txt == c:
                    return "%s 内容未变化，未写入" % p
                open(p, "w", encoding="utf-8").write(c)
                self._read_ranges[os.path.normcase(os.path.realpath(p))] = {
                    "digest": hashlib.sha256(c.encode("utf-8")).hexdigest(), "ranges": [(0, len(c))]}
                self._note_written(p)
                return "已写入 %s（%d 字符）%s" % (p, len(c), self._verify(p))
            if name == "patch_file":
                p = self._safe_write(a.get("path") or "")
                old, new = a.get("old") or "", a.get("new") or ""
                t = open(p, encoding="utf-8", errors="ignore").read()
                if t.count(old) != 1:
                    return "匹配到 %d 处，需要唯一匹配" % t.count(old)
                merged = t.replace(old, new, 1)
                open(p, "w", encoding="utf-8").write(merged)
                self._read_ranges[os.path.normcase(os.path.realpath(p))] = {
                    "digest": hashlib.sha256(merged.encode("utf-8")).hexdigest(), "ranges": [(0, len(merged))]}
                self._note_written(p)
                return "已替换 1 处：%s%s" % (p, self._verify(p))
            if name == "run_cmd":
                cmd = (a.get("cmd") or "").strip()
                low = cmd.lower()
                for bad in DENY_CMD:
                    if bad in low:
                        return "命令被拒绝（黑名单：%s）" % bad
                before = self._fs_snapshot(cmd)
                p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.cwd,
                                     encoding="utf-8", errors="ignore",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    stdout, stderr = p.communicate(timeout=40)
                    out = (((stdout or "") + (stderr or "")).strip() or "(无输出)")[:12000]
                    changed = self._fs_changed(before, cmd)
                    if changed:
                        for _p in changed:
                            self._note_written(_p)
                        checks = ''.join(self._verify(_p) for _p in changed if _p.lower().endswith(('.py', '.js', '.mjs')))
                        return out + '\n[文件变化] ' + ', '.join(changed[:6]) + checks
                    if re.search(r'(set-content|out-file|add-content|tee-object|git apply|copy-item|move-item|new-item|remove-item)',
                                 low):
                        # 命令看起来要写，但没检测到文件变化：记未核实，不算完成
                        self._unverified = True
                        return out + '\n[修改未核实] 命令像写操作，但没有检测到目标文件变化'
                    return out
                finally:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   capture_output=True, timeout=15,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if p.poll() is None:
                        p.kill()
                    p.communicate()

            return "未知工具：%s" % name
        except Exception as e:
            return "工具异常：%s" % str(e)[:160]

    def _mem_file(self):
        return os.path.join(DATA_DIR, "hub", "memory-%s.jsonl" % (self.provider or "mimo"))

    def _save_turn_memory(self, out_text):
        try:
            mf = self._mem_file()
            os.makedirs(os.path.dirname(mf), exist_ok=True)
            rec = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "task": (self.task or "").replace("\n", " ")[:240],
                "out": (out_text or "").replace("\n", " ")[:360],
                "files": getattr(self, "_written_paths", [])[:8],
            }
            with open(mf, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # 统一记忆通道：同步写入 MemoryBank（去重 + 来源引用），统一检索/预算选择由此取用。
            try:
                from agent_memory import MemoryBank, CompressedMemory
                _entry = "任务:%s => 结论:%s" % (rec["task"], rec["out"])
                MemoryBank(os.path.join(DATA_DIR, "hub", "agent-memory")).get(
                    self.provider or "mimo").add(_entry, source="agent_loop#turn", kind="task")
                CompressedMemory().remember_messages([{
                    "id": "turn-" + __import__('uuid').uuid4().hex, "sender": self.provider,
                    "to": [self.provider], "kind": "task", "ts": time.time(),
                    "text": "需求 " + (self.task or "") + "\n结果 " + (out_text or "") +
                            "\n文件 " + ", ".join(rec["files"])}], "agent_loop#turn")
            except Exception:
                pass
        except OSError:
            pass

    def _context_block(self):
        from agent_memory import CompressedMemory
        return CompressedMemory().context(self.provider, self.task, budget=2400)

    @reported("provider")
    def run(self):
        # Transport constraints are not the user's task intent.
        intent = re.sub(r'^【硬约束】[^\n]*\n', '', self.task or '')
        # History is evidence, not a request to repeat an earlier modification.
        intent = intent.rsplit('【当前任务】', 1)[-1].strip()
        intent = re.sub(r'^【[^】]+】：?\s*', '', intent)
        intent = intent.split('【输出纪律】', 1)[0].strip()
        explicit_read_only = bool(re.search(
            r'(不要修改任何文件|不要改文件|不修改文件|禁止修改文件|只需回复|仅回复|只出方案|先给方案|别动手|只查询|仅查询)', intent))
        _modify = bool(re.search(
            r'(修改|改成|改为|修复|实现|新增|增加|删除|去掉|替换|落盘|写入|补上|改掉|调整)', intent)) and not explicit_read_only
        self._modify = _modify
        import routing_config as bootstrap
        from memory_summary import build_recent_memory_block
        bootstrap.sync_workspace_rules()
        sys_prompt = bootstrap.build(self.provider)
        sys_prompt += build_recent_memory_block(self.provider, self.task)
        msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": self.task}]
        self._wrote = False
        round_budget = min(MAX_ROUNDS, self.rounds if self.rounds and self.rounds > 0 else MAX_ROUNDS)
        file_writes = {}
        last_signature = None
        repeated_calls = 0
        for rnd in range(round_budget):
            # 让 agent-activity 里的进度实时反映"第几轮 + 在跑什么"，别只显示一个笼统的任务名
            self._round = rnd + 1
            _rec = getattr(self, "_activity_record", None)
            if _rec is not None:
                _rec["round"] = rnd + 1
                _rec["tool"] = ""
            if rnd == round_budget - 2:
                msgs.append({"role": "user", "content": "执行预算还剩2轮，工具仍可正常使用，请优先完成修改与验证。"})
            if getattr(self, "_modify", False) and rnd >= 5 and not getattr(self, "_wrote", False):
                msgs.append({"role": "user", "content": "你还没落盘。现在立刻用 patch_file 或 write_file 改目标文件，禁止再只读或只解释。"})
            if getattr(self, "_modify", False) and rnd == 7 and not getattr(self, "_wrote", False):
                msgs.append({"role": "user", "content": "最后通牒：第 8 轮必须落盘。下一次调用只允许 patch_file/write_file，读完就直接改。"})
            m = self.chat(msgs)
            m.setdefault("role", "assistant")
            msgs.append(m)
            calls = m.get("tool_calls") or []
            if not calls:
                txt = m.get("content") or ""
                # 标准形态
                for mm in re.finditer(r"<tool_call>\s*<function=([a-zA-Z_]+)>(.*?)</function>\s*</tool_call>", txt, re.S):
                    args = {pm.group(1): pm.group(2).strip()
                            for pm in re.finditer(r"<parameter=([a-zA-Z_]+)>(.*?)</parameter>", mm.group(2), re.S)}
                    calls.append({"id": "x", "function": {"name": mm.group(1),
                                                          "arguments": json.dumps(args, ensure_ascii=False)}})
                # 兜底：模型常写不闭合的 <function=name> … <parameter=k>v（缺 </function>/</parameter>）
                if not calls:
                    for mm in re.finditer(r"<function\s*=\s*([a-zA-Z_]+)\s*>(.*?)(?=</function>|</tool_call>|$)", txt, re.S):
                        body = mm.group(2)
                        args = {}
                        for pm in re.finditer(r"<parameter\s*=\s*([a-zA-Z_]+)\s*>\s*(.*?)\s*(?=</parameter>|<parameter\s*=|</function>|$)",
                                              body, re.S):
                            args[pm.group(1)] = pm.group(2).strip()
                        if args:
                            calls.append({"id": "x", "function": {"name": mm.group(1),
                                                                  "arguments": json.dumps(args, ensure_ascii=False)}})
                if not calls:
                    if txt.strip() and (not getattr(self, "_modify", False) or getattr(self, "_wrote", False)):
                        return self._deliver(txt)
                    if txt.strip() and rnd < round_budget - 1:
                        # 修改类任务只给方案不动手 = 没干活：打回去逼它落盘
                        msgs.append({"role": "user", "content": "不要发方案或 diff。现在调用 patch_file 把改动写进目标文件，然后再回答。"})
                        continue
                    log({"ts": time.time(), "event": "empty_response", "provider": self.provider,
                         "round": rnd, "message_keys": list(m)})
                    # Retry empty output with tools still available.
                    msgs.append({"role": "user", "content": "上次响应为空，工具仍可正常使用，请继续执行并验证。"})
                    continue
            if calls:
                for index, call in enumerate(calls):
                    if call.get("id") == "x":
                        call["id"] = "xml_%s_%s" % (rnd, index)
                m["tool_calls"] = calls
            for c in calls:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                name = fn.get("name")
                signature = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                repeated_calls = repeated_calls + 1 if signature == last_signature else 1
                last_signature = signature
                if repeated_calls > 8:
                    return self._fuse(rnd + 1)
                if name in ("patch_file", "write_file"):
                    path = str(args.get("path") or "")
                    path = os.path.normcase(os.path.abspath(os.path.join(self.cwd, path)))
                    file_writes[path] = file_writes.get(path, 0) + 1
                    if file_writes[path] > MAX_FILE_WRITES:
                        return self._fuse(rnd + 1)
                out = self.run_tool(name, args)
                log({"ts": time.time(), "provider": self.provider, "round": rnd,
                     "tool": fn.get("name"), "args": args, "out": out[:300]})
                # read_file already limits each page and sanitizes it. Condensing it
                # again would discard content that the coverage tracker counted as read.
                content = out if fn.get("name") == "read_file" else _fit_tool_output(out)
                msgs.append({"role": "tool", "tool_call_id": c.get("id"), "content": content})
        # Budget exhaustion is an execution stop, never a model turn with tools removed.
        log({"ts": time.time(), "event": "execution_budget_exhausted",
             "provider": self.provider, "rounds": round_budget})
        return self._fuse(round_budget)

    def _fuse(self, rounds):
        return "⚠️ [防跑飞熔断] 已连续执行 %d 轮未收敛，已安全停止，请检查目标文件状态。" % rounds



def msgs_to_vertex(msgs):
    """把 OpenAI 风格 messages 转成 Vertex contents。"""
    out = []
    for m in msgs:
        role = m.get("role")
        if role == "system":
            out.append({"role": "user", "parts": [{"text": "[系统] " + (m.get("content") or "")}]})
        elif role == "user":
            out.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})
        elif role == "assistant":
            parts = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
            out.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif role == "tool":
            out.append({"role": "user", "parts": [{"functionResponse": {
                "name": "tool", "response": {"result": m.get("content") or ""}}}]})
    return out



def _paused_marker():
    p = cfg.root_path('data/mimo-paused')
    if os.path.exists(p):
        # 超过 3 分钟的暂停标记视为已过期，自动清理防止 mimo 永久假死
        try:
            if time.time() - os.path.getmtime(p) > 180:
                os.remove(p)
                return False
        except OSError:
            pass
        return True
    return False

def _main():
    if "--provider" not in sys.argv or sys.argv[sys.argv.index("--provider") + 1:][:1] == ["mimo"]:
        paused = _paused_marker()
    else:
        paused = False
    if paused:
        # 暂停标记：输出静默符，hub 会吞掉，不再逐条回执打扰 dsh
        print('[[silent]]')
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mimo")
    ap.add_argument("--task", required=True)
    ap.add_argument("--cwd", default=cfg.root_path(''))
    ap.add_argument("--rounds", type=int, default=25)
    a = ap.parse_args()
    os.makedirs(a.cwd, exist_ok=True)
    t0 = time.time()
    out = Runner(a.provider, a.task, a.cwd, a.rounds).run()
    if not isinstance(out, str) or not out.strip():
        out = "⚠️ [执行异常] EmptyResponse：执行未返回有效回执。"
    print(out)
    log({"ts": time.time(), "event": "done", "provider": a.provider, "out": out[:200]})


def main():
    try:
        return _main()
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code in (None, 0):
            return 0
        # Exception messages can contain credentials or request bodies; keep the receipt safe.
        msg = str(error)
        http_match = re.search(r"provider HTTP \d{3}", msg)
        if http_match:
            receipt = "⚠️ [执行异常] %s：执行已停止，请检查运行日志与目标文件状态。" % http_match.group(0)
        else:
            receipt = "⚠️ [执行异常] %s：执行已停止，请检查运行日志与目标文件状态。" % type(error).__name__
        try:
            print(receipt, flush=True)
        except UnicodeEncodeError:
            print(receipt.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
