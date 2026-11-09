# -*- coding: utf-8 -*-
"""项目 5-1 工单质量模块：异步命名、缺项追踪、阶段校验、历史兼容。

只读写 ${CONFLUX_WORKSPACE}\\tickets\\*.md 与 ${CONFLUX_ROOT}\\data\\hub\\projects.json，
不删除、不移动任何文件。可独立运行做历史补标：
    python ticket_quality.py backfill
"""
import confluxconfig as cfg
import json
import os
import re
import threading
import time
import urllib.request

TICKETS_DIR = cfg.expand('${CONFLUX_WORKSPACE}/tickets')
PROJECTS_FILE = cfg.root_path('data/hub/projects.json')
CONFIG_FILE = cfg.root_path('data/hub/mimo-name-config.json')
LOG_FILE = cfg.expand('${CONFLUX_WORKSPACE}/ticket-quality-log.jsonl')

# 栏目定义：key → 别名元组（支持多种历史与新模板标题）
SECTIONS = [
    ("name", ("项目名", "工单名")),
    ("goal", ("原始需求", "目标（Goal）", "目标")),
    ("plan", ("技术方案", "方案与分工（Plan & Roles）", "方案与分工")),
    ("log", ("分工与执行记录", "执行记录（Execution Log）", "执行记录")),
    ("acceptance", ("验收", "验收标准（Acceptance）", "验收标准")),
    ("effect", ("最终效果", "风险与回滚（Risk & Rollback）", "项目交付说明", "交付说明", "风险与回滚")),
    ("status", ("状态与驳回", "状态与时间线（Status & Timeline）", "状态与时间线")),
]
_LABELS = {k: titles[0] for k, titles in SECTIONS}
_TITLES = {k: titles for k, titles in SECTIONS}

# 阶段 → 必填栏目（项目名走异步生成，只追踪不阻塞）
STAGE_REQUIRED = {
    "ready": ["goal"],
    "doing": ["goal", "plan"],
    # review = 待验收：此时验收结论尚未产生，不能把 acceptance 当进入条件（否则与“验收只能走 accept 入口”互相锁死）
    "review": ["goal", "plan", "log"],
    "done": ["goal", "plan", "log", "acceptance", "effect"],
}

# 只算"真实内容"的占位词：这些一律视为待填写，不用默认文本伪装完成
_PLACEHOLDERS = ("待填写", "待补", "待命名", "{标题}", "（待补）", "N/A", "占位",
                 "待验收：需补", "待人工补", "待人工处理", "（测试单）")


