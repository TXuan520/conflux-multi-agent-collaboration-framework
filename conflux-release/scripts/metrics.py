#!/usr/bin/env python3
"""Collect collaboration-context and response-latency metrics.

Run once (safe for Task Scheduler):  python metrics.py
Run continuously:                 python metrics.py --loop
"""
from __future__ import annotations
import confluxconfig as cfg

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(cfg.root_path(''))
HUB = Path(cfg.HUB_DATA)
OUTPUT = HUB / "metrics.jsonl"
STATE = HUB / "metrics-alert-state.json"
MESSAGES = HUB / "messages.jsonl"
GEMINI_ROOT = Path(os.environ.get("USERPROFILE", cfg.home_path(''))) / ".gemini" / "antigravity" / "brain"
CODEX_ROOT = Path(os.environ.get("USERPROFILE", cfg.home_path(''))) / ".codex" / "sessions"

# Deliberately conservative defaults.  Environment variables make rollout tuning
# possible without editing the collector.
GEMINI_CACHE_LIMIT = int(os.environ.get("METRICS_GEMINI_CACHE_LIMIT", "80000"))
GEMINI_INPUT_LIMIT = int(os.environ.get("METRICS_GEMINI_INPUT_LIMIT", "12000"))
CODEX_LINES_LIMIT = int(os.environ.get("METRICS_CODEX_LINES_LIMIT", "5000"))
CODEX_BYTES_LIMIT = int(os.environ.get("METRICS_CODEX_BYTES_LIMIT", str(10 * 1024 * 1024)))


def json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def newest(paths: Iterable[Path]) -> Path | None:
    candidates = []
    for path in paths:
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            pass
    return max(candidates, default=(0, None))[1]


def gemini_metrics() -> dict[str, Any]:
    transcript = newest(GEMINI_ROOT.glob("**/.system_generated/logs/transcript.jsonl"))
    result: dict[str, Any] = {"transcript": str(transcript) if transcript else None,
                              "input_tokens": None, "cache_read_tokens": None,
                              "output_tokens": None, "step_index": None}
    if not transcript:
        return result
    # Scan newest-to-oldest in memory so an incomplete trailing record cannot hide
    # the most recent planner response containing usage numbers.
    records = list(json_lines(transcript))
    for record in reversed(records):
        if any(key in record for key in ("input_tokens", "cache_read_tokens", "output_tokens")):
            result.update({key: record.get(key) for key in ("input_tokens", "cache_read_tokens", "output_tokens")})
            result["step_index"] = record.get("step_index")
            break
    return result


def codex_metrics() -> dict[str, Any]:
    rollout = newest(CODEX_ROOT.glob("**/rollout-*.jsonl"))
    if not rollout:
        return {"rollout": None, "lines": 0, "bytes": 0}
    try:
        with rollout.open("rb") as fh:
            lines = sum(1 for _ in fh)
        size = rollout.stat().st_size
    except OSError:
        lines = size = 0
    return {"rollout": str(rollout), "lines": lines, "bytes": size}


def response_latency_ms(messages: list[dict[str, Any]], agent: str) -> dict[str, Any]:
    """Pair an agent reply with the newest unpaired message addressed to it."""
    pending: list[float] = []
    samples: list[int] = []
    for message in sorted(messages, key=lambda item: float(item.get("ts", 0))):
        sender, targets = message.get("sender"), message.get("to") or []
        ts = message.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if sender != agent and agent in targets:
            pending.append(float(ts))
        elif sender == agent and pending:
            # The latest prompt is the relevant one when a sender batches messages.
            started = pending.pop()
            elapsed = int((float(ts) - started) * 1000)
            if 0 <= elapsed <= 24 * 60 * 60 * 1000:
                samples.append(elapsed)
            pending.clear()
    return {"median_ms": int(statistics.median(samples)) if samples else None,
            "samples": len(samples)}


def reasons(gemini: dict[str, Any], codex: dict[str, Any]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    cache = gemini.get("cache_read_tokens")
    input_tokens = gemini.get("input_tokens")
    if isinstance(cache, int) and cache >= GEMINI_CACHE_LIMIT:
        found.append(("gemini", str(gemini.get("transcript")), f"cache_read={cache} ≥ {GEMINI_CACHE_LIMIT}"))
    elif isinstance(input_tokens, int) and input_tokens >= GEMINI_INPUT_LIMIT:
        found.append(("gemini", str(gemini.get("transcript")), f"input={input_tokens} ≥ {GEMINI_INPUT_LIMIT}"))
    if codex["lines"] >= CODEX_LINES_LIMIT:
        found.append(("codex", str(codex.get("rollout")), f"rollout lines={codex['lines']} ≥ {CODEX_LINES_LIMIT}"))
    elif codex["bytes"] >= CODEX_BYTES_LIMIT:
        found.append(("codex", str(codex.get("rollout")), f"rollout bytes={codex['bytes']} ≥ {CODEX_BYTES_LIMIT}"))
    return found


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"alerted_sessions": []}


def report_alert(agent: str, reason: str) -> None:
    payload = json.dumps({"from": "watch", "to": ["human", "dsh", "ag", "codex"],
                          "text": f"建议新开对话，原因：{agent} {reason}"}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request("http://127.0.0.1:8777/api/send", data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=3).read()
    except (urllib.error.URLError, TimeoutError, OSError):
        # Metrics remains useful if the UI is not running; the alert is retained in
        # the JSONL record and will be eligible to send again next invocation.
        raise


def collect() -> dict[str, Any]:
    HUB.mkdir(parents=True, exist_ok=True)
    gemini, codex = gemini_metrics(), codex_metrics()
    messages = list(json_lines(MESSAGES))
    latencies = {agent: response_latency_ms(messages, agent) for agent in ("ag", "codex", "dsh")}
    state = load_state()
    already = set(state.get("alerted_sessions", []))
    alerts: list[dict[str, str]] = []
    for agent, session, reason in reasons(gemini, codex):
        key = f"{agent}:{session}"
        if key not in already:
            try:
                report_alert(agent, reason)
            except OSError:
                continue
            already.add(key)
            alerts.append({"agent": agent, "reason": reason, "session": session})
    state["alerted_sessions"] = sorted(already)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    row = {"ts": time.time(), "collected_at": datetime.now(timezone.utc).isoformat(),
           "gemini": gemini, "codex": codex, "latency": latencies, "alerts": alerts}
    with OUTPUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="collect immediately and every hour")
    args = parser.parse_args()
    while True:
        row = collect()
        print(json.dumps(row, ensure_ascii=False))
        if not args.loop:
            return
        time.sleep(3600)


if __name__ == "__main__":
    main()
