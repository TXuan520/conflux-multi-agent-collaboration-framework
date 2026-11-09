# -*- coding: utf-8 -*-
"""路由名单派生回归测试。

红线（文档 v1.2 验收第 1 条）：在 providers.json 里新增一个虚拟角色后，
不改任何 Python 正则代码，「>>> 显示名」必须能正确路由到它的 id。

跑法：python test_dynamic_routing.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routing


def _reset_cache():
    routing._CACHE["key"] = None
    routing._CACHE["providers"] = None


def _write(path, providers):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "providers": providers}, f, ensure_ascii=False)


BASE = [
    {"id": "dsh", "display_name": "DeepSeek", "aliases": ["deepseek", "飞鱼"]},
    {"id": "arch", "display_name": "Argon", "aliases": ["arch", "架构师"]},
    {"id": "doubao", "display_name": "Seedance", "aliases": ["doubao", "豆包"]},
    {"id": "mimo", "display_name": "MiMo", "aliases": ["mimo", "小米"]},
    {"id": "human", "display_name": "炫炫", "aliases": ["human", "炫炫", "主人"]},
]


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  <- " + str(detail)))
    return bool(cond)


def main():
    tmp = tempfile.mkdtemp(prefix="route-test-")
    path = os.path.join(tmp, "providers.json")
    ok = True

    # 1. 存量别名全部可解析
    _write(path, BASE)
    _reset_cache()
    routing.PROVIDERS_FILE = path
    for alias, want in (("Argon", "arch"), ("架构师", "arch"), ("豆包", "doubao"),
                        ("炫炫", "human"), ("deepseek", "dsh")):
        got = routing.resolve_names(alias)
        ok &= check("存量别名 %s -> %s" % (alias, want), got == [want], got)

    # 2. 新增虚拟角色：不改代码也要能路由（核心红线）
    virt = list(BASE)
    virt.append({"id": "qwen", "display_name": "Qwen", "aliases": ["qwen", "千问"]})
    _write(path, virt)
    _reset_cache()   # 模拟 mtime 变化后的热重载
    ok &= check("新角色进入 target_names", "Qwen" in routing.target_names(), routing.target_names()[-3:])
    ok &= check("新角色进入箭头正则", "Qwen" in routing.arrow_alt(), routing.arrow_alt()[:80])
    ok &= check(">>> Qwen 解析到 qwen", routing.resolve_names("Qwen") == ["qwen"], routing.resolve_names("Qwen"))
    ok &= check(">>> 千问 解析到 qwen", routing.resolve_names("千问") == ["qwen"], routing.resolve_names("千问"))

    # 3. 箭头行被真正识别成「收件人行」
    pat = r"^((?:(?:(?:%s)|[@+,/，、和与及\s])\s*)+)(.*)$" % routing.arrow_alt()
    ok &= check(">>> Qwen 命中断行正则", bool(re.match(pat, "Qwen 你好", re.I)),
                re.match(pat, "Qwen 你好"))

    # 4. 多目标 / 中文混排
    ok &= check("多目标 炫炫和Argon", set(routing.resolve_names("炫炫和Argon")) == {"human", "arch"},
                routing.resolve_names("炫炫和Argon"))
    ok &= check("多目标 千问+Mimo", set(routing.resolve_names("千问+Mimo")) == {"qwen", "mimo"},
                routing.resolve_names("千问+Mimo"))

    # 5. 关闭的角色不进名单
    off = [dict(p) for p in virt]
    for p in off:
        if p["id"] == "qwen":
            p["enabled"] = False
    _write(path, off)
    _reset_cache()
    ok &= check("enabled=false 的角色不参与路由", routing.resolve_names("Qwen") == [], routing.resolve_names("Qwen"))

    # 6. 文件坏掉时不能把 hub 拖死
    with open(path, "w", encoding="utf-8") as f:
        f.write("{坏掉的 json")
    _reset_cache()
    ok &= check("坏文件退回内置名单", routing.resolve_names("Argon") == ["arch"], routing.resolve_names("Argon"))

    print("\nRESULT:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
