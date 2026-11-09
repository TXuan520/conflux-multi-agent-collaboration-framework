"""Portable configuration shared by Conflux scripts. Never supplies secret defaults."""

import os
from pathlib import Path
import shutil
import sys


_PROJECT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """读项目根目录的 .env；已存在的环境变量优先，不覆盖。"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(_PROJECT_DIR / ".env")

ROOT = str(Path(os.environ.get("CONFLUX_ROOT") or _PROJECT_DIR).resolve())
if not Path(ROOT).is_dir():
    raise SystemExit("CONFLUX_ROOT must name an existing project directory")
DATA_ROOT = os.path.abspath(os.environ.get("CONFLUX_DATA_ROOT") or os.path.join(ROOT, "data"))
HUB_DATA = os.path.abspath(os.environ.get("CONFLUX_DATA_DIR") or os.path.join(DATA_ROOT, "hub"))
WORKSPACE = os.path.abspath(os.environ.get("CONFLUX_WORKSPACE") or str(Path(ROOT).parent / "cooperation"))
USER_HOME = str(Path.home())

for _auto_dir in (DATA_ROOT, HUB_DATA, os.path.join(DATA_ROOT, "group-files"),
                  WORKSPACE, os.path.join(WORKSPACE, "tickets")):
    try:
        os.makedirs(_auto_dir, exist_ok=True)
    except OSError:
        pass


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit("Missing required environment variable " + name)
    return value


def qq_id(name):
    value = require_env(name)
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise SystemExit(name + " must be a positive QQ number")
    return int(value)


def optional_env(name, default=""):
    """可选扩展用的取值：没配就返回空，不让核心功能因为缺 QQ/中继配置起不来。"""
    return os.environ.get(name, default).strip()


def optional_qq_id(name, default=0):
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise SystemExit(name + " must be a positive QQ number")
    return int(value)


def optional_relay_host():
    host = os.environ.get("CONFLUX_RELAY_HOST", "").strip()
    if not host:
        return ""
    if any(char in host for char in "/\\:@?# \r\n"):
        raise SystemExit("CONFLUX_RELAY_HOST must be a hostname or IPv4 address")
    return host


def root_path(relative=""):
    parts = relative.replace("\\", "/").split("/")
    if parts[:2] == ["data", "hub"]:
        return os.path.join(HUB_DATA, *parts[2:])
    if parts[:1] == ["data"]:
        return os.path.join(DATA_ROOT, *parts[1:])
    return os.path.join(ROOT, *parts) if relative else ROOT


def home_path(relative=""):
    return os.path.join(USER_HOME, *relative.replace("\\", "/").split("/"))


def expand(text):
    return text.replace("${CONFLUX_ROOT}", ROOT).replace("${CONFLUX_WORKSPACE}", WORKSPACE).replace(
        "${CONFLUX_USER_HOME}", USER_HOME)


def relay_host():
    host = require_env("CONFLUX_RELAY_HOST")
    if any(char in host for char in "/\\:@?# \r\n"):
        raise SystemExit("CONFLUX_RELAY_HOST must be a hostname or IPv4 address")
    return host


def relay_url(scheme="wss", path="/host"):
    port = os.environ.get("CONFLUX_RELAY_PORT", "8443")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise SystemExit("CONFLUX_RELAY_PORT must be between 1 and 65535")
    return "%s://%s:%s%s" % (scheme, relay_host(), port, path)


def codex_exe():
    configured = os.environ.get("CONFLUX_CODEX_EXE")
    if configured:
        found = shutil.which(configured)
    else:
        candidates = sorted(Path(home_path("AppData/Local/OpenAI/Codex/bin")).glob("*/codex.exe"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        found = str(candidates[0]) if candidates else shutil.which("codex")
    if not found:
        raise SystemExit("Codex CLI missing. Set CONFLUX_CODEX_EXE to an installed executable")
    return found


def python_exe(windowless=False):
    name = "CONFLUX_PYTHONW" if windowless else "CONFLUX_PYTHON"
    configured = os.environ.get(name)
    exe = "pythonw.exe" if windowless else "python.exe"
    candidate = configured or root_path("stt/venv/Scripts/" + exe)
    if os.path.isfile(candidate):
        return candidate
    if configured:
        raise SystemExit(name + " must name an installed interpreter")
    found = shutil.which(exe)
    if not found and not windowless:
        found = sys.executable
    if not found:
        raise SystemExit("Python missing. Set " + name + " to an installed interpreter")
    return found
