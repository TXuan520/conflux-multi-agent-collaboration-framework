"""Opt-in, non-streaming protocol adapter; importing performs no I/O.

Only probe_capability sends HTTP, exclusively a 16-token dummy-tool probe.
No business transport, config writes, XML execution, or agentloop integration.
Run: python -B ${CONFLUX_ROOT}\\scripts\\provideradapter.py --self-test

Integration: encode_request(protocol, model, messages, tools=(), max_tokens=1024)
returns a wire payload for the caller's existing transport; decode_response
returns StandardMessage. Tool schemas use name/description/parameters.
Parts support text and tool_result (tool_call_id/name/content/is_error).
Gemini part metadata is retained in native_parts for same-protocol replay,
including thoughtSignature; it must not be copied across protocols.

Isolation proof: this is the only added source file. No imports of agentloop,
arch, mimo or codex, no registration, environment mutation or import-time I/O.
Existing callers do not import this new filename, so their paths stay intact.
Probe endpoint is an explicit full URL; caller supplies authentication headers.
Uncertain HTTP/protocol errors fail closed as UNREACHABLE, never a downgrade.

Wire references:
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
https://ai.google.dev/api/generate-content
"""

from __future__ import annotations

import argparse
import copy
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, MutableMapping


PROTOCOLS = ("openaichat", "anthropicmessages", "gemininative")
DUMMY_NAME = "capability_probe"


@dataclass
class ToolCallIntent:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class StandardMessage:
    role: str
    parts: list[dict[str, Any]] = field(default_factory=list)
    toolcalls: list[ToolCallIntent] = field(default_factory=list)
    native_protocol: str | None = None
    native_parts: list[dict[str, Any]] | None = None


class ProbeStatus(str, Enum):
    NATIVETOOLS = "NATIVETOOLS"
    NONATIVETOOLS = "NONATIVETOOLS"
    UNREACHABLE = "UNREACHABLE"
    AUTH_ERROR = "AUTH_ERROR"


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    elapsed: float
    detail: str = ""

    @property
    def can_fallback(self) -> bool:
        return self.status is ProbeStatus.NONATIVETOOLS


def _protocol(protocol: str) -> None:
    if protocol not in PROTOCOLS:
        raise ValueError("unsupported protocol: " + protocol)


def encode_request(protocol: str, model: str, messages: list[StandardMessage],
                   tools: tuple | list = (), max_tokens: int = 1024,
                   *, force_tool: str | None = None) -> dict[str, Any]:
    """Pure conversion. Text/tool-result parts only; unsupported parts raise."""
    _protocol(protocol)
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    wire, system = [], []
    for msg in messages:
        if msg.role not in ("system", "user", "assistant", "tool"):
            raise ValueError("unsupported role: " + msg.role)
        for part in msg.parts:
            if part.get("type") not in ("text", "tool_result"):
                raise ValueError("unsupported part type")
        if msg.toolcalls and msg.role != "assistant":
            raise ValueError("only assistant messages may request tools")
        if any(p["type"] == "tool_result" for p in msg.parts) and msg.role not in ("tool", "user"):
            raise ValueError("tool results must have user/tool role")
        if msg.role == "system":
            if any(p["type"] != "text" for p in msg.parts):
                raise ValueError("system parts must be text")
            system.extend(p["text"] for p in msg.parts)
            if protocol != "openaichat":
                continue
        if protocol == "openaichat":
            texts = [p["text"] for p in msg.parts if p["type"] == "text"]
            results = [p for p in msg.parts if p["type"] == "tool_result"]
            if texts or msg.toolcalls or not results:
                if msg.role == "tool":
                    raise ValueError("tool role requires tool_result parts only")
                item = {"role": msg.role, "content": "\n".join(texts) or None}
                if msg.toolcalls:
                    item["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                        "name": c.name, "arguments": json.dumps(c.arguments)}} for c in msg.toolcalls]
                wire.append(item)
            wire.extend({"role": "tool", "tool_call_id": p["tool_call_id"],
                         "content": p["content"] if isinstance(p["content"], str)
                         else json.dumps(p["content"])} for p in results)
        else:
            parts = []
            for p in msg.parts:
                if p["type"] == "text":
                    parts.append({"type": "text", "text": p["text"]} if protocol == "anthropicmessages"
                                 else {"text": p["text"]})
                elif protocol == "anthropicmessages":
                    parts.append({"type": "tool_result", "tool_use_id": p["tool_call_id"],
                                  "content": p["content"] if isinstance(p["content"], str)
                                  else json.dumps(p["content"]), "is_error": p.get("is_error", False)})
                else:
                    if not p.get("name"):
                        raise ValueError("Gemini tool_result requires name")
                    parts.append({"functionResponse": {"id": p["tool_call_id"], "name": p["name"],
                                  "response": {"error" if p.get("is_error") else "result": p["content"]}}})
            for c in msg.toolcalls:
                parts.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                             if protocol == "anthropicmessages" else
                             {"functionCall": {"id": c.id, "name": c.name, "args": c.arguments}})
            if protocol == "gemininative" and msg.native_protocol == protocol and msg.native_parts is not None:
                parts = copy.deepcopy(msg.native_parts)
            if protocol == "anthropicmessages":
                item = {"role": "user" if msg.role == "tool" else msg.role, "content": parts}
                if wire and wire[-1]["role"] == item["role"]:
                    wire[-1]["content"].extend(parts)
                else:
                    wire.append(item)
            else:
                wire.append({"role": "model" if msg.role == "assistant" else "user", "parts": parts})
    if protocol == "openaichat":
        payload = {"model": model, "messages": wire, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = [{"type": "function", "function": copy.deepcopy(t)} for t in tools]
        if force_tool:
            payload["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
    elif protocol == "anthropicmessages":
        payload = {"model": model, "messages": wire, "max_tokens": max_tokens}
        if system:
            payload["system"] = "\n".join(system)
        if tools:
            payload["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                                 "input_schema": copy.deepcopy(t["parameters"])} for t in tools]
        if force_tool:
            payload["tool_choice"] = {"type": "tool", "name": force_tool}
    else:
        payload = {"contents": wire, "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": copy.deepcopy(list(tools))}]
        if force_tool:
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [force_tool]}}
    return payload


def decode_response(protocol: str, payload: dict[str, Any]) -> StandardMessage:
    """Pure non-streaming response conversion; malformed responses raise."""
    _protocol(protocol)
    result = StandardMessage("assistant")
    if protocol == "openaichat":
        message = payload["choices"][0]["message"]
        content = message.get("content")
        if isinstance(content, str):
            result.parts.append({"type": "text", "text": content})
        elif content is not None:
            raise ValueError("unsupported OpenAI content")
        for call in message.get("tool_calls", []):
            args = json.loads(call["function"]["arguments"])
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be an object")
            result.toolcalls.append(ToolCallIntent(call["id"], call["function"]["name"], args))
    else:
        parts = payload["content"] if protocol == "anthropicmessages" else payload["candidates"][0]["content"]["parts"]
        if not isinstance(parts, list):
            raise ValueError("response parts must be a list")
        if protocol == "gemininative":
            result.native_protocol, result.native_parts = protocol, copy.deepcopy(parts)
        for i, part in enumerate(parts):
            if protocol == "anthropicmessages" and part.get("type") == "tool_use":
                result.toolcalls.append(ToolCallIntent(part["id"], part["name"], part["input"]))
            elif protocol == "gemininative" and "functionCall" in part:
                c = part["functionCall"]
                result.toolcalls.append(ToolCallIntent(c.get("id", "gemini_" + str(i)), c["name"], c.get("args", {})))
            elif "text" in part and not part.get("thought"):
                result.parts.append({"type": "text", "text": part["text"]})
        if any(not isinstance(c.arguments, dict) for c in result.toolcalls):
            raise ValueError("tool arguments must be an object")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward caller credentials to a redirected endpoint.


