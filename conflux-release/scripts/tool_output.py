# -*- coding: utf-8 -*-
"""工具输出瘦身：超长截断 + 内容哈希去重落盘 + 落盘前脱敏。

给 agent_loop 的工具返回值用：
    from tool_output import condense
    out = condense(raw_text)        # 短就原样返回，长就落盘并给头尾预览

三条硬约束（方案文档 v1.2 的 P0-4）：
1. 默认上限 4000 字符，超过就落盘，只回填头尾 + 路径提示。
2. 落盘文件按 sha256[:16] 命名，同一份内容重复读只占一份磁盘。
3. 落盘前必须脱敏——token / 密码 / 私钥不许进缓存目录，也不许进哈希输入。
"""
import confluxconfig as cfg
import hashlib
import os
import re

MAX_INLINE = 4000
HEAD_CHARS = 1500
TAIL_CHARS = 500
CACHE_DIR = cfg.root_path('data/hub/tool_cache')

_SECRET_PATTERNS = [
    # 私钥整块
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    # 常见 token 形态
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b"), "[REDACTED_KEY]"),
    (re.compile(r"\b(AQ\.[A-Za-z0-9_\-]{8,})\b"), "[REDACTED_KEY]"),
    (re.compile(r"\b(ya29\.[A-Za-z0-9_\-]{8,})\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\b(ghp_[A-Za-z0-9]{8,})\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})"), "[REDACTED_GOOGLE_KEY]"),
    # 认证头：先吃掉整段值，免得被后面的 key=value 规则拆成两半
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*basic\s+)[A-Za-z0-9+/=._\-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{10,}"), r"\1[REDACTED]"),
    # 连接串里的 user:pass@
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    # key = value / "key": "value" / ?key=value —— 键名两侧的引号必须吃掉，否则标准 JSON 漏网
    (re.compile(r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
                r"apikey|token|secret|password|passwd|pwd|"r"key)\b[\"']?\s*[:=]\s*[\"']?)"
                r"([^\s\"',;}\r\n]{4,})"), r"\1[REDACTED]"),
]


def sanitize(text):
    """把明显的凭据替换掉。返回脱敏后的文本。"""
    if not isinstance(text, str):
        text = str(text)
    out = text
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _hash(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def store(text, cache_dir=None):
    """落盘（同哈希复用）。返回 (hash_id, path, reused)。"""
    cache_dir = cache_dir or CACHE_DIR
    hid = _hash(text)
    path = os.path.join(cache_dir, hid + ".txt")
    if os.path.exists(path):
        return hid, path, True
    os.makedirs(cache_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return hid, path, False


def condense(text, limit=MAX_INLINE, cache_dir=None, label="工具输出"):
    """短文本原样返回；超限则脱敏→落盘→返回头尾预览。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    clean = sanitize(text)
    hid, path, reused = store(clean, cache_dir)
    head = clean[:HEAD_CHARS]
    tail = clean[-TAIL_CHARS:] if len(clean) > HEAD_CHARS + TAIL_CHARS else ""
    parts = [
        "[%s共 %d 字符，超过 %d 上限，已落盘至 %s%s。" % (label, len(clean), limit, path,
                                                "（同内容已存在，复用）" if reused else ""),
        "下面只给头 %d 字和尾 %d 字；需要具体段落请用 grep 或分段读取。]" % (HEAD_CHARS, len(tail)),
        "",
        head,
    ]
    if tail:
        parts += ["", "……（中略 %d 字符）……" % (len(clean) - HEAD_CHARS - len(tail)), "", tail]
    return "\n".join(parts)
