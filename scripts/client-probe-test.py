"""Offline desktop API fallback regression, without launching clients or sending traffic."""
import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import runpy
import subprocess
import sys
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent_loop
import unittest
from unittest.mock import Mock, patch

import client_probe
import routing


def load_dispatcher():
    source = Path(__file__).with_name("hub.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "make_auto_dispatcher")
    drain_fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_agent_drain")
    namespace = {
        "threading": threading,
        "_routing": routing,
        "_AGENT_Q": {},
        "_AGENT_QLOCK": threading.Lock(),
        "_agent_drain": lambda pid: None,
        "append_msg": lambda *a, **k: None,
    }
    exec(compile(ast.Module(body=[drain_fn, function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class ClientFallbackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="client-fallback-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "providers.json"
        self.providers = [{"id": pid, "enabled": True, "protocol": "openai_chat",
                           "endpoint_url": "http://127.0.0.1/mock", "model": "mock",
                           "api_key_ref": "env:ISOLATED_PROVIDER_TEST_KEY"}
                          for pid in ("codex", "ag", "dsh", "doubao", "workbuddy")]
        self.path.write_text(json.dumps({"providers": self.providers}), encoding="utf-8")
        self.ns = load_dispatcher()
        self.ns["providers_read"] = lambda: json.loads(self.path.read_text(encoding="utf-8"))
        self.api = Mock(return_value="api-reply")
        self.desktop = Mock(return_value="desktop-reply")
        self.ns["ask_custom_provider"] = self.api
        self.enterContext(patch.object(routing, "PROVIDERS_FILE", str(self.path)))
        self.enterContext(patch.dict(routing._CACHE, {"key": None, "providers": None}))
        self.enterContext(patch.dict(client_probe._PROBE_CACHE, {}, clear=True))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.launch = self.enterContext(patch.object(client_probe, "ensure_client_runtime"))

    def test_offline_clients_use_registered_api_configuration(self):
        # routing.load_providers intentionally returns display metadata only.
        self.assertNotIn("endpoint_url", routing.load_providers()[0])
        for pid in ("codex", "ag", "dsh", "doubao", "workbuddy"):
            with self.subTest(client=pid), patch.dict(client_probe.PROBERS, {
                    pid: lambda: {"runtime_status": "offline", "installed": False}}):
                dispatch = self.ns["make_auto_dispatcher"](pid, self.desktop)
                self.assertEqual(dispatch("original message"), "api-reply")
                self.api.assert_called_once_with(pid, "original message")
                self.desktop.assert_not_called()
                self.launch.assert_not_called()
                self.api.reset_mock()

    def test_desktop_online_keeps_desktop_dispatch(self):
        with patch.dict(client_probe.PROBERS, {
                "workbuddy": lambda: {"runtime_status": "desktop_online"}}):
            dispatch = self.ns["make_auto_dispatcher"]("workbuddy", self.desktop)
            self.assertEqual(dispatch("desktop task"), "desktop-reply")
        self.desktop.assert_called_once_with("desktop task")
        self.api.assert_not_called()
        self.launch.assert_not_called()

    def test_api_failure_does_not_resend_task_to_offline_desktop(self):
        with patch.dict(client_probe.PROBERS, {
                "workbuddy": lambda: {"runtime_status": "offline"}}):
            self.api.return_value = "未完成：provider HTTP 503"
            dispatch = self.ns["make_auto_dispatcher"]("workbuddy", self.desktop)
            self.assertEqual(dispatch("task"), "未完成：provider HTTP 503")
        self.api.assert_called_once_with("workbuddy", "task")
        self.desktop.assert_not_called()
        self.launch.assert_not_called()

    def test_offline_without_api_uses_existing_client_start_path(self):
        self.providers[-1] = {"id": "workbuddy", "enabled": True}
        self.path.write_text(json.dumps({"providers": self.providers}), encoding="utf-8")
        with patch.dict(client_probe.PROBERS, {
                "workbuddy": lambda: {"runtime_status": "desktop_launchable"}}):
            self.launch.return_value = (True, "started")
            dispatch = self.ns["make_auto_dispatcher"]("workbuddy", self.desktop)
            self.assertEqual(dispatch("task"), "desktop-reply")
        self.launch.assert_called_once_with("workbuddy")
        self.desktop.assert_called_once_with("task")
        self.api.assert_not_called()


class RealBuiltinFallbackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="real-client-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data" / "hub"
        self.data.mkdir(parents=True)
        self.path = self.data / "providers.json"
        protocols = {"dsh": "dsh-native", "codex": "codex-cli", "ag": "antigravity",
                     "doubao": "doubao-desktop", "workbuddy": "workbuddy-desktop"}
        self.providers = [{"id": pid, "protocol": protocol, "enabled": True}
                          for pid, protocol in protocols.items()]
        self.path.write_text(json.dumps({"providers": self.providers}), encoding="utf-8")
        self.requests = []
        requests = self.requests

        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                self.send_response(503 if self.path == "/failure" else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                payload = ({"candidates": [{"content": {"parts": [{"text": "real-api-reply"}]}}]}
                           if "contents" in body else
                           {"choices": [{"message": {"role": "assistant", "content": "real-api-reply"}}]})
                self.wfile.write(json.dumps(payload).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%s" % self.server.server_port
        self.process_env = {name: os.environ[name] for name in
                            ("SYSTEMROOT", "SystemRoot", "WINDIR", "APPDATA", "USERPROFILE", "TEMP", "TMP")
                            if name in os.environ}
        self.process_env["NO_PROXY"] = "127.0.0.1,localhost"
        self.enterContext(patch.dict(os.environ, self.process_env, clear=True))
        self.enterContext(patch.object(agent_loop.cfg, "HUB_DATA", str(self.data)))
        self.enterContext(patch.object(routing, "PROVIDERS_FILE", str(self.path)))
        self.enterContext(patch.dict(routing._CACHE, {"key": None, "providers": None}))
        self.enterContext(patch.dict(client_probe._PROBE_CACHE, {}, clear=True))

    def test_real_runner_for_all_five_desktop_protocols_and_key_sources(self):
        for provider in self.providers:
            pid = provider["id"]
            prefix = "CONFLUX_" + pid.upper()
            standard = agent_loop.BUILTIN_API_DEFAULTS[pid][3]
            for key_name in (standard, prefix + "_KEY", pid.upper() + "_API_KEY"):
                with self.subTest(client=pid, key=key_name), patch.dict(os.environ, {
                        **self.process_env, key_name: "test-key", prefix + "_ENDPOINT": self.base + "/" + pid,
                        prefix + "_MODEL": "test-model"}, clear=True):
                    runner = agent_loop.Runner(pid, "reply", str(self.root))
                    ns = load_dispatcher()
                    ns["providers_read"] = lambda: {"providers": self.providers}
                    ns["ask_custom_provider"] = lambda provider_id, text: runner.chat_custom(
                        [{"role": "user", "content": text}])
                    desktop = Mock(side_effect=AssertionError("offline desktop called"))
                    with patch.dict(client_probe._PROBE_CACHE, {}, clear=True), patch.dict(
                            client_probe.PROBERS, {pid: lambda: {"runtime_status": "offline"}}):
                        response = ns["make_auto_dispatcher"](pid, desktop)("original-task")
                    desktop.assert_not_called()
                    self.assertEqual(response["content"], "real-api-reply")
                    path, headers, body = self.requests[-1]
                    self.assertEqual(path, "/" + pid)
                    self.assertNotIn("tools", body)
                    self.assertIn("original-task", json.dumps(body))
                    if pid == "ag":
                        self.assertEqual(headers["x-goog-api-key"], "test-key")
                        self.assertIn("contents", body)
                    else:
                        self.assertEqual(headers["authorization"], "Bearer test-key")
                        self.assertEqual(body["model"], "test-model")

    def test_builtin_defaults_and_environment_precedence(self):
        for provider in self.providers:
            pid = provider["id"]
            config = agent_loop.custom_provider_config(pid, provider)
            protocol, endpoint, model, key = agent_loop.BUILTIN_API_DEFAULTS[pid]
            self.assertEqual(config["protocol"], protocol)
            self.assertEqual(config["endpoint_url"], endpoint.replace("{model}", model))
            self.assertTrue(config["capabilities"]["xml_react"])
        provider = dict(self.providers[0], protocol="anthropic_messages", endpoint_url=self.base,
                        model="persisted", api_key_ref="env:STORED_KEY",
                        capabilities={"native_tools": True})
        with patch.dict(os.environ, {"STORED_KEY": "stored", "CONFLUX_DSH_KEY": "override",
                                    "CONFLUX_DSH_MODEL": "override-model"}):
            config = agent_loop.custom_provider_config("dsh", provider)
            self.assertEqual(config["protocol"], "anthropic_messages")
            self.assertEqual(config["api_key_ref"], "env:CONFLUX_DSH_KEY")
            self.assertEqual(config["model"], "override-model")
            self.assertEqual(config["capabilities"], {"native_tools": True})

    def test_real_http_failure_and_disabled_builtin(self):
        with patch.dict(os.environ, {"CONFLUX_DSH_ENDPOINT": self.base + "/failure",
                                    "DEEPSEEK_API_KEY": "test-key"}):
            runner = agent_loop.Runner("dsh", "reply", str(self.root))
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                runner.chat_custom([{"role": "user", "content": "task"}])
        self.providers[0]["enabled"] = False
        self.path.write_text(json.dumps({"providers": self.providers}), encoding="utf-8")
        count = len(self.requests)
        with self.assertRaisesRegex(ValueError, "禁用"):
            runner.chat_custom([{"role": "user", "content": "task"}])
        self.assertEqual(len(self.requests), count)

    def test_provider_get_reports_arch_and_mimo_credential_configuration(self):
        from urllib.parse import urlparse
        source = Path(__file__).with_name("hub.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        ready = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "api_provider_ready")
        handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler")
        get = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == "do_GET")
        # Execute the real provider GET branch, excluding unrelated handlers and startup effects.
        branch = next(n for n in ast.walk(get) if isinstance(n, ast.If) and
                      isinstance(n.test, ast.Compare) and any(isinstance(v, ast.Constant)
                      and v.value == "/api/providers" for v in ast.walk(n.test)))
        get.body = [get.body[0], branch]
        credentials = self.root / "credentials"
        credentials.mkdir()
        mimo = credentials / "mimo.key"
        vertex = credentials / "vertex.key"
        mimo.write_text("test-mimo", encoding="utf-8")
        vertex.write_text("test-vertex", encoding="utf-8")
        ns = {"os": os, "urlparse": urlparse, "_providers_lock": threading.Lock(),
              "cfg": SimpleNamespace(root_path=lambda name: str(mimo)),
              "providers_read": lambda: {"providers": [{"id": "arch"}, {"id": "mimo"}]},
              "_adapter_protocol": lambda value: "", "_secret_from_ref": lambda ref: "", "agent_enabled_map": lambda: {}}
        exec(compile(ast.Module(body=[ready, get], type_ignores=[]), str(source), "exec"), ns)
        captured = []
        instance = SimpleNamespace(path="/api/providers", _json=captured.append)
        fake_vertex = SimpleNamespace(API_KEY_FILE=str(vertex), ADC=str(vertex), USE_API_KEY=True)
        with patch.dict(sys.modules, {"vertex_client": fake_vertex}), patch.object(
                client_probe, "probe_all_clients", return_value={}):
            ns["do_GET"](instance)
            self.assertEqual({p["id"]: p["runtime_status"] for p in captured[-1]["providers"]},
                             {"arch": "api_online", "mimo": "api_online"})
            mimo.unlink()
            vertex.unlink()
            ns["do_GET"](instance)
            self.assertEqual({p["id"]: p["runtime_status"] for p in captured[-1]["providers"]},
                             {"arch": "offline", "mimo": "offline"})

    def test_dsh_flush_reaches_real_subprocess_api_and_retains_desktop_parameters(self):
        scripts = Path(__file__).resolve().parent
        ns = runpy.run_path(str(scripts / "provider-registration-test.py"))["load_hub"]()
        from broadcast_policy import BroadcastPolicy
        ns['_broadcast_policy'] = BroadcastPolicy(lambda *args: ns['_deliver_to_target'](*args))
        replies = []
        inject = Mock(return_value=True)
        ns["providers_read"] = lambda: json.loads(self.path.read_text(encoding="utf-8"))
        ns.update(DATA_DIR=str(self.data), PROVIDERS_FILE=str(self.path), subprocess=subprocess,
                  cfg=SimpleNamespace(optional_env=lambda name: sys.executable,
                      root_path=lambda name: str(scripts / "agent_loop.py"), WORKSPACE=str(self.root)),
                  DISPLAY_AGENTS=("codex", "ag", "dsh", "mimo", "arch", "doubao", "workbuddy"),
                  AGENTS=(), AGENT_LABELS={}, ROUNDTABLE_ENABLED=False,
                  _to_map={"api-task": ["dsh"], "desktop-task": ["dsh"]},
                  _agent_current_tasks={}, read_msgs=lambda since: [],
                  activity=lambda *a, **k: None, mark=lambda *a: None,
                  append_msg=lambda sender, text: replies.append((sender, text)),
                  upsert_task=lambda *a: None, is_no_reply_needed=lambda text: False,
                  shrink_handoff=lambda text, sender: text, msg_stamp=lambda mid: "test",
                  dispatch_once=lambda *a, **k: True, push_expecting_target=lambda *a: None,
                  _brainstorm_dispatch=lambda fn, target, mids: fn, inject_qq=inject)
        for name in ("ask_codex", "ask_antigravity", "ask_mimo", "ask_arch", "ask_doubao", "ask_workbuddy"):
            ns[name] = Mock(side_effect=AssertionError("unexpected desktop dispatch"))
        with patch.dict(os.environ, {"CONFLUX_ROOT": str(self.root), "CONFLUX_DATA_ROOT": str(self.root / "data"),
                "CONFLUX_DATA_DIR": str(self.data), "CONFLUX_WORKSPACE": str(self.root),
                "CONFLUX_DSH_ENDPOINT": self.base + "/dsh", "DEEPSEEK_API_KEY": "test-key",
                "PYTHONIOENCODING": "utf-8"}), patch.object(client_probe, "get_client_status", return_value={"runtime_status": "offline", "api_fallback": True}):
            ns["flush_batch"]([("human", ">>> DeepSeek\n只需回复", "api-task")])
        self.assertEqual(replies, [("dsh", "real-api-reply")])
        inject.assert_not_called()
        with patch.object(client_probe, "get_client_status", return_value={"runtime_status": "desktop_online"}):
            ns["flush_batch"]([("codex", ">>> DeepSeek\nreview", "desktop-task")])
        self.assertEqual(inject.call_count, 1)
        self.assertEqual(inject.call_args.kwargs, {"reply_to": ["codex"], "source": "agent"})


if __name__ == "__main__":
    result = unittest.main(exit=False)
    if not result.result.wasSuccessful():
        raise SystemExit(1)
    print("PASS real five-client Runner HTTP fallback, DSH flush/subprocess and desktop reply routing")
