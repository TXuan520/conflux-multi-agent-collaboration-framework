# -*- coding: utf-8 -*-
"""发布门禁：Git 推送前扫泄露 + 跑回归。

用法：
    python release-gate.py --scan ./release          # 只扫泄露
    python release-gate.py --scan ./release --tests   # 扫完再跑回归
    python release-gate.py --scan <目录> --json               # 机器可读输出

退出码：0 全绿；1 有阻断项；2 参数错误。
"""
import argparse
import json
import os
import re
import subprocess
import sys

# 阻断级泄露模式：(标签, 正则, 说明)
SECRET_PATTERNS = [
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "明文 API Key"),
    ("vertex-key", re.compile(r"\bAQ\.[A-Za-z0-9_\-]{20,}"), "Vertex Express Key"),
    ("gcp-token", re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"), "Google OAuth Token"),
    ("github-token", re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "GitHub Token"),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"), "Google API Key"),
    ("onebot-token", re.compile(r"\b[0-9a-f]{40}\b"), "疑似 OneBot/NapCat 令牌（40 位十六进制）"),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥块"),
    ("ecs-ip", re.compile(r"\b8\.133\.163\.167\b"), "云端服务器 IP"),
    ("qq-bot", re.compile(r"\b3819386926\b"), "机器人 QQ 号"),
    ("qq-owner", re.compile(r"\b1039361080\b"), "主人 QQ 号"),
    ("win-user", re.compile(r"C:[\\/]{1,2}Users[\\/]{1,2}10393"), "本机 Windows 用户名"),
    ("feiyu-path", re.compile(r"D:[\\/]{1,2}feiyu"), "本机绝对路径"),
    # 移动端与部署侧的敏感项（Argon 09:49 补充）
    ("cert-pin", re.compile(r"(?i)(sha-?256|pin(ning)?)[^\n]{0,40}[0-9a-f]{64}"), "证书指纹钉扎"),
    ("relay-url", re.compile(r"8\.133\.163\.167|rela[y][^\n]{0,20}8443"), "中继地址与端口"),
    ("signing-key", re.compile(r"(?i)(storePassword|keyPassword|keystore)\s*[=:]\s*\S+"), "签名口令"),
    ("gradle-local", re.compile(r"sdk\.dir\s*="), "本机 Android SDK 路径"),
]
BLOCK_FILES = {"local.properties", "keystore.properties", "relay-cert.sha256", "admin.token"}
# 命中也不一定阻断的路径（测试夹具、示例文件）
ALLOW_HINTS = ("example", ".sample", "fixture", "test-", "_test")
SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "stt", "napcat", "logs",
             ".cdp-profile", "mimo-profile", "tool_cache"}
SKIP_EXT = {".pyc", ".pyo", ".bin", ".class", ".jar", ".aar", ".dex", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".mp3", ".wav",
            ".flac", ".mp4", ".zip", ".7z", ".exe", ".dll", ".so", ".db", ".sqlite", ".cookie"}
MAX_FILE_MB = 5
MAX_TREE_MB = 60


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def scan(root):
    findings, total_bytes, big = [], 0, []
    for path in iter_files(root):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        total_bytes += size
        rel = os.path.relpath(path, root)
        release_rel = rel.replace("\\", "/").lower()
        is_allowed_hub_asset = (
            release_rel in ("data/hub/index.html", "data/hub/providers.example.json",
                            "data/hub/sentence-backbone.js", "data/hub/sw.js",
                            "data/hub/conflux.ico", "data/hub/icon.ico", "data/hub/icon.png") or
            release_rel.startswith("data/hub/wallpapers/")
        )
        if release_rel.startswith("data/hub/") and not is_allowed_hub_asset:
            findings.append({"file": rel, "line": 0, "kind": "runtime-artifact",
                             "why": "发布包 data/hub 仅保留页面和配置示例", "sample": os.path.basename(path)})
        if os.path.basename(path).lower() in BLOCK_FILES:
            findings.append({"file": rel, "line": 0, "kind": "blocked-file",
                             "why": "本机私有配置文件不入库", "sample": os.path.basename(path)})
        if "\\build\\" in rel.lower() or ".gradle" in rel.lower():
            continue
        if size > MAX_FILE_MB * 1024 * 1024:
            big.append((rel, round(size / 1048576, 1)))
        if os.path.splitext(path)[1].lower() in SKIP_EXT:
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    if len(line) > 4000:
                        line = line[:4000]
                    for label, pat, why in SECRET_PATTERNS:
                        m = pat.search(line)
                        if not m:
                            continue
                        if label != "feiyu-path" and any(h in rel.lower() for h in ALLOW_HINTS):
                            continue
                        findings.append({"file": rel, "line": lineno, "kind": label,
                                         "why": why, "sample": m.group(0)[:12] + "…"})
                        break
        except OSError:
            continue
    return {"root": root, "files_scanned": True, "findings": findings,
            "total_mb": round(total_bytes / 1048576, 1), "big_files": big}