def protect_api_key(value):
    """Encrypt newly stored keys with the current Windows user's DPAPI key."""
    import ctypes
    import base64
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("API Key 加密失败")
    try:
        return "dpapi:" + base64.b64encode(ctypes.string_at(result.data, result.size)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def read_secret_file(path):
    """Read DPAPI keys and existing plaintext references."""
    import ctypes
    import base64
    from ctypes import wintypes
    with open(path, encoding="utf-8") as stream:
        value = stream.read().strip()
    if not value.startswith("dpapi:"):
        return value
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]
    raw = base64.b64decode(value[6:], validate=True)
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("API Key 解密失败")
    try:
        return ctypes.string_at(result.data, result.size).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def probe_capability(protocol: str, endpoint_url: str, model: str,
                     headers: dict[str, str] | None = None,
                     *, timeout: float = 3.0) -> ProbeResult:
    """One dummy request; total caller wait <=3s, including DNS/body reads.

    A daemon worker bounds caller latency even if OS DNS ignores socket timeout.
    It cannot mutate capabilities. A timed-out OS operation may finish later.
    No retries, redirects, business messages, tool execution or persistence.
    """
    started = time.monotonic()
    _protocol(protocol)
    if not 0 < timeout <= 3.0:
        raise ValueError("probe timeout must be in (0, 3]")
    if not endpoint_url.startswith(("http://", "https://")):
        raise ValueError("endpoint_url must be a full HTTP(S) endpoint")
    endpoint_url = endpoint_url.replace("{model}", model)
    deadline = started + timeout
    tool = {"name": DUMMY_NAME, "description": "Capability check only; no side effects.",
            "parameters": {"type": "object", "properties": {}, "required": []}}
    body = encode_request(protocol, model, [StandardMessage("user", [
        {"type": "text", "text": "Call capability_probe with no arguments."}])], [tool],
        max_tokens=16, force_tool=DUMMY_NAME)
    request_headers = {"Content-Type": "application/json"}
    if protocol == "anthropicmessages":
        request_headers["anthropic-version"] = "2023-06-01"
    request_headers.update(headers or {})
    output = queue.Queue(maxsize=1)

    def _extract_upstream_msg(raw_text: str) -> str:
        text = (raw_text or "").strip()
        if not text:
            return ""
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                err = obj.get("error")
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("msg") or err.get("code") or ""
                    if msg:
                        return str(msg).strip()[:160]
                elif isinstance(err, str) and err.strip():
                    return err.strip()[:160]
                for k in ("message", "msg", "detail", "error_description"):
                    if isinstance(obj.get(k), str) and obj[k].strip():
                        return obj[k].strip()[:160]
        except Exception:
            pass
        return text[:120]

    def worker():
        status, detail = ProbeStatus.UNREACHABLE, "transport_error"
        try:
            req = urllib.request.Request(endpoint_url, data=json.dumps(body).encode(),
                                         headers=request_headers, method="POST")
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(req, timeout=max(0.001, deadline - time.monotonic())) as response:
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise ValueError("response too large")
                decoded = decode_response(protocol, json.loads(raw))
                native = any(c.name == DUMMY_NAME and c.arguments == {} for c in decoded.toolcalls)
                status = ProbeStatus.NATIVETOOLS if native else ProbeStatus.NONATIVETOOLS
                detail = "dummy_tool_observed" if native else "no_dummy_tool_observed"
        except urllib.error.HTTPError as exc:
            raw_body = exc.read(8192).decode("utf-8", errors="replace")
            raw_low = raw_body.lower()
            up_msg = _extract_upstream_msg(raw_body)
            base_detail = {401: "API Key 无效或未授权 (HTTP 401)",
                           403: "API Key 无权限访问该模型或未开通服务 (HTTP 403)",
                           404: "Endpoint 地址或模型名称不存在 (HTTP 404)",
                           429: "配额耗尽、余额不足或触发限流 (HTTP 429)",
                           400: "请求参数或模型名不匹配 (HTTP 400)",
                           422: "请求参数或模型名不匹配 (HTTP 422)"}.get(
                               exc.code, "API 服务返回错误 (HTTP %s)" % exc.code)
            if exc.code == 429 and any(w in raw_low for w in ("insufficient", "balance", "arrears", "billing", "quota", "credit")):
                base_detail = "账户余额不足或该模型无可用配额 (HTTP 429)"
            elif exc.code == 403 and any(w in raw_low for w in ("permission", "access denied", "not enabled", "region", "location")):
                base_detail = "账号未开通该模型访问权限或区域受限 (HTTP 403)"
            detail = (base_detail + "：" + up_msg) if up_msg else base_detail
            if exc.code in (401, 403):
                status = ProbeStatus.AUTH_ERROR
            elif exc.code in (400, 422):
                if any(word in raw_low for word in ("tools", "tool_choice", "function calling", "functioncalling")) and any(
                    word in raw_low for word in ("not supported", "unsupported", "not allowed", "unknown parameter")):
                    # Fallback plain chat probe (without tools) to verify model & quota
                    try:
                        plain_body = encode_request(protocol, model, [StandardMessage("user", [
                            {"type": "text", "text": "hi"}])], [], max_tokens=8)
                        req2 = urllib.request.Request(endpoint_url, data=json.dumps(plain_body).encode(),
                                                      headers=request_headers, method="POST")
                        with urllib.request.build_opener(_NoRedirect()).open(
                                req2, timeout=max(0.2, deadline - time.monotonic())) as resp2:
                            decode_response(protocol, json.loads(resp2.read(262144)))
                        status = ProbeStatus.NONATIVETOOLS
                        detail = "explicit_tools_unsupported"
                    except urllib.error.HTTPError as exc2:
                        raw2 = exc2.read(8192).decode("utf-8", errors="replace")
                        up2 = _extract_upstream_msg(raw2)
                        if exc2.code in (401, 403):
                            status = ProbeStatus.AUTH_ERROR
                        detail = ("模型不支持工具调用且调用失败 (HTTP %s)" % exc2.code) + (("：" + up2) if up2 else "")
                        exc2.close()
                    except Exception:
                        status = ProbeStatus.NONATIVETOOLS
                        detail = "explicit_tools_unsupported"
            exc.close()
        except Exception as exc:
            detail = "Endpoint 无法连接或网络不通（DNS/连接超时）" if isinstance(
                exc, (urllib.error.URLError, TimeoutError, OSError)) else "API 返回格式无效或协议不匹配"
        output.put((status, detail))

    threading.Thread(target=worker, daemon=True, name="provider-capability-probe").start()
    try:
        status, detail = output.get(timeout=max(0, deadline - time.monotonic()))
        if time.monotonic() > deadline:
            status, detail = ProbeStatus.UNREACHABLE, "Endpoint 无法连接或网络超时（超过 3 秒）"
    except queue.Empty:
        status, detail = ProbeStatus.UNREACHABLE, "Endpoint 无法连接或网络超时（超过 3 秒）"
    return ProbeResult(status, time.monotonic() - started, detail)