def _log(entry):
    try:
        with open(LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(dict(entry, ts=time.strftime("%Y-%m-%d %H:%M:%S")),
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


def split_sections(text):
    """按 `## N. 标题` 切栏目，返回 {key: 原始内容}。多种历史与新模板标题都认。"""
    text = text or ""
    out = {k: "" for k, _ in SECTIONS}
    heads = list(re.finditer(r"^##\s*\d+[.、]\s*([^\n]+)$", text, re.M))
    for i, m in enumerate(heads):
        title = m.group(1).strip()
        body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        for key, titles in SECTIONS:
            if any(title.startswith(t) or t in title for t in titles):
                out[key] = body
                break
    return out


def field_state(content):
    """filled=真实内容 / pending=只有占位符 / empty=空。"""
    c = re.sub(r"<!--.*?-->", "", content or "", flags=re.S).strip()
    if not c:
        return "empty"
    lines = [ln.strip() for ln in c.splitlines() if ln.strip()]
    real = []
    for ln in lines:
        body = re.sub(r"^-\s*\S+：", "", ln).strip()
        if not body or any(p in body for p in _PLACEHOLDERS):
            continue
        real.append(body)
    return "filled" if real else "pending"


def missing_report(text):
    """缺项追踪：每栏状态 + 责任人/更新时间标记（不改原值，只报告）。"""
    secs = split_sections(text)
    fields = []
    for key, label in _LABELS.items():
        state = field_state(secs.get(key, ""))
        if state != "filled":
            fields.append({"field": key, "label": label, "state": state})
    return fields


def validate_stage(text, stage):
    """阶段校验：(ok, 缺项标题列表)。缺项时禁止进入该阶段。"""
    need = STAGE_REQUIRED.get(stage)
    if need is None:
        return True, []
    secs = split_sections(text)
    missing = [_LABELS[k] for k in need if field_state(secs.get(k, "")) != "filled"]
    return (not missing), missing


def _ticket_path(tid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", str(tid)):
        raise ValueError("invalid ticket id")
    return os.path.join(TICKETS_DIR, str(tid) + ".md")


def _append_line(tid, key, line):
    """往指定栏目末尾追加一行；不覆盖、不删除原内容。"""
    p = _ticket_path(tid)
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    heads = list(re.finditer(r"^##\s*\d+[.、]\s*([^\n]+)$", text, re.M))
    for i, m in enumerate(heads):
        title = m.group(1).strip()
        if any(t in title for t in _TITLES[key]):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            seg = text[m.end():end].rstrip("\n")
            text = text[:m.end()] + seg + "\n\n" + line + "\n\n" + text[end:]
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return True
    return False


# ================= 异步命名（群外独立 MiMo 接口） =================

_NAME_LOCK = threading.Lock()
_NAME_STATE = {}   # tid -> {"state": "pending|ok|failed", "tries": n, "reason": str, "updated": ts}
NAME_STATE_FILE = cfg.root_path('data/hub/name-state.json')   # 项目 5-1：命名状态持久化，重启可恢复


def _persist_name_state():
    try:
        tmp = NAME_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                      for k, v in _NAME_STATE.items()}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, NAME_STATE_FILE)
    except Exception:
        pass


try:   # 启动时回填上次的命名状态（pending/ok/failed + 原因），断点可查
    with open(NAME_STATE_FILE, encoding="utf-8") as f:
        for _k, _v in json.load(f).items():
            if isinstance(_v, dict):
                _NAME_STATE[str(_k)] = dict(_v)
except Exception:
    pass
_RETRY_BACKOFF = (11, 30, 60)   # 项目 5-1：失败退避 11s/30s/60s，最多 3 次


def name_state(tid):
    return dict(_NAME_STATE.get(str(tid), {"state": "idle", "tries": 0, "reason": ""}))


def title_status_of(tid, content=""):
    """第 1 栏命名状态：ok / pending / failed。没跑过状态机的历史单从内容推断。"""
    st = str(_NAME_STATE.get(str(tid), {}).get("state") or "")
    if st in ("ok", "failed", "pending"):
        return st
    sec = split_sections(content or "").get("name", "")
    c = re.sub(r"<!--.*?-->", "", sec, flags=re.S)
    if "命名失败" in c or "待生成" in c and "（自动命名）" not in c:
        return "failed" if "命名失败" in c else "pending"
    return "ok" if field_state(sec) == "filled" else "pending"


def _set_name_line(tid, line):
    """第 1 栏命名行：同源（- mimo：…）覆盖旧值，无则追加；不删其它内容。"""
    p = _ticket_path(tid)
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    heads = list(re.finditer(r"^##\s*\d+[.、]\s*([^\n]+)$", text, re.M))
    for i, m in enumerate(heads):
        if any(t in m.group(1) for t in _LABELS["name"]):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            lines = text[m.end():end].splitlines(keepends=True)
            replaced = False
            for j, ln in enumerate(lines):
                if re.match(r"^\s*-\s*mimo\s*[：:]", ln):
                    lines[j] = line + "\n"
                    replaced = True
                    break
            if not replaced:
                lines.append("\n" + line + "\n")
            text = text[:m.end()] + "".join(lines) + text[end:]
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return True
    return False


def _mimo_name_call(goal, timeout=20):
    """调用群外 MiMo 接口起名；接口地址由 dsh 在 mimo-name-config.json 配置。"""
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    url, token = cfg.get("url", ""), cfg.get("token", "")
    if not url:
        raise RuntimeError("mimo-name-config.json 未配置 url")
    body = json.dumps({
        "prompt": "给下面这个任务起一个中文项目名，不超过12个字，只输出名字本身：\n%s" % (goal or "")[:500],
        "max_tokens": 256, "timeout": timeout,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token} if token else {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    name = (data.get("name") or data.get("text") or data.get("result") or "").strip()
    name = re.sub(r"^[""'「『]|[""'」』]$", "", name.splitlines()[0].strip()) if name else ""
    return name[:16]


def async_name(tid, goal, tries=4, name_fn=None):
    """异步命名状态机：pending → ok/failed；失败退避 11/30/60s 重试，全程不阻塞建单。
    失败时第 1 栏显式写「待生成」+ 失败原因，成功后覆盖。"""
    tid = str(tid)
    with _NAME_LOCK:
        st = _NAME_STATE.setdefault(tid, {"state": "pending", "tries": 0, "reason": ""})
        if st.get("_running") or st.get("state") == "ok":
            return
        st["_running"] = True
        st["state"] = "pending"
    fn = name_fn or _mimo_name_call
    try:
        for i in range(tries):
            with _NAME_LOCK:
                _NAME_STATE[tid]["tries"] += 1
            try:
                name = fn(goal)
                if not name:
                    raise RuntimeError("空名字")
                _set_name_line(tid, "- mimo：%s（自动命名）" % name)
                with _NAME_LOCK:
                    _NAME_STATE[tid].update(state="ok", reason="", updated=time.time())
                    _persist_name_state()
                _log({"tid": tid, "event": "name_ok", "name": name})
                return
            except Exception as e:
                reason = str(e)[:120]
                _log({"tid": tid, "event": "name_fail", "try": i + 1, "err": reason})
                wait = _RETRY_BACKOFF[min(i, len(_RETRY_BACKOFF) - 1)]
                with _NAME_LOCK:
                    _NAME_STATE[tid].update(state="pending", reason=reason, updated=time.time())
                    _persist_name_state()
                if i + 1 < tries:
                    _set_name_line(tid, "- mimo：待生成（命名失败：%s，%ds 后重试 %d/%d）"
                                   % (reason, wait, i + 2, tries))
                    time.sleep(wait)
                else:
                    _set_name_line(tid, "- mimo：待生成（命名失败：%s，已试 %d 次，等重试调度）"
                                   % (reason, tries))
        with _NAME_LOCK:
            _NAME_STATE[tid].update(state="failed", reason=reason, updated=time.time())
            _persist_name_state()
    finally:
        with _NAME_LOCK:
            _NAME_STATE[tid]["_running"] = False
            _persist_name_state()


def resume_naming(name_fn=None):
    """断点恢复：hub 重启后，把第 1 栏仍是「待生成/命名失败/待命名」的单重新拉起异步命名。"""
    resumed = []
    try:
        files = [f for f in os.listdir(TICKETS_DIR) if f.endswith(".md") and not f.startswith("_")]
    except OSError:
        return resumed
    for fn in files:
        tid = fn[:-3]
        st = _NAME_STATE.get(tid) or {}
        if st.get("state") == "ok" or st.get("_running"):
            continue
        try:
            with open(os.path.join(TICKETS_DIR, fn), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        name_sec = re.sub(r"<!--.*?-->", "", split_sections(text).get("name", ""), flags=re.S)
        if not re.search(r"待生成|命名失败|待命名", name_sec):
            continue
        goal = re.sub(r"^-\s*\S+：\s*", "", split_sections(text).get("goal", "").strip(), flags=re.M).strip()
        threading.Thread(target=async_name, args=(tid, goal), kwargs={"name_fn": name_fn}, daemon=True).start()
        resumed.append(tid)
    return resumed


def spawn_async_name(tid, goal, name_fn=None):
    threading.Thread(target=async_name, args=(tid, goal), kwargs={"name_fn": name_fn}, daemon=True).start()


# ================= 历史兼容 =================

def backfill_history():
    """旧工单只补状态/缺项标记，保留原值；幂等，已补过的跳过。"""
    changed = []
    for fn in sorted(os.listdir(TICKETS_DIR)):
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        tid = fn[:-3]
        p = os.path.join(TICKETS_DIR, fn)
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if "缺项追踪（系统补标" in text:
            continue
        miss = missing_report(text)
        if not miss:
            continue
        marks = "、".join("%s(%s)" % (m["label"], m["state"]) for m in miss)
        ok = _append_line(tid, "status",
                          "- system：缺项追踪（系统补标 %s）：%s" % (time.strftime("%Y-%m-%d %H:%M"), marks))
        if not ok:
            # 缺第 7 栏（或写入失败）时禁止谎报成功：记失败、不改原文件
            _log({"tid": tid, "event": "backfill_failed", "reason": "status_section_missing"})
            continue
        changed.append(tid)
        _log({"tid": tid, "event": "backfill", "missing": [m["label"] for m in miss]})
    return changed


if __name__ == "__main__":
    import sys
    if "backfill" in sys.argv:
        print("backfilled:", backfill_history())
    else:
        print("usage: python ticket_quality.py backfill")
