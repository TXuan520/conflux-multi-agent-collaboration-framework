# -*- coding: utf-8 -*-
"""
agent_memory.py —— 按 AI 隔离的记忆存储 / 来源引用 / 相关检索 / 预算选择（独立模块，零外部依赖）。

供 hub.py 注入差异化策略使用，替换「每轮全量重贴静态块」。设计目标：
  1. 按 AI 隔离：每个 agent 只读写自己的记忆命名空间，互不可见。
  2. 来源引用：每条记忆携带 source（来源路径/通道/turn），检索结果可追溯。
  3. 相关检索：query 与记忆文本的字符 n-gram 相似度排序（无第三方库，可复现）。
  4. 预算选择：给定条数/字符预算，返回不超预算的记忆子集（相关度优先，静态块保底）。
  5. 可回退：记忆缺失/损坏时返回空集而不抛异常，注入方回退到默认静态块。

自测：python agent_memory.py   （在临时目录跑，不碰生产 data/hub）
"""
from __future__ import annotations
import confluxconfig as cfg

import json
from contextlib import closing
import os
import re
import time
from typing import List, Dict, Optional

# ---- 可复现检索：字符 3-gram + Jaccard，无第三方依赖 ----
_STOP = set("的了是在和有我你他她它们这那个都也就很啊吧呢吗呀把被让向与及或但而如其所为以于对从到一不没无")
_N = 3
# 中文短查询（如 2 字关键词）比 _N 短时会退化成整串，永远匹配不到长文本的 3-gram；
# 这里额外用二元组做一次并集，保证「工单」能命中「工单系统规范整改」这类包含关系。
_GRAMS_BI = 2


def _norm(s: str) -> str:
    s = re.sub(r"[\s\*`_#>　、，。；：！？!?,.;:()（）\[\]【】\-]+", "", (s or "")).lower()
    return s


def _grams(s: str) -> set:
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", _norm(s))
    if len(t) < _N:
        return {t} if t else set()
    return {t[i:i + _N] for i in range(len(t) - _N + 1)}


def _grams_bi(s: str) -> set:
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", _norm(s))
    if len(t) < _GRAMS_BI:
        return {t} if t else set()
    return {t[i:i + _GRAMS_BI] for i in range(len(t) - _GRAMS_BI + 1)}