def apply_probe_result(capabilities: MutableMapping, provider_key: str,
                       result: ProbeResult) -> bool:
    """Explicit opt-in memory update; failures never overwrite existing entries."""
    if result.status not in (ProbeStatus.NATIVETOOLS, ProbeStatus.NONATIVETOOLS):
        return False
    entry = dict(capabilities.get(provider_key, {}))
    entry.update(native_tools=result.status is ProbeStatus.NATIVETOOLS,
                 xmlreact=result.can_fallback)
    capabilities[provider_key] = entry
    return True


def self_test() -> None:
    """Loopback mock only. Assertions cover protocols, status/write guards/deadline."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest.mock import patch

    def native(protocol):
        if protocol == "openaichat":
            return {"choices": [{"message": {"content": None, "tool_calls": [{"id": "p1", "type": "function",
                "function": {"name": DUMMY_NAME, "arguments": "{}"}}]}}]}
        if protocol == "anthropicmessages":
            return {"content": [{"type": "tool_use", "id": "p1", "name": DUMMY_NAME, "input": {}}]}
        return {"candidates": [{"content": {"parts": [{"functionCall": {"name": DUMMY_NAME, "args": {}},
                                                        "thoughtSignature": "preserve-me"}]}}]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            protocol, mode = self.path.strip("/").split("/")
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert (request.get("generationConfig", {}).get("maxOutputTokens") if protocol == "gemininative"
                    else request["max_tokens"]) in (16, 8)
            has_tools = "tools" in request
            if has_tools:
                assert DUMMY_NAME in json.dumps(request["tools"])
            code, payload = 200, native(protocol)
            if mode in ("401", "403", "500", "429", "400"):
                code, payload = int(mode), {"error": "temporary or invalid request"}
            elif mode == "unsupported":
                if has_tools:
                    code, payload = 400, {"error": "tools not supported"}
                else:
                    payload = ({"choices": [{"message": {"content": "OK"}}]} if protocol == "openaichat" else
                               {"content": [{"type": "text", "text": "OK"}]} if protocol == "anthropicmessages" else
                               {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})
            elif mode == "plain":
                payload = ({"choices": [{"message": {"content": "OK"}}]} if protocol == "openaichat" else
                           {"content": [{"type": "text", "text": "OK"}]} if protocol == "anthropicmessages" else
                           {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})
            elif mode == "malformed":
                payload = {"error": "not a successful protocol response"}
            elif mode == "slow":
                time.sleep(3.3)
            encoded = json.dumps(payload).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:" + str(server.server_port)
    try:
        for protocol in PROTOCOLS:
            for mode, expected in (("native", ProbeStatus.NATIVETOOLS), ("plain", ProbeStatus.NONATIVETOOLS),
                                   ("500", ProbeStatus.UNREACHABLE), ("401", ProbeStatus.AUTH_ERROR),
                                   ("403", ProbeStatus.AUTH_ERROR), ("unsupported", ProbeStatus.NONATIVETOOLS),
                                   ("429", ProbeStatus.UNREACHABLE), ("400", ProbeStatus.UNREACHABLE),
                                   ("malformed", ProbeStatus.UNREACHABLE)):
                result = probe_capability(protocol, base + "/" + protocol + "/" + mode, "mock")
                assert result.status is expected, (protocol, mode, result)
                caps = {"mock": {"sentinel": True}}
                before = copy.deepcopy(caps)
                changed = apply_probe_result(caps, "mock", result)
                assert changed == (expected in (ProbeStatus.NATIVETOOLS, ProbeStatus.NONATIVETOOLS))
                if not changed:
                    assert caps == before and not result.can_fallback
                else:
                    assert caps["mock"]["sentinel"] is True
                print(protocol, mode, "=>", result.status.value)
            decoded = decode_response(protocol, native(protocol))
            assert decoded.toolcalls[0].name == DUMMY_NAME
            history = [StandardMessage("system", [{"type": "text", "text": "system"}]),
                       StandardMessage("user", [{"type": "text", "text": "run"}]), decoded,
                       StandardMessage("tool", [{"type": "tool_result", "tool_call_id": decoded.toolcalls[0].id,
                                                 "name": DUMMY_NAME, "content": {"ok": True}}])]
            converted = encode_request(protocol, "mock", history)
            if protocol == "gemininative":
                assert converted["contents"][1]["parts"][0]["thoughtSignature"] == "preserve-me"
            assert "system" in json.dumps(converted) and "ok" in json.dumps(converted)
        result = probe_capability("openaichat", base + "/openaichat/slow", "mock")
        assert result.status is ProbeStatus.UNREACHABLE and result.elapsed < 3.15, result
        print("timeout =>", result.status.value, "elapsed=%.3fs" % result.elapsed)
        with patch("socket.getaddrinfo", side_effect=OSError("mock DNS failure")):
            result = probe_capability("openaichat", base + "/openaichat/native", "mock")
            assert result.status is ProbeStatus.UNREACHABLE
            print("DNS =>", result.status.value)
        refused = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        refused_port = refused.server_port
        refused.server_close()
        result = probe_capability("openaichat", "http://127.0.0.1:%s/openaichat/native" % refused_port, "mock")
        assert result.status is ProbeStatus.UNREACHABLE
        print("TCP =>", result.status.value)
        print("PASS: conversions; four statuses; failure write/fallback guards; 3s deadline; no integration changes")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    if parser.parse_args().self_test:
        self_test()