def run_tests(scripts_dir):
    suites = ["test_dynamic_routing.py", "test_tool_output.py",
              "file-read-safety-test.py", "guard-negative-test.py",
              "provider-registration-test.py", "provider-dispatch-test.py", "client-probe-test.py", "test-recipient-batching.py", "test-broadcast-mode.py", "provider_adapter.py"]
    results = []
    for name in suites:
        path = os.path.join(scripts_dir, name)
        if not os.path.exists(path):
            results.append({"suite": name, "status": "missing"})
            continue
        try:
            r = subprocess.run([sys.executable, path] + (["--self-test"] if name == "provider_adapter.py" else []), capture_output=True, text=True,
                               timeout=300, encoding="utf-8", errors="ignore")
            # unittest writes its summary to stderr. The exit code determines success.
            diagnostic = (r.stderr or r.stdout) if r.returncode else (r.stdout or r.stderr)
            tail = (diagnostic or "").strip().splitlines()[-1:] or [""]
            results.append({"suite": name, "status": "pass" if r.returncode == 0 else "fail",
                            "returncode": r.returncode, "tail": tail[0][:120]})
        except Exception as exc:
            results.append({"suite": name, "status": "error", "tail": str(exc)[:120]})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True, help="待发布目录")
    ap.add_argument("--tests", action="store_true", help="顺带跑回归")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--allow-size", action="store_true", help="忽略体积超限")
    args = ap.parse_args()
    if not os.path.isdir(args.scan):
        print("目录不存在：%s" % args.scan)
        return 2

    rep = scan(args.scan)
    tests = run_tests(os.path.dirname(os.path.abspath(__file__))) if args.tests else []
    blocked = (bool(rep["findings"]) or (not args.allow_size and rep["total_mb"] > MAX_TREE_MB)
               or any(t.get("status") != "pass" for t in tests))
    if args.json:
        rep["tests"] = tests
        rep["blocked"] = blocked
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 1 if blocked else 0

    print("扫描目录：%s" % rep["root"])
    print("总体积：%.1f MB（上限 %d MB）" % (rep["total_mb"], MAX_TREE_MB))
    if rep["findings"]:
        print("\n阻断项 %d 条：" % len(rep["findings"]))
        for f in rep["findings"][:40]:
            print("  [%s] %s:%d  %s  样本=%s" % (f["kind"], f["file"], f["line"], f["why"], f["sample"]))
        if len(rep["findings"]) > 40:
            print("  …… 其余 %d 条省略" % (len(rep["findings"]) - 40))
    else:
        print("泄露扫描：无命中")
    if rep["big_files"]:
        print("\n大文件（>%dMB，确认是否该入库）：" % MAX_FILE_MB)
        for name, mb in rep["big_files"][:10]:
            print("  %s  %.1f MB" % (name, mb))
    if tests:
        print("\n回归：")
        for t in tests:
            print("  %-28s %s  %s" % (t["suite"], t.get("status"), t.get("tail", "")))
        if any(t.get("status") != "pass" for t in tests):
            blocked = True
    print("\n结论：%s" % ("BLOCKED 不可发布" if blocked else "PASS 可以发布"))
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
