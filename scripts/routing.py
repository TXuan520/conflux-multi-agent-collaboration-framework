# -*- coding: utf-8 -*-
"""协作台路由名单的单一事实源。

原来「谁能被 >>> 点名」这件事散在 hub.py 的三处硬编码名单里：
箭头拆分正则、m_arrow 的 target_names、inline_targets 的别名表。
漏写一个显示名（比如 argon）就会出现「消息发不出去但没人报错」。

现在统一从 ${CONFLUX_ROOT}\\data\\hub\\providers.json 派生：
    providers[].id          内部 id，也是消息里的 sender
    providers[].display_name 显示名（如 Argon）
    providers[].aliases     所有能被点名的写法

改名单只改 JSON，不改代码；providers.json 的 mtime 变了自动重载。
"""
import confluxconfig as cfg
import json
import os
import re
import time

PROVIDERS_FILE = cfg.root_path('data/hub/providers.json')

# 没有任何 provider 文件时的兜底：宁可路由不全，也不能让 hub 起不来
_DEFAULT_PROVIDERS = [
    {"id": "dsh", "display_name": "DeepSeek", "aliases": ["deepseek", "飞鱼", "ds"]},
    {"id": "codex", "display_name": "GPT", "aliases": ["gpt", "codex", "chatgpt"]},
    {"id": "ag", "display_name": "Gemini", "aliases": ["gemini", "antigravity", "双子"]},
    {"id": "arch", "display_name": "Argon", "aliases": ["arch", "argon", "架构师"]},
    {"id": "mimo", "display_name": "MiMo", "aliases": ["mimo", "小米"]},
    {"id": "doubao", "display_name": "Seedance", "aliases": ["doubao", "豆包", "seedance"]},
    {"id": "workbuddy", "display_name": "WorkBuddy", "aliases": ["workbuddy", "WorkBuddy", "wb", "小务", "混元"]},
    {"id": "human", "display_name": "炫炫", "aliases": ["human", "炫炫", "主人"]},
]

_CACHE = {"key": None, "providers": None}


def load_providers(path=None):
    """读 providers.json；文件缺失或损坏时退回内置名单。

    缓存键用 (mtime_ns, size)：只比 mtime 时，同一时钟 tick 内两次写入会拿到旧快照。
    """
    path = path or PROVIDERS_FILE
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return list(_DEFAULT_PROVIDERS)
    if _CACHE["key"] == key and _CACHE["providers"] is not None:
        return _CACHE["providers"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("providers") if isinstance(data, dict) else data
        out = []
        for it in items or []:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            if it.get("enabled") is False:
                continue
            aliases = [str(a) for a in (it.get("aliases") or []) if str(a).strip()]
            for extra in (it.get("display_name"), it.get("id")):
                if extra and str(extra) not in aliases:
                    aliases.append(str(extra))
            out.append({"id": str(it["id"]), "display_name": str(it.get("display_name") or it["id"]),
                        "aliases": aliases,
                        # 透传展示用字段，前端不用再写死一份角色表
                        "role": str(it.get("role") or ""),
                        "color": str(it.get("color") or ""),
                        "avatar": str(it.get("avatar") or ""),
                        "system_prompt": str(it.get("system_prompt") or ""),
                        "mode": str(it.get("mode") or ""),
                        "protocol": str(it.get("protocol") or "")})
    except Exception:
        return list(_DEFAULT_PROVIDERS)
    _CACHE["key"] = key
    _CACHE["providers"] = out
    return out


def alias_map(providers=None):
    """别名（小写）-> 内部 id。

    先到先得：后注册的角色不能靠重名别名劫持已有角色的路由。
    冲突在注册接口那侧会直接 409，这里只是兜底。
    """
    m = {}
    for p in (load_providers() if providers is None else providers):
        for a in p["aliases"]:
            key = str(a).strip().lower()
            if key and key not in m:
                m[key] = p["id"]
    return m


# 保留 id：任何新注册的角色都不许占用这些名字
RESERVED_IDS = frozenset({"human", "system", "dsh", "codex", "ag", "arch", "mimo", "doubao", "workbuddy",
                          "narrator", "watch"})


def agent_ids(providers=None):
    """所有可当收件人的 id（含 human）。"""
    return {p["id"] for p in (load_providers() if providers is None else providers)}


def sender_ids(providers=None):
    """可当消息发送者的 id：不含 human/system，但配置读空时要兜底，
    否则一次坏文件就能让全部 agent 的消息被拒。"""
    ids = agent_ids(providers) - {"human", "system"}
    return ids


def target_names(providers=None):
    """给 m_arrow 用的名单（长别名优先，避免 ds 抢在 deepseek 前面）。"""
    names = set()
    for p in (load_providers() if providers is None else providers):
        for a in p["aliases"]:
            names.add(str(a).strip())
    return sorted(names, key=len, reverse=True)


def arrow_alt(providers=None):
    """箭头拆分正则里那段 alternation，例如 deepseek|gemini|gpt|..."""
    return "|".join(re.escape(n) for n in target_names(providers))


def resolve_names(target_str, providers=None):
    """把 '>>> 之后的收件人片段' 解析成内部 id 列表（去重保序）。"""
    s = str(target_str or "").strip()
    if not s:
        return []
    low = s.lower()
    amap = alias_map(providers)
    out = []

    # 1) 精确/分词命中：名字之间可能用空格、逗号、加号、斜杠、和、与、及 分隔
    tokens = [t for t in re.split(r"[^\w\u4e00-\u9fff]+|和|与|及", low) if t]
    for tok in tokens:
        if tok in amap and amap[tok] not in out:
            out.append(amap[tok])
    # 1b) 无空格连写（>>> MiMo你好）：英文别名 >=3 字符时允许做词首前缀
    if not out:
        for tok in tokens:
            for alias in sorted(amap, key=len, reverse=True):
                if (len(alias) >= 3 and alias.isascii() and tok.startswith(alias)):
                    rid = amap[alias]
                    if rid not in out:
                        out.append(rid)
                    break

    # 2) 兜底：只让中文别名做子串匹配。
    #    英文短别名（ds、ag、gpt）做子串会误伤，比如 unknown_agent_ds 会被判给 dsh。
    if not out:
        for alias in sorted(amap, key=len, reverse=True):
            if not alias or not any(ord(ch) > 127 for ch in alias):
                continue
            if alias in low:
                rid = amap[alias]
                if rid not in out:
                    out.append(rid)
    return out
