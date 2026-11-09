# -*- coding: utf-8 -*-
"""工单流程：四栏极简状态机 + 群聊原生分发 + 成员全员交卷汇聚屏障 + 总管循环返工 + 用户验收同单续跑。

四栏顺序：
  1. name     ① 项目名称
  2. goal     ② 用户要求（用户初始要求 + 用户驳回重做时追加要求）
  3. plan     ③ 方案与分工（总管制定方案并用 >>> 成员 指派，登记 pending_assignees）
  4. delivery ④ 验收与交付（全员交卷汇聚后唤醒总管；总管可局部驳回 >>> 成员返工，或验收通过进入 pending_acceptance 待用户最终验收）
"""
import json
import os
import re
import threading
import time
import uuid

STAGES = [
    ("name", "项目名称"),
    ("goal", "用户要求"),
    ("plan", "方案与分工"),
    ("delivery", "验收与交付"),
]
STAGE_KEYS = [k for k, _ in STAGES]
STAGE_LABEL = dict(STAGES)
FLOW = ["name", "plan", "delivery"]
USER_STAGE = "goal"
STAGE_TIMEOUT = 300

ALIAS = {
    "gpt": "codex",
    "gemini": "ag",
    "deepseek": "dsh",
    "seedance": "doubao",
    "argon": "arch",
    "mimo": "mimo",
    "workbuddy": "workbuddy",
}

KNOWN_AGENTS = {"codex", "ag", "dsh", "doubao", "arch", "mimo", "workbuddy"}

_STORE_LOCK = threading.RLock()


def _norm(name):
    n = (name or "").strip().lower()
    return ALIAS.get(n, n)


def _extract_mentions(text, exclude=None):
    """从文本的独立行 >>> 成员 提取被指派成员列表（去重且保序）。"""
    exclude_set = {_norm(x) for x in (exclude or []) if x}
    found = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*>>>\s*([a-zA-Z0-9_-]+)\s*$", line.strip())
        if not m:
            continue
        ag = _norm(m.group(1))
        if ag in KNOWN_AGENTS and ag not in exclude_set and ag not in found:
            found.append(ag)
    return found


def project_name(text):
    """Extract the AI summary without transport markers or Markdown labels."""
    text = re.sub(r"\[#[-\w]+:name\]", "", text or "")
    text = re.sub(r"【[^】\n]+】", "", text)
    for line in text.splitlines():
        line = re.sub(r"^[\s#>*-]+", "", line).replace("**", "").strip()
        line = re.sub(r"^(?:项目名称|项目名|名称)\s*[:：]\s*", "", line)
        line = re.sub(r"^项目\s*[-－—]\s*", "", line).strip(' \"「」『』`')
        if line:
            return line[:40]
    return ""


def _now():
    return time.time()


REJECT_WORDS = (
    "验收不通过", "驳回", "打回", "FAIL", "fail", "不通过",
    "初审不通过", "初审驳回", "终审不通过", "终审驳回", "二审不通过", "二审驳回"
)
PASS_WORDS = (
    "验收通过", "通过", "同意", "合格", "PASS", "pass", "完成",
    "初审通过", "终审通过", "二审通过"
)

DEFAULT_ROLES = {
    "supervisor": "arch",
    "workers": ["dsh", "codex"],
    "auditor": "ag",
}


def _delivery_verdict(output):
    """delivery 环节判定：reject / pass。先判驳回词。"""
    text = output or ""
    if any(k in text for k in REJECT_WORDS):
        return "reject"
    if any(k in text for k in PASS_WORDS):
        return "pass"
    return "pass"