def similarity(a: str, b: str) -> float:
    # 3-gram 为主；中文短语包含关系靠 2-gram 并集兜底
    ga = _grams(a) | _grams_bi(a)
    gb = _grams(b) | _grams_bi(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    if not inter:
        return 0.0
    jaccard = inter / len(ga | gb)
    overlap = inter / min(len(ga), len(gb))
    # 中文短查询与长文本的包含关系，Jaccard 会被长度稀释；用重叠系数折中
    return 0.5 * jaccard + 0.5 * overlap


class AgentMemory:
    """单个 agent 的隔离记忆库。根目录下按 agent 名分子目录，天然隔离。"""

    def __init__(self, root: str, agent: str):
        if not agent or any(c in agent for c in "/\\.."):
            raise ValueError("agent name must be a simple id")
        self.agent = agent
        self.dir = os.path.join(root, agent)
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "memory.jsonl")

    # ---- 写 ----
    def add(self, text: str, source: str = "", kind: str = "fact",
            ts: Optional[float] = None, dedupe: bool = True) -> Dict:
        """写入一条记忆，带来源引用 source。默认按去空白规范化去重。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("empty memory")
        rec = {"id": "%x" % int((ts or time.time()) * 1000),
               "agent": self.agent, "text": text,
               "source": source or "", "kind": kind,
               "scope": "self-activity-v2" if kind in ("task", "operation") else "archive",
               "ts": ts or time.time()}
        if dedupe and any(_norm(m.get("text", "")) == _norm(text)
                          for m in (self._activities() if rec["scope"] == "self-activity-v2" else self._load())
                          if isinstance(m, dict)):
            return rec
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def _exists(self, text: str) -> bool:
        n = _norm(text)
        for m in self._load():
            if _norm(m.get("text", "")) == n:
                return True
        return False

    # ---- 读 ----
    def _load(self) -> List[Dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return out

    def all(self) -> List[Dict]:
        return self._load()

    def _activities(self) -> List[Dict]:
        # Legacy files may contain copied group chatter and project snapshots.
        # Preserve them for audit, but never inject records without explicit ownership.
        if not CompressedMemory.participants([self.agent]):
            return []
        return [m for m in self._load() if isinstance(m, dict)
                and m.get("agent") == self.agent
                and m.get("scope") == "self-activity-v2"
                and m.get("kind") in ("task", "operation")]

    def search(self, query: str, top_k: int = 5, min_score: float = 0.05) -> List[Dict]:
        """相关检索：按相似度降序，每条附 score 与原 source（来源引用）。"""
        scored = []
        for m in self._activities():
            s = similarity(query, m.get("text", ""))
            if s >= min_score:
                item = dict(m)
                item["score"] = round(s, 4)
                scored.append(item)
        scored.sort(key=lambda x: (-x["score"], -x.get("ts", 0)))
        return scored[:top_k]

    # ---- 预算选择 ----
    def select(self, query: str = "", char_budget: int = 2000, top_k: int = 8,
               prefix: Optional[List[str]] = None) -> List[str]:
        """在预算内选自身执行履历；兼容 prefix 参数但不注入静态背景。

        返回去重后的字符串列表；每条记忆前缀标注来源 `[来源]`。
        """
        # prefix is retained in the signature for old callers, but static project
        # snapshots are no longer part of an agent's own execution history.
        chosen: List[str] = []
        used = 0
        pool = self.search(query, top_k=top_k) if query else \
            [dict(m, score=0.0) for m in self._activities()][-top_k:]
        for m in pool:
            src = m.get("source", "")
            tag = "[%s] " % src if src else ""
            piece = tag + m.get("text", "")
            if any(_norm(piece) == _norm(c) for c in chosen):
                continue
            if used + len(piece) <= char_budget:
                chosen.append(piece)
                used += len(piece)
        return chosen

    def inject_block(self, query: str = "", char_budget: int = 2000,
                     prefix: Optional[List[str]] = None) -> str:
        """一步生成自身执行履历，不包含静态背景，不超预算。"""
        pieces = self.select(query=query, char_budget=char_budget, prefix=prefix)
        return "\n".join(pieces)

    def count(self) -> int:
        return len(self._load())


class MemoryBank:
    """多 agent 记忆总线：按 agent 名隔离路由，一个 agent 看不到别人的记忆。"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._mem: Dict[str, AgentMemory] = {}

    def get(self, agent: str) -> AgentMemory:
        if agent not in self._mem:
            self._mem[agent] = AgentMemory(self.root, agent)
        return self._mem[agent]

    def agents(self) -> List[str]:
        try:
            return sorted(d for d in os.listdir(self.root)
                          if os.path.isdir(os.path.join(self.root, d)))
        except Exception:
            return []


# Compatibility export; execution history is managed by the program, not a per-turn tail.
MEMORY_POLICY = ""


class CompressedMemory:
    """Durable excerpts for routed messages and versioned project records."""

    def __init__(self, path=None):
        import sqlite3
        self.path = path or cfg.root_path('data/hub/agent-memory/compressed.sqlite3')
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE IF NOT EXISTS excerpts ('
                'agent TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, '
                'entity TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (agent, source))')
            connection.execute('CREATE INDEX IF NOT EXISTS excerpts_agent_time ON excerpts(agent, ts)')

    @staticmethod
    def compact(text, budget=600):
        text = re.sub(r'(?i)(api[_ -]?key|authorization|token|password|密钥|密码)\s*[:=：]\s*[^\s,，;；]+',
                      r'\1=[已隐藏]', str(text or ''))
        lines = list(dict.fromkeys(re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()))
        text = ' / '.join(lines)
        if len(text) <= budget:
            return text
        head = budget // 2
        return text[:head] + ' … ' + text[-(budget-head-3):]

    @staticmethod
    def participants(values):
        return {str(value) for value in values if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', str(value))
                and value not in ('human', 'system', 'narrator', 'watch', 'unknown')}

    def _insert(self, rows):
        import sqlite3
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            before = connection.total_changes
            connection.executemany('INSERT OR IGNORE INTO excerpts VALUES (?, ?, ?, ?, ?, ?)', rows)
            return connection.total_changes - before

    def remember_messages(self, messages, source):
        """Index only the author's own executed-task/operation summaries.

        Receipt does not prove participation: recipients, human requests, narrator
        notices and group project documents never create an execution history.
        """
        import hashlib
        def rows():
            for message in messages:
                sender = message.get('sender') or ''
                if not self.participants([sender]):
                    continue
                body = str(message.get('text') or '').strip()
                kind = message.get('kind') or 'text'
                if not body or kind not in ('task', 'operation', 'text'):
                    continue
                # Task/operation records come from execution hooks. Plain chat is
                # kept only when the author reports an action, not an assignment.
                if kind == 'text' and not re.search(
                        r'(已(?:完成|修复|修改|实现|新增|验证|检查|交付|部署|写入|落盘|更新|执行)|'
                        r'验证(?:通过|失败)|自验(?:通过|失败)|未完成[：:]|执行失败)', body):
                    continue
                ident = str(message.get('id') or hashlib.sha256(
                    json.dumps(message, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
                # Different references keep old contaminated rows untouched and
                # permit re-indexing the same source under the new ownership rule.
                reference = source + '#' + ident + '@self-v2'
                text = self.compact(body)
                yield (sender, reference, 'self_activity_v2', ident, text,
                       float(message.get('ts') or 0))
        return self._insert(rows())

    def remember_project(self, path, aliases, display_no=''):
        """Compatibility no-op: a whole project is not an agent's own activity."""
        return 0

    def sync_projects(self, tickets_dir, aliases, projects_file):
        """Project snapshots are intentionally not imported into execution history."""
        return 0

    def sync_messages(self, path):
        def messages():
            with open(path, encoding='utf-8') as stream:
                for line in stream:
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(message, dict):
                        yield message
        return self.remember_messages(messages(), path)

    def context(self, agent, query='', budget=2400):
        import sqlite3
        if not self.participants([agent]):
            return ''
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            records = connection.execute(
                'SELECT source, kind, entity, text, ts FROM excerpts '
                "WHERE agent=? AND kind='self_activity_v2' ORDER BY ts DESC, rowid DESC", (agent,)).fetchall()
        pool = [(source, kind, text, timestamp)
                for source, kind, entity, text, timestamp in records]
        relevant = sorted(pool, key=lambda row: (similarity(query, row[2]), row[3]), reverse=True)[:6]
        recent = pool[:2]
        chosen, sources, used = [], set(), 0
        for source, kind, text, timestamp in recent + relevant:
            if source in sources:
                continue
            piece = '[来源 %s]\n%s' % (source, text)
            if used + len(piece) + 2 > budget:
                continue
            chosen.append(piece)
            sources.add(source)
            used += len(piece) + 2
        return '\n\n'.join(chosen)


# ================= 临时目录自测 =================
def _selftest() -> None:
    import tempfile
    ok = 0
    total = 0

    def check(cond, msg):
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
            print("  ok  -", msg)
        else:
            print("  FAIL-", msg)

    with tempfile.TemporaryDirectory() as td:
        print("== agent_memory 自测（临时目录 %s）==" % td)
        bank = MemoryBank(td)
        mimo = bank.get("mimo")
        codex = bank.get("codex")

        # 1. 隔离
        mimo.add("mimo 记得住项目8-1 的基线", source=cfg.expand('${CONFLUX_WORKSPACE}/ai-context-baseline.md'), kind="task")
        codex.add("codex 记得住 hub.py 的路由检查", source=cfg.root_path('scripts/hub.py'), kind="task")
        check(mimo.count() == 1 and codex.count() == 1, "写入各 1 条")
        check(all("mimo" in x["text"] for x in mimo.all()), "mimo 只见自己的记忆")
        check(all("codex" in x["text"] for x in codex.all()), "codex 只见自己的记忆")
        check(bank.get("dsh").count() == 0, "未写入的 agent 记忆为空")

        # 2. 来源引用
        got = mimo.search("基线 项目8-1")
        check(bool(got) and got[0].get("source", "").endswith("ai-context-baseline.md"),
              "检索结果带来源引用 source")

        # 3. 相关检索排序
        mimo.add("今天复验了跨会话事实回忆", source="memory/facts.md", kind="operation")
        top = mimo.search("跨会话 回忆")
        check(top and "回忆" in top[0]["text"], "相关检索命中目标记忆")
        check(top[0]["score"] >= top[-1]["score"], "相关度降序")

        # 4. 去重
        before = mimo.count()
        mimo.add("mimo 记得住项目8-1 的基线")   # 重复
        check(mimo.count() == before, "重复文本去重")

        # 5. 预算选择：静态块保底 + 相关优先 + 不超预算
        long_txt = "预算测试" * 200
        mimo.add(long_txt, source="big")
        sel = mimo.select(query="跨会话", char_budget=120,
                          prefix=["【项目速览】固定静态块"])
        check(not any("项目速览" in piece for piece in sel), "不注入静态项目背景")
        check(sum(len(x) for x in sel) <= 120, "总长度不超字符预算")

        # 6. 可回退：损坏行跳过不抛异常
        with open(mimo.path, "a", encoding="utf-8") as f:
            f.write("{坏json行\n")
        check(mimo.count() >= before, "损坏行跳过，不抛异常")

        # 7. 空 agent 名非法
        try:
            AgentMemory(td, "../evil")
            check(False, "非法 agent 名应拒绝")
        except ValueError:
            check(True, "非法 agent 名被拒绝")

        # 8. 固定回归：短中文查询修复（2026-11-04，覆盖双字查询/无关文本/预算限制）
        reg = bank.get("regress")
        reg.add("工单系统规范整改", source="ticket/8-1", kind="task")
        reg.add("跨会话事实回忆基线", source="memory/facts.md", kind="fact")

        # 8.1 双字查询：2 字查询必须命中包含它的长文本
        check(similarity("工单", "工单系统规范整改") > 0,
              "回归[双字查询] similarity(工单, 工单系统规范整改) > 0")
        hit = reg.search("工单", top_k=5)
        check(hit and hit[0]["text"] == "工单系统规范整改" and hit[0]["score"] > 0,
              "回归[双字查询] search(工单) 命中长文本且带 score")

        # 8.2 无关文本：毫不相关的查询不得命中
        check(similarity("量子力学", "工单系统规范整改") == 0.0,
              "回归[无关文本] 无关查询相似度为 0")
        check(not reg.search("量子力学", top_k=5),
              "回归[无关文本] 无关查询检索不返回结果")

        # 8.3 预算限制：任何预算下返回内容都不超限
        tiny = reg.select(query="工单", char_budget=8, prefix=["12345678"])
        check(sum(len(x) for x in tiny) <= 8 and not tiny,
              "回归[预算限制] 极小预算不注入静态块且不超限")
        mid = reg.select(query="跨会话", char_budget=30)
        check(sum(len(x) for x in mid) <= 30,
              "回归[预算限制] 相关命中时总长仍不超预算")

    print("== 自测结果：%d/%d 通过 ==" % (ok, total))
    if ok != total:
        raise SystemExit(1)


if __name__ == "__main__":
    _selftest()
