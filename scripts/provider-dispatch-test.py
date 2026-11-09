"""Local registration -> arrow routing -> flush_batch -> real worker -> mock API."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import routing


def main():
    scripts = Path(__file__).resolve().parent
    load_hub = runpy.run_path(str(scripts / "provider-registration-test.py"))["load_hub"]
    ns = load_hub()
    from broadcast_policy import BroadcastPolicy
    ns['_broadcast_policy'] = BroadcastPolicy(lambda *args: ns['_deliver_to_target'](*args))
    requests, replies, launches = [], [], []

    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            protocol, mode = self.path.strip("/").split("/")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((protocol, mode, body))
            assert self.headers.get({"openaichat": "Authorization", "anthropicmessages": "x-api-key",
                                    "gemininative": "x-goog-api-key"}[protocol]) in ("Bearer test-key", "test-key")
            probe = "capability_probe" in json.dumps(body.get("tools", []))
            tool_done = "fixture-content" in json.dumps(body)
            name = "capability_probe" if probe else "read_file"
            args = {} if probe else {"path": str(fixture)}
            text = "reply-complete" if tool_done else ""
            call = not tool_done
            if mode == "xml":
                call = False
                text = "probe-plain" if probe else ("reply-complete" if tool_done else
                    "<tool_call><function=read_file><parameter=path>" + str(fixture) +
                    "</parameter></function></tool_call>")
                if not probe:
                    assert "tools" not in body
            if protocol == "openaichat":
                msg = {"role": "assistant", "content": text}
                if call:
                    msg["tool_calls"] = [{"id": "call-1", "type": "function", "function": {
                        "name": name, "arguments": json.dumps(args)}}]
                payload = {"choices": [{"message": msg}]}
            elif protocol == "anthropicmessages":
                payload = {"content": ([{"type": "tool_use", "id": "call-1", "name": name, "input": args}]
                           if call else [{"type": "text", "text": text}])}
            else:
                parts = ([{"functionCall": {"id": "call-1", "name": name, "args": args},
                           "thoughtSignature": "test-signature"}] if call else [{"text": text}])
                payload = {"candidates": [{"content": {"parts": parts}}]}
                if tool_done:
                    assert "test-signature" in json.dumps(body)
            if mode == "fail" and not probe:
                self.send_response(503)
            else:
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    def no_builtin(*args, **kwargs):
        raise AssertionError("custom message reached a builtin agent")

    def launch(target, fn, text, **kwargs):
        launches.append(target)
        fn(text)

    with tempfile.TemporaryDirectory(prefix="provider-dispatch-") as temp:
        root = Path(temp)
        data = root / "data" / "hub"
        data.mkdir(parents=True)
        workspace = root / "workspace"
        workspace.mkdir()
        fixture = workspace / "fixture.txt"
        fixture.write_text("fixture-content", encoding="utf-8")
        providers = data / "providers.json"
        ns.update(DATA_DIR=str(data), PROVIDERS_FILE=str(providers),
                  ADMIN_TOKEN_FILE=str(data / "admin.token"), SECRET_DIRS=(str(root / "data/secrets") + os.sep,),
                  subprocess=subprocess, cfg=SimpleNamespace(
                      optional_env=lambda name: sys.executable,
                      root_path=lambda name: str(scripts / "agent_loop.py"), WORKSPACE=str(workspace)),
                  DISPLAY_AGENTS=("codex", "ag", "dsh", "mimo", "arch", "doubao", "workbuddy"),
                  AGENTS=(), AGENT_LABELS={}, ROUNDTABLE_ENABLED=False, _to_map={},
                  read_msgs=lambda since: [], activity=lambda *a, **k: None, mark=lambda *a: None,
                  append_msg=lambda sender, text: replies.append((sender, text)),
                  upsert_task=lambda *a: None, is_no_reply_needed=lambda text: False,
                  shrink_handoff=lambda text, sender: text, msg_stamp=lambda mid: "test",
                  dispatch_once=lambda *a, **k: True, push_expecting_target=lambda *a, **k: None,
                  _brainstorm_dispatch=lambda fn, target, mids: fn, launch_agent=launch,
                  _compressed_agent_context=lambda agent, query: "",
                  agent_failure=lambda *args: None, track_verdict_now=lambda name: "")
        for name in ("ask_codex", "ask_antigravity", "inject_qq", "ask_mimo", "ask_arch", "ask_doubao", "ask_workbuddy"):
            ns[name] = no_builtin
        ns["providers_write"]({"providers": []})
        endpoint = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        registration = ThreadingHTTPServer(("127.0.0.1", 0), ns["Handler"])
        for server in (endpoint, registration):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {"CONFLUX_ROOT": temp, "CONFLUX_DATA_ROOT": str(root / "data"),
               "CONFLUX_DATA_DIR": str(data), "CONFLUX_WORKSPACE": str(workspace),
               "PROVIDER_DISPATCH_TEST_KEY": "test-key", "HUB_PROBE_ALLOW_PRIVATE": "1",
               "PYTHONIOENCODING": "utf-8"}
        try:
            with patch.dict(os.environ, env), patch.object(routing, "PROVIDERS_FILE", str(providers)):
                for protocol, mode in (("openaichat", "native"), ("anthropicmessages", "native"),
                                       ("gemininative", "native"), ("openaichat", "xml"),
                                       ("openaichat", "fail")):
                    pid = protocol + "_" + mode
                    candidate = {"id": pid, "display_name": pid, "aliases": [pid + "alias"],
                                 "endpoint_url": "http://127.0.0.1:%s/%s/%s" % (endpoint.server_port, protocol, mode),
                                 "protocol": protocol, "model": "mock", "api_key_ref": "env:PROVIDER_DISPATCH_TEST_KEY"}
                    request = urllib.request.Request("http://127.0.0.1:%s/api/providers" % registration.server_port,
                        data=json.dumps(candidate).encode(), headers={"Content-Type": "application/json",
                        "Cookie": "conflux_admin=" + ns["_admin_token"]()})
                    with urllib.request.urlopen(request, timeout=6) as response:
                        result = json.load(response)
                    assert result["registered"] and result["persisted"], result
                    assert result["status"] == ("NONATIVETOOLS" if mode == "xml" else "NATIVETOOLS"), result
                    assert pid in ns["display_agents"]()
                    arrow = ">>> " + pid + "alias"
                    recipients = routing.resolve_names(arrow[4:])
                    assert recipients == [pid], recipients
                    mid = "test-" + pid
                    ns["_to_map"][mid] = recipients
                    ns["flush_batch"]([("human", arrow + "\n只需回复，先 read_file 读取 fixture", mid)])
                    assert launches[-1] == pid and replies[-1][0] == pid, (launches, replies)
                    if mode == "fail":
                        assert "HTTP 503" in replies[-1][1], replies[-1]
                    else:
                        assert replies[-1][1] == "reply-complete", replies[-1]
                    print("PASS register -> arrow -> flush -> subprocess ->", protocol, mode)
                data_config = ns["providers_read"]()
                disabled = data_config["providers"][-1]["id"]
                data_config["providers"][-1]["enabled"] = False
                ns["providers_write"](data_config)
                assert disabled not in ns["display_agents"]() and disabled not in ns["dispatch_targets"]()
                before = len(launches)
                ns["flush_batch"]([("human", "disabled", "test-" + disabled)])
                assert len(launches) == before
                ns["_to_map"]["human-only"] = ["human"]
                ns["flush_batch"]([("human", "private", "human-only")])
                assert len(launches) == before
                print("PASS disabled providers and human-only messages are not dispatched")
        finally:
            for server in (endpoint, registration):
                server.shutdown()
                server.server_close()
    print("PASS custom provider registration, routing, native/XML tool loops and explicit HTTP failures")


if __name__ == "__main__":
    main()