class TicketFlow:
    def __init__(self, data_dir, tickets_dir):
        self.data_dir = data_dir
        self.tickets_dir = tickets_dir
        self.config_path = os.path.join(data_dir, "ticket-flow-config.json")
        self._lock = _STORE_LOCK
        os.makedirs(self.tickets_dir, exist_ok=True)

    def is_agent_enabled(self, name):
        """检查智能体是否处于启用状态。默认全部启用。"""
        if not name:
            return False
        _n = _norm(name)
        try:
            path = os.path.join(self.data_dir, "agent_enabled.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    m = json.load(f)
                if isinstance(m, dict):
                    return bool(m.get(_n, True))
        except Exception:
            pass
        return True

    # ---------- 配置 ----------
    def config(self):
        try:
            with open(self.config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        defaults = cfg.get("defaults") or {}
        out = {}
        for k in STAGE_KEYS:
            agents = defaults.get(k) or []
            out[k] = [a for a in agents if isinstance(a, str) and a]
        roles_in = cfg.get("roles") or {}
        roles_out = {
            "supervisor": _norm(roles_in.get("supervisor") or DEFAULT_ROLES["supervisor"]),
            "workers": [_norm(w) for w in (roles_in.get("workers") or DEFAULT_ROLES["workers"]) if w],
            "auditor": _norm(roles_in.get("auditor") or DEFAULT_ROLES["auditor"]),
        }
        return {"defaults": out, "roles": roles_out}

    def set_config(self, payload):
        with self._lock:
            cfg = self.config()
            incoming = (payload or {}).get("defaults") or {}
            for k in STAGE_KEYS:
                if k in incoming:
                    agents = incoming.get(k) or []
                    cfg["defaults"][k] = [a for a in agents if isinstance(a, str) and a]
            if "roles" in (payload or {}):
                r_in = (payload or {}).get("roles") or {}
                cfg["roles"] = {
                    "supervisor": _norm(r_in.get("supervisor") or cfg.get("roles", {}).get("supervisor") or DEFAULT_ROLES["supervisor"]),
                    "workers": [_norm(w) for w in (r_in.get("workers") or cfg.get("roles", {}).get("workers") or DEFAULT_ROLES["workers"]) if w],
                    "auditor": _norm(r_in.get("auditor") or cfg.get("roles", {}).get("auditor") or DEFAULT_ROLES["auditor"]),
                }
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return cfg

    # ---------- 工单 ----------
    def _path(self, tid):
        return os.path.join(self.tickets_dir, "%s.json" % tid)

    def load(self, tid):
        with self._lock:
            try:
                with open(self._path(tid), encoding="utf-8") as f:
                    ticket = json.load(f)
                    self._sync_name(ticket)
                    return ticket
            except Exception:
                return None

    def save(self, ticket):
        with self._lock:
            ticket["updated_at"] = _now()
            tmp = "%s.%s.%s.tmp" % (self._path(ticket["id"]), threading.get_ident(), uuid.uuid4().hex[:6])
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ticket, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path(ticket["id"]))
            return ticket

    def list(self, limit=100):
        with self._lock:
            items = []
            try:
                names = [n for n in os.listdir(self.tickets_dir) if n.endswith(".json")]
            except Exception:
                return []
            for n in names:
                try:
                    with open(os.path.join(self.tickets_dir, n), encoding="utf-8") as f:
                        ticket = json.load(f)
                        self._sync_name(ticket)
                        items.append(ticket)
                except Exception:
                    continue
            items.sort(key=lambda t: t.get("updated_at") or 0, reverse=True)
            return items[:limit]

    def agents_for(self, ticket, stage):
        override = (ticket.get("stage_agents") or {}).get(stage) or []
        candidates = []
        if override:
            candidates = list(override)
        elif stage in ("plan", "delivery") and ticket.get("supervisor"):
            candidates = [ticket["supervisor"]]
        else:
            candidates = list((self.config()["defaults"] or {}).get(stage) or [])
        
        # 优先保留在岗已启用的智能体
        enabled_cands = [a for a in candidates if self.is_agent_enabled(a)]
        if enabled_cands:
            return enabled_cands
        
        # 若原定智能体已被用户停用，安全降级到当前在岗可用的智能体，避免工单挂死
        for fallback in ("dsh", "mimo", "arch", "ag", "codex"):
            if self.is_agent_enabled(fallback):
                return [fallback]
        return candidates

    def create(self, goal, title=None, stage_agents=None, supervisor=None, roles=None):
        with self._lock:
            tid = uuid.uuid4().hex[:8]
            clean_title = (title or "").strip()
            stage_agents = {k: [a for a in (v or []) if a] for k, v in (stage_agents or {}).items() if k in STAGE_KEYS}
            cfg_roles = (self.config().get("roles") or DEFAULT_ROLES)
            r_in = roles or {}
            ticket_roles = {
                "supervisor": _norm(supervisor or r_in.get("supervisor") or cfg_roles.get("supervisor") or "arch"),
                "workers": [_norm(w) for w in (r_in.get("workers") or cfg_roles.get("workers") or ["dsh", "codex"]) if w],
                "auditor": _norm(r_in.get("auditor") or cfg_roles.get("auditor") or "ag"),
            }
            sup = ticket_roles["supervisor"]
            auditor = ticket_roles["auditor"]
            if not sup:
                cfg_plan = (self.config()["defaults"] or {}).get("plan") or []
                sup = _norm((stage_agents.get("plan") or cfg_plan or ["arch"])[0])
            if sup:
                if not stage_agents.get("plan"):
                    stage_agents["plan"] = [sup]
                if not stage_agents.get("delivery"):
                    stage_agents["delivery"] = [sup]
            ticket = {
                "id": tid,
                "title": clean_title or "项目名称生成中",
                "created_at": _now(),
                "current": "plan" if clean_title else "name",
                "status": "running",
                "round": 1,
                "supervisor": sup,
                "auditor": auditor,
                "roles": ticket_roles,
                "is_121": bool(roles is not None),
                "reviews": {"initial": None, "final": None},
                "pending_assignees": [],
                "completed_assignees": [],
                "worker_submissions": {},
                "user_accepted": False,
                "user_notified": False,
                "acceptance_note": "",
                "stage_agents": stage_agents,
                "stages": {
                    k: {"agents": [], "status": "pending", "output": "", "outputs": {}, "round": 0, "updated_at": None}
                    for k in STAGE_KEYS
                },
                "log": [],
            }
            ticket["stages"]["goal"] = {
                "agents": [],
                "status": "done",
                "output": (goal or "").strip(),
                "outputs": {},
                "round": 1,
                "updated_at": _now(),
            }
            if clean_title:
                ticket["stages"]["name"] = {
                    "agents": [],
                    "status": "done",
                    "output": clean_title,
                    "outputs": {},
                    "round": 1,
                    "updated_at": _now(),
                }
                self._arm(ticket, "plan")
            else:
                self._arm(ticket, "name")
            return self.save(ticket)

    def _arm(self, ticket, stage, agents=None):
        if stage not in STAGE_KEYS:
            stage = "plan"
        agents = list(agents) if agents is not None else self.agents_for(ticket, stage)
        st = ticket["stages"].setdefault(
            stage, {"agents": [], "status": "pending", "output": "", "outputs": {}, "round": 0, "updated_at": None}
        )
        st["converged"] = False
        st["outputs"] = {}
        st.update({
            "agents": agents,
            "status": "running" if agents else "stalled",
            "round": (st.get("round") or 0) + 1,
            "updated_at": _now(),
        })
        ticket["current"] = stage
        ticket["status"] = "running" if agents else "stalled"
        trigger = " ".join(">>> %s" % a for a in agents) if agents else ""
        ticket.setdefault("log", []).append({
            "ts": _now(),
            "stage": stage,
            "event": "arm",
            "agents": agents,
            "trigger": trigger,
        })
        return agents

    def next_stage(self, stage):
        if stage not in FLOW:
            return None
        i = FLOW.index(stage)
        return FLOW[i + 1] if i + 1 < len(FLOW) else None

    def _sync_name(self, ticket):
        stage = (ticket.get("stages") or {}).get("name") or {}
        if stage.get("status") == "done":
            name = project_name(stage.get("output"))
            if name:
                ticket["title"] = name
                stage["output"] = name

    def record(self, ticket, stage, agent, text):
        """记录某栏输出并按四栏规则推进。"""
        agent_id = _norm(agent)
        clean_body = re.sub(r"^\s*\[#[0-9a-fA-F]{6,}:[a-z]+\]\s*", "", text or "").strip()
        st = ticket["stages"][stage]
        agents = [_norm(a) for a in (st.get("agents") or [])]

        # 汇总收敛阶段（当某栏配置多智能体时保留支持）
        if st.get("status") == "summarizing" and agent_id == (agents[0] if agents else ""):
            outs = st.setdefault("outputs", {})
            outs[agent_id] = clean_body
            st["output"] = clean_body
            st["status"] = "done"
            self._sync_name(ticket)
            return self._finish_stage(ticket, stage, clean_body)

        outs = st.setdefault("outputs", {})
        outs[agent_id] = clean_body
        _exp = st.get("expecting") or {}
        if _exp:
            _left = [a for a in (_exp.get("agents") or []) if _norm(a) != agent_id]
            _exp["agents"] = _left
            st["expecting"] = _exp
        st["updated_at"] = _now()

        if all(outs.get(_norm(a)) for a in (st.get("agents") or [])):
            raw_agents = st.get("agents") or []
            if len(raw_agents) == 1:
                final_out = outs.get(_norm(raw_agents[0])) or clean_body
            else:
                parts = ["【%s】%s" % (a, outs[_norm(a)]) for a in raw_agents]
                if len(raw_agents) >= 2 and not st.get("converged"):
                    st["status"] = "summarizing"
                    st["converged"] = True
                    st["round"] = 2
                    st["output"] = "\n\n".join(parts).strip()
                    return {"advanced": False, "summarize": True, "agent": raw_agents[0], "next": None, "agents": []}
                final_out = "\n\n".join(parts).strip()
            st["output"] = final_out
            st["status"] = "done"
            self._sync_name(ticket)
            return self._finish_stage(ticket, stage, final_out)

        st["status"] = "collecting"
        return {"advanced": False, "next": None, "agents": []}

    def _finish_stage(self, ticket, stage, final_out):
        supervisor = _norm(ticket.get("supervisor") or (self.agents_for(ticket, "plan") or ["arch"])[0])
        auditor = _norm(ticket.get("auditor") or ((ticket.get("roles") or {}).get("auditor")) or "ag")
        if stage == "name":
            nxt_agents = self._arm(ticket, "plan")
            return {"advanced": True, "next": "plan", "agents": nxt_agents}

        if stage == "plan":
            raw_workers = _extract_mentions(final_out, exclude=[supervisor])
            workers = []
            evicted_disabled = []
            for w in raw_workers:
                if self.is_agent_enabled(w):
                    workers.append(w)
                else:
                    evicted_disabled.append(w)
            if evicted_disabled:
                ticket.setdefault("log", []).append({
                    "ts": _now(),
                    "stage": "plan",
                    "event": "filter_disabled_workers",
                    "disabled_workers": evicted_disabled,
                    "msg": "总管指派的已停用智能体 [%s] 被工单系统自动拦截剔除，防止工单挂死" % "、".join(evicted_disabled),
                })
            ticket["worker_submissions"] = {}
            ticket["completed_assignees"] = []
            if workers:
                ticket["pending_assignees"] = list(workers)
                ticket["current"] = "plan"
                ticket["status"] = "running"
                return {
                    "advanced": False,
                    "waiting_workers": True,
                    "workers": list(workers),
                    "evicted_disabled": evicted_disabled,
                    "next": None,
                    "agents": [],
                }
            else:
                ticket["pending_assignees"] = []
                nxt_agents = self._arm(ticket, "delivery")
                return {"advanced": True, "next": "delivery", "agents": nxt_agents, "evicted_disabled": evicted_disabled}

        if stage == "delivery":
            # 若还有未交卷成员，严禁提前交付通过
            if ticket.get("pending_assignees"):
                st = ticket["stages"]["delivery"]
                st["status"] = "pending"
                ticket["current"] = "plan"
                ticket["status"] = "running"
                return {"advanced": False, "blocked_by_pending_workers": True, "next": None, "agents": []}

            reviews = ticket.setdefault("reviews", {"initial": None, "final": None})
            initial_review = reviews.get("initial")
            has_two_tier_audit = bool(ticket.get("completed_assignees") and auditor and supervisor != auditor and (ticket.get("roles") or {}).get("auditor"))

            if has_two_tier_audit and (not initial_review or not initial_review.get("passed")):
                # =================== 环节 3：总管初审验收 ===================
                verdict = _delivery_verdict(final_out)
                raw_rework = _extract_mentions(final_out, exclude=[supervisor])
                rework_workers = [w for w in raw_rework if self.is_agent_enabled(w)]
                if verdict == "reject" or rework_workers:
                    if not rework_workers:
                        rework_workers = [w for w in (ticket.get("completed_assignees") or []) if self.is_agent_enabled(w)]
                    reviews["initial"] = {"by": supervisor, "passed": False, "reason": final_out[:1000], "ts": _now()}
                    if rework_workers:
                        ticket["pending_assignees"] = list(rework_workers)
                        ticket["completed_assignees"] = [
                            a for a in (ticket.get("completed_assignees") or []) if _norm(a) not in set(rework_workers)
                        ]
                        for w in rework_workers:
                            (ticket.get("worker_submissions") or {}).pop(w, None)
                        ticket["stages"]["delivery"]["status"] = "pending"
                        ticket["current"] = "plan"
                        ticket["status"] = "running"
                        return {
                            "advanced": False,
                            "rejected": True,
                            "rework_workers": list(rework_workers),
                            "sub_stage": "initial_review",
                            "next": None,
                            "agents": [],
                        }
                    nxt_agents = self._arm(ticket, "plan")
                    return {"advanced": False, "rejected": True, "next": "plan", "agents": nxt_agents, "sub_stage": "initial_review"}

                # 初审通过 -> 流转环节 4 终审
                reviews["initial"] = {"by": supervisor, "passed": True, "reason": final_out[:1000], "ts": _now()}
                ticket["stages"]["delivery"].setdefault("outputs", {})[supervisor] = final_out
                ticket["current"] = "delivery"
                nxt_agents = self._arm(ticket, "delivery", [auditor])
                ticket["status"] = "pending_final_review" if ticket.get("is_121") else "running"
                self.expect(ticket["id"], "delivery", nxt_agents)
                return {
                    "advanced": True,
                    "next": "delivery",
                    "agents": nxt_agents,
                    "to_audit": True,
                    "sub_stage": "initial_review",
                }

            else:
                # =================== 终审把关（环节 4，或单管直接交付） ===================
                verdict = _delivery_verdict(final_out)
                reviewer = auditor if has_two_tier_audit else supervisor
                raw_rework = _extract_mentions(final_out, exclude=[reviewer])
                rework_workers = [w for w in raw_rework if self.is_agent_enabled(w)]
                if verdict == "reject" or rework_workers:
                    reviews["final"] = {"by": reviewer, "passed": False, "reason": final_out[:1000], "ts": _now()}
                    if initial_review:
                        reviews["initial"]["passed"] = False
                    if rework_workers:
                        ticket["pending_assignees"] = list(rework_workers)
                        ticket["completed_assignees"] = [
                            a for a in (ticket.get("completed_assignees") or []) if _norm(a) not in set(rework_workers)
                        ]
                        for w in rework_workers:
                            (ticket.get("worker_submissions") or {}).pop(w, None)
                        ticket["stages"]["delivery"]["status"] = "pending"
                        ticket["current"] = "plan"
                        ticket["status"] = "running"
                        return {
                            "advanced": False,
                            "rejected": True,
                            "rework_workers": list(rework_workers),
                            "sub_stage": "final_review",
                            "next": None,
                            "agents": [],
                        }
                    ticket["stages"]["delivery"]["status"] = "pending"
                    nxt_agents = self._arm(ticket, "plan", [supervisor])
                    self.expect(ticket["id"], "plan", nxt_agents)
                    return {
                        "advanced": False,
                        "rejected": True,
                        "next": "plan",
                        "agents": nxt_agents,
                        "sub_stage": "final_review",
                    }

                # 终审通过 -> pending_acceptance
                reviews["final"] = {"by": reviewer, "passed": True, "reason": final_out[:1000], "ts": _now()}
                ticket["stages"]["delivery"].setdefault("outputs", {})[reviewer] = final_out
                if has_two_tier_audit and initial_review:
                    combined_delivery = (
                        "【初审验收·%s】\n%s\n\n【终审把关·%s】\n%s"
                        % (supervisor, initial_review.get("reason", ""), auditor, final_out)
                    )
                    ticket["stages"]["delivery"]["output"] = combined_delivery
                ticket["current"] = ""
                ticket["status"] = "pending_acceptance"
                ticket["user_accepted"] = False
                notify_needed = not bool(ticket.get("user_notified"))
                ticket["user_notified"] = True
                return {
                    "advanced": True,
                    "next": None,
                    "agents": [],
                    "pending_acceptance": True,
                    "notify_user": notify_needed,
                    "sub_stage": "final_review",
                }

        return {"advanced": False, "next": None, "agents": []}

    def _record_worker_submission(self, ticket, worker, text):
        w = _norm(worker)
        pending = [_norm(x) for x in (ticket.get("pending_assignees") or [])]
        if w not in pending:
            return None
        pending = [x for x in pending if x != w]
        completed = [_norm(x) for x in (ticket.get("completed_assignees") or [])]
        if w not in completed:
            completed.append(w)
        subs = ticket.setdefault("worker_submissions", {})
        subs[w] = (text or "").strip()
        ticket["pending_assignees"] = pending
        ticket["completed_assignees"] = completed

        # 追加到第3栏 plan 的阶段性交卷记录展示，同时保持原方案可见
        plan_st = (ticket.get("stages") or {}).get("plan") or {}
        plan_outs = plan_st.setdefault("outputs", {})
        plan_outs[w] = (text or "").strip()

        if not pending:
            supervisor = _norm(ticket.get("supervisor") or ((ticket.get("roles") or {}).get("supervisor")) or "arch")
            nxt_agents = self._arm(ticket, "delivery", [supervisor])
            self.expect(ticket["id"], "delivery", nxt_agents)
            ticket["status"] = "pending_initial_review" if ticket.get("is_121") else "running"
            self.save(ticket)
            return {
                "ticket": ticket["id"],
                "title": ticket.get("title"),
                "stage": "plan",
                "worker": w,
                "all_workers_submitted": True,
                "advanced": True,
                "next": "delivery",
                "agents": nxt_agents,
            }
        self.save(ticket)
        return {
            "ticket": ticket["id"],
            "title": ticket.get("title"),
            "stage": "plan",
            "worker": w,
            "all_workers_submitted": False,
            "advanced": False,
            "next": None,
            "agents": [],
        }

    def user_accept(self, tid, passed, reason=""):
        """用户最终验收：passed 必须为真实 bool；未处于 pending_acceptance 严禁通过；驳回则同单续跑。"""
        with self._lock:
            if type(passed) is not bool:
                return {"ok": False, "error": "passed 必须为布尔值"}
            tk = self.load(tid)
            if not tk:
                return {"ok": False, "error": "工单不存在", "status_code": 404}
            if tk.get("status") != "pending_acceptance":
                return {"ok": False, "error": "工单尚未完成交付，不能提前进行用户验收"}
            clean_reason = (reason or "").strip()
            if passed:
                tk["status"] = "done"
                tk["current"] = ""
                tk["user_accepted"] = True
                tk["acceptance_note"] = clean_reason or "用户验收通过"
                self.save(tk)
                return {"ok": True, "ticket": tk}
            else:
                if not clean_reason:
                    clean_reason = "用户验收驳回"
                tk["round"] = int(tk.get("round") or 1) + 1
                tk["status"] = "running"
                tk["user_accepted"] = False
                tk["user_notified"] = False
                tk["acceptance_note"] = clean_reason
                tk["pending_assignees"] = []
                tk["completed_assignees"] = []
                tk["worker_submissions"] = {}
                tk["reviews"] = {"initial": None, "final": None}
                # 追加用户驳回要求到第 2 栏 goal，保留初始要求原文
                goal_st = tk.setdefault("stages", {}).setdefault(
                    "goal", {"agents": [], "status": "done", "output": "", "outputs": {}, "round": 1, "updated_at": _now()}
                )
                prev_goal = (goal_st.get("output") or "").strip()
                append_line = "【第 %d 轮用户驳回要求】%s" % (tk["round"], clean_reason)
                if clean_reason not in prev_goal:
                    goal_st["output"] = (prev_goal + "\n\n" + append_line).strip() if prev_goal else append_line
                goal_st["round"] = tk["round"]
                goal_st["updated_at"] = _now()

                for k in ("plan", "delivery"):
                    if k in tk.get("stages", {}):
                        tk["stages"][k]["status"] = "pending"
                        tk["stages"][k]["output"] = ""
                        tk["stages"][k]["outputs"] = {}
                        tk["stages"][k]["expecting"] = {}
                ags = self._arm(tk, "plan")
                self.expect(tk["id"], "plan", ags)
                self.save(tk)
                return {"ok": True, "ticket": tk, "redispatch_stage": "plan", "agents": ags}

    def build_prompt(self, ticket, stage, available_agents=None, disabled_agents=None):
        """生成派单文本：带工单号/环节标记 + 已完成环节的上下文。"""
        lines = ["[工单#%s 环节:%s] %s" % (ticket.get("id"), stage, STAGE_LABEL.get(stage, stage))]
        if stage == "name":
            goal = ((ticket.get("stages") or {}).get("goal") or {}).get("output") or ""
            lines.append("第二栏·用户要求：\n%s" % goal.strip())
            lines.append("请只根据上面用户要求，总结一个准确、简短的项目名称（建议4—20字）。"
                         "概括要完成的事情，不编造功能，不取宣传性或诗意名称，不照抄整段需求。"
                         "除规定的首行标记外，只输出名称本身，不写前缀、解释、方案或分工。")
        else:
            lines.append("工单标题：%s" % (ticket.get("title") or ""))
            if ticket.get("acceptance_note") and not ticket.get("user_accepted"):
                lines.append("【用户驳回重做要求】%s" % ticket.get("acceptance_note").strip())
        for k in ([] if stage == "name" else STAGE_KEYS):
            if k == stage:
                break
            out = (ticket.get("stages", {}).get(k) or {}).get("output") or ""
            if out.strip():
                lines.append("【已完成·%s】%s" % (STAGE_LABEL.get(k, k), out.strip()[:1200]))

        if stage == "plan":
            workers = (ticket.get("roles") or {}).get("workers") or ["dsh", "codex"]
            workers_str = "、".join(workers)
            lines.append("【1-2-1 环节 1·方案与分工】你是全流程总管架构师。")
            lines.append("本工单执行环节固定由两名成员协同干活：%s。" % workers_str)
            if disabled_agents:
                dis_desc = "、".join(["%s(%s)" % (a.get("name") or a.get("id"), a.get("id")) for a in disabled_agents])
                lines.append("【⚠️ 智能体停用警示】当前以下智能体处于【停用】状态：%s" % dis_desc)
            lines.append("请根据用户要求制定清晰完整的设计与实现方案，并在文末使用独立行明确指派协同人：")
            for w in workers:
                lines.append(">>> %s" % w)
        elif stage == "delivery":
            reviews = ticket.get("reviews") or {}
            initial_review = reviews.get("initial")
            has_two_tier_audit = bool(ticket.get("auditor") and ticket.get("supervisor") != ticket.get("auditor") and (ticket.get("roles") or {}).get("auditor"))
            if has_two_tier_audit and (not initial_review or not initial_review.get("passed")):
                subs = ticket.get("worker_submissions") or {}
                if subs:
                    for w, content in subs.items():
                        lines.append("【协同成员交卷·%s】%s" % (w, str(content).strip()[:1200]))
                lines.append("【1-2-1 环节 3·总管初审验收与交付】协同成员已完成交卷。作为总管，请对照用户要求与既定方案进行质量初审：")
                lines.append("- 若初审通过：请明确声明【初审通过】或【验收通过】，并附交付成果摘要。初审通过后，工单将保留记录并流转至终审。")
                lines.append("- 若初审不合格：请明确声明【初审驳回】或【验收不通过】，并使用独立行 `>>> 成员代号` 指出缺陷要求返工。")
            elif has_two_tier_audit:
                lines.append("【总管初审意见·%s】%s" % (initial_review.get("by", "arch"), str(initial_review.get("reason", "")).strip()[:1000]))
                subs = ticket.get("worker_submissions") or {}
                if subs:
                    for w, content in subs.items():
                        lines.append("【协同成员交卷·%s】%s" % (w, str(content).strip()[:1000]))
                lines.append("【1-2-1 环节 4·终审质检与二审把关】总管初审已通过。作为终审审计员，请进行终审质量与安全性把关：")
                lines.append("- 若终审通过：请明确声明【终审通过】或【二审通过】。终审通过后，工单将进入「待用户验收」，交由 Master 炫炫做最终决策。")
                lines.append("- 若终审不通过：请明确声明【终审驳回】或【二审驳回】，指明问题（若是执行问题可 `>>> 成员代号` 打回执行人，若是设计方案问题打回总管）。")
            else:
                subs = ticket.get("worker_submissions") or {}
                if subs:
                    for w, content in subs.items():
                        lines.append("【成员交卷·%s】%s" % (w, str(content).strip()[:1200]))
                lines.append("【全流程监管人验收与交付】所有指派成员均已交卷。请对比用户要求验收：")
                lines.append("- 若验收通过：请输出【验收通过】及最终交付清单与用户指引，工单将进入「待用户验收」。")
                lines.append("- 若某成员不合格：请声明【验收不通过】并用独立行 `>>> 成员代号` 指派返工。")

        agents = (ticket.get("stages", {}).get(stage) or {}).get("agents") or []
        if len(agents) >= 2:
            lines.append("本环节由 %s 协作：先各自给出方案，随后由 %s 汇总收敛终稿。" % ("、".join(agents), agents[0]))
        st = (ticket.get("stages", {}).get(stage) or {})
        if st.get("status") == "summarizing":
            outs = st.get("outputs") or {}
            for a, v in outs.items():
                lines.append("【%s 初稿】%s" % (a, (v or "").strip()[:1500]))
            lines.append("你是本环节主笔：请在上面各人初稿基础上消解分歧，输出唯一最终稿。")
        lines.append("回复首行必须写 [#%s:%s]，否则不会被记入工单。" % (ticket.get("id"), stage))
        lines.append("请直接输出本环节内容，结尾不要加多余说明。")
        return "\n".join(lines)

    def expect(self, ticket_id, stage, agents):
        """派单时一次性登记「谁在等这一环节的回复」，hook 只认登记过的对象。"""
        with self._lock:
            t = self.load(ticket_id)
            if not t or stage not in STAGE_KEYS:
                return
            st = t["stages"].get(stage) or {}
            st["expecting"] = {"agents": list(agents or []), "stage": stage, "ts": _now()}
            t["stages"][stage] = st
            self.save(t)

    def hook(self, sender, text):
        """智能体回复时调用：
        1. 带 [#tid:stage] 标记的回复：精确匹配对应工单与当前环节（若越级、错人、工单已删或尚有待交卷成员时拦截）。
        2. 成员群聊交卷回复（如 `>>> 总管` 或带 `[#tid]`）：匹配 pending_assignees；若同一成员同时在多张工单 pending 且未带明确工单号，拒绝猜测归属以防串单。
        """
        if not sender or not isinstance(text, str) or not text.strip():
            return None
        with self._lock:
            _s = _norm(sender)
            _mk = re.match(r"^\s*\[#([0-9a-fA-F]{6,})(?::([a-zA-Z0-9_:]+))?\]", text or "")
            if _mk and _mk.group(2):
                tid = _mk.group(1).lower()
                raw_stage = _mk.group(2).lower()
                stage_alias = {
                    "review": "delivery", "review:initial": "delivery", "review:final": "delivery",
                    "audit": "delivery", "verify": "delivery", "work": "delivery"
                }
                stage = stage_alias.get(raw_stage, raw_stage)
                t = self.load(tid)
                if not t or t.get("status") not in ("running", "pending_initial_review", "pending_final_review"):
                    return None
                # 当处于 plan 但正在等待成员交卷时，禁止总管抢跑 delivery
                if stage == "delivery" and t.get("pending_assignees"):
                    return None
                if t.get("current") != stage:
                    return None
                st = t.get("stages", {}).get(stage) or {}
                agents = st.get("agents") or []
                exp = st.get("expecting") or {}
                _ag = [_norm(a) for a in agents]
                _ex = [_norm(a) for a in (exp.get("agents") or [])]
                if _s not in _ag or (_ex and _s not in _ex):
                    return None
                if exp.get("ts") and _now() - (exp.get("ts") or 0) > 1800:
                    return None
                res = self.record(t, stage, _s, text)
                self.save(t)
                return {"ticket": t["id"], "title": t.get("title"), "stage": stage, **res}

            # 成员交卷匹配（群聊汇报给总管）
            explicit_tid = _mk.group(1).lower() if _mk else None
            if not explicit_tid:
                m_any = re.search(r"#([0-9a-fA-F]{8})\b", text or "")
                if m_any:
                    explicit_tid = m_any.group(1).lower()

            candidates = []
            for t in self.list(50):
                if t.get("status") not in ("running", "pending_initial_review", "pending_final_review"):
                    continue
                pending = [_norm(x) for x in (t.get("pending_assignees") or [])]
                if _s not in pending:
                    continue
                if explicit_tid and str(t.get("id") or "").lower() != explicit_tid:
                    continue
                candidates.append(t)

            if not candidates:
                return None
            # 多单同人且无明确工单号标记：拒绝猜测，防止串单
            if len(candidates) > 1 and not explicit_tid:
                return None

            target = self.load(candidates[0]["id"])
            if not target:
                return None
            return self._record_worker_submission(target, _s, text)

    def stalled(self):
        out = []
        for t in self.list(200):
            if t.get("status") not in ("running", "pending_initial_review", "pending_final_review") or t.get("pending_assignees"):
                continue
            for k in STAGE_KEYS:
                st = t.get("stages", {}).get(k) or {}
                if st.get("status") in ("running", "collecting", "summarizing") and st.get("updated_at") and \
                        _now() - st["updated_at"] > STAGE_TIMEOUT:
                    out.append({"ticket": t["id"], "title": t.get("title"), "stage": k,
                                "age": int(_now() - st["updated_at"])})
        return out

    def redispatch(self, tid, stage=None):
        with self._lock:
            t = self.load(tid)
            if not t:
                return None
            stage = stage or t.get("current") or "name"
            if stage not in STAGE_KEYS:
                return None
            agents = self._arm(t, stage)
            self.save(t)
            return {"ticket": tid, "stage": stage, "agents": agents,
                    "trigger": " ".join(">>> %s" % a for a in agents)}