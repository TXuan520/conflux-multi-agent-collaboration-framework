# -*- coding: utf-8 -*-
"""从 Codex 会话 rollout 里抽真实 token 用量（含缓存命中），按天汇总。

数据源：~/.codex/sessions/**/rollout-*.jsonl 里的 token_usage_record。
输出：data/hub/usage.jsonl（逐条）+ 控制台按天汇总。
"""
import confluxconfig as cfg
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

OUT = cfg.root_path('data/hub/usage.jsonl')


def iter_records():
    root = os.path.expanduser(r"~\.codex\sessions")
    for f in sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)):
        model = ""
        with open(f, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                t = o.get("type")
                if t == "turn_context":
                    p = o.get("payload") or {}
                    model = p.get("model") or model
                if t != "token_usage_record":
                    continue
                p = o.get("payload") or {}
                u = p.get("usage") or {}
                if not u:
                    continue
                yield {"ts": o.get("timestamp"),
                       "session": p.get("session_id"),
                       "model": model,
                       "input": u.get("input_tokens") or 0,
                       "cached": u.get("cached_input_tokens") or 0,
                       "output": u.get("output_tokens") or 0,
                       "reasoning": u.get("reasoning_output_tokens") or 0,
                       "total": u.get("total_tokens") or 0}


def main():
    rows = list(iter_records())
    if not rows:
        print("没有找到 token_usage_record")
        return
    by_day = defaultdict(lambda: {"calls": 0, "input": 0, "cached": 0, "output": 0, "total": 0, "models": set()})
    for r in rows:
        try:
            day = r["ts"][:10]
        except Exception:
            day = "unknown"
        d = by_day[day]
        d["calls"] += 1
        d["input"] += r["input"]
        d["cached"] += r["cached"]
        d["output"] += r["output"]
        d["total"] += r["total"]
        if r["model"]:
            d["models"].add(r["model"])
    for day in sorted(by_day):
        d = by_day[day]
        rate = (d["cached"] / d["input"] * 100) if d["input"] else 0
        print("%s  调用 %4d  输入 %9d  命中缓存 %9d (%4.1f%%)  输出 %6d  合计 %9d  %s"
              % (day, d["calls"], d["input"], d["cached"], rate, d["output"], d["total"],
                 ",".join(sorted(d["models"]))[:40]))
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("明细已写:", OUT)


if __name__ == "__main__":
    main()
