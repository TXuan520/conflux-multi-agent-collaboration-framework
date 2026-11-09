r"""Exercise actual hub POST routing in isolation, with temporary config and fake probe.

Run python -B ${CONFLUX_ROOT}\scripts\provider-registration-test.py.
AST extraction avoids hub's startup side effects. No running hub or real config is used.
"""

import ast
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def load_hub():
    source = Path(__file__).with_name("hub.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {"providers_read", "providers_write", "_secret_from_ref", "_adapter_protocol",
                 "_admin_ok", "_admin_token", "_host_is_internal", "_normalize_endpoint_url", "_build_api_headers",
                 "display_agents", "make_auto_dispatcher", "dispatch_targets", "ask_custom_provider", "flush_batch", "_deliver_to_target",
                 "agent_enabled_map", "agent_is_enabled", "set_agent_enabled"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions]
    handler = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Handler")
    methods = {"log_message", "_json", "_register_provider", "_probe_provider", "do_POST"}
    handler.body = [node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    nodes.append(handler)
    module = ast.Module(body=nodes, type_ignores=[])
    import routing
    import routing_config as bootstrap
    namespace = dict(globals(), _providers_lock=threading.Lock(), _routing=routing,
                     bootstrap=bootstrap, _ADMIN_TOKEN="", _ADMIN_TOKEN_LOCK=threading.Lock(),
                     AGENT_ENABLED_FILE="", _AGENT_ENABLED={}, _AGENT_Q={}, _AGENT_QLOCK=threading.Lock(), _agent_drain=lambda pid: None)
    exec(compile(module, str(source), "exec"), namespace)
    return namespace


def main():
    namespace = load_hub()
    calls = []
    status = ["NATIVETOOLS"]
    mutation = [None]

    def probe(protocol, endpoint, model, headers=None, timeout=3.0):
        assert timeout == 3.0
        assert namespace["_providers_lock"].acquire(blocking=False)
        namespace["_providers_lock"].release()
        calls.append((protocol, endpoint, model, headers))
        if mutation[0]:
            mutation[0]()
        return SimpleNamespace(status=SimpleNamespace(value=status[0]), elapsed=0.001, detail="mock")

    with tempfile.TemporaryDirectory(prefix="provider-registration-") as temp, \
            patch.object(namespace["bootstrap"].cfg, "WORKSPACE", temp):
        namespace["PROVIDERS_FILE"] = str(Path(temp) / "providers.json")
        namespace["DATA_DIR"] = temp
        namespace["ADMIN_TOKEN_FILE"] = str(Path(temp) / "admin.token")
        namespace["SECRET_DIRS"] = (str(Path(temp) / "secrets") + os.sep,)
        namespace["providers_write"]({"version": 1, "providers": [
            {"id": "existing", "capabilities": {"native_tools": True, "custom": 7}}
        ]})
        server = ThreadingHTTPServer(("127.0.0.1", 0), namespace["Handler"])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def post(path, body, authenticated=True):
            request = urllib.request.Request(
                "http://127.0.0.1:%s%s" % (server.server_port, path),
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                    "Cookie": "conflux_admin=" + (namespace["_admin_token"]() if authenticated else "invalid")})
            try:
                response = urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.code, json.load(response)

        def config():
            return json.loads(Path(namespace["PROVIDERS_FILE"]).read_text(encoding="utf-8"))

        def candidate(pid):
            return {"id": pid, "endpoint_url": "http://127.0.0.1/mock", "protocol": "openaichat",
                    "model": "mock", "api_key_ref": "env:PROVIDER_REGISTRATION_TEST_KEY"}

        try:
            with patch.dict(sys.modules, {"provider_adapter": SimpleNamespace(probe_capability=probe)}), patch.dict(
                    os.environ, {"PROVIDER_REGISTRATION_TEST_KEY": "mock-secret", "HUB_PROBE_ALLOW_PRIVATE": "1"}):
                assert post("/api/providers", candidate("denied"), False)[0] == 403
                assert not calls
                for state in ("NATIVETOOLS", "NONATIVETOOLS", "UNREACHABLE", "AUTH_ERROR"):
                    status[0] = state
                    code, response = post("/api/providers", candidate(state))
                    verified = state in ("NATIVETOOLS", "NONATIVETOOLS")
                    if not verified:
                        assert code == 400 and response["status"] == state
                        assert all(p["id"] != state.lower() for p in config()["providers"])
                        forced = dict(candidate(state), force=True)
                        code, response = post("/api/providers", forced)
                    assert code == 200 and response["registered"] is True
                    assert response["status"] == state and response["persisted"] is True
                    entry = next(p for p in config()["providers"] if p["id"] == response["provider_id"])
                    if verified:
                        assert entry["capabilities"]["native_tools"] == (state == "NATIVETOOLS")
                        assert entry["capabilities"]["xml_react"] == (state == "NONATIVETOOLS")
                    else:
                        assert entry["capabilities"] == {}
                    assert calls[-1][3] == {"Authorization": "Bearer mock-secret"}
                    assert "mock-secret" not in Path(namespace["PROVIDERS_FILE"]).read_text(encoding="utf-8")
                    print("register", state, "persisted=" + str(response["persisted"]))

                before = config()
                for state in ("AUTH_ERROR", "UNREACHABLE"):
                    status[0] = state
                    code, response = post("/api/providers/probe", {"provider_id": "nativetools"})
                    assert code == 200 and response["persisted"] is False and config() == before
                status[0] = "NONATIVETOOLS"
                code, response = post("/api/providers/probe", {"provider_id": "nativetools", "persist": False})
                assert code == 200 and response["persisted"] is False and config() == before
                print("PASS failure preservation and persist=false")

                count = len(calls)
                bad = [dict(candidate("bad"), endpoint_url=""), dict(candidate("bad"), protocol="unknown"),
                       dict(candidate("bad"), api_key_ref="plaintext"),
                       dict(candidate("bad"), capabilities={"native_tools": True}),
                       dict(candidate("bad"), endpoint_url="https://host/path?key=plaintext"),
                       dict(candidate("bad"), aliases="wrong"), [], dict(candidate("bad"), model=1)]
                for body in bad:
                    assert post("/api/providers", body)[0] == 400
                assert post("/api/providers", candidate("NATIVETOOLS"))[0] == 409
                assert len(calls) == count and config() == before
                print("PASS invalid/duplicate registration rejected before probe")

                for remove in (False, True):
                    def change_during_probe():
                        data = config()
                        if remove:
                            data["providers"] = [p for p in data["providers"] if p["id"] != "nativetools"]
                        else:
                            entry = next(p for p in data["providers"] if p["id"] == "nativetools")
                            entry["model"] = "changed-model"
                        namespace["providers_write"](data)

                    mutation[0] = change_during_probe
                    code, response = post("/api/providers/probe", {"provider_id": "nativetools"})
                    assert code == 200 and response["persisted"] is False
                assert config()["providers"][0] == before["providers"][0]
                print("PASS changed/deleted provider rejects stale write; existing provider preserved")
                print("PASS actual POST routing and automatic save-then-probe")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
