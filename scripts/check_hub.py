# -*- coding: utf-8 -*-
"""
hub.py 交付门槛自检（项目 5-1，2026-11-04 缩进事故后立的规矩）：

改完 hub.py 必须跑本脚本，全绿才能报「已完成」：
    python ${CONFLUX_ROOT}\\scripts\\check_hub.py

四道闸：
    1. py_compile   语法编译
    2. ast.parse    结构解析（缩进/块级错位会在这里或编译挂）
    3. import hub   实际导入一次，模块级代码真跑一遍（DeepSeek 明确要求）
    4. /api/status  运行态探活（只读 GET；hub 在跑才查，没跑只提示不判失败）

任何一闸失败 → 退出码 1，禁止交付。
"""
import ast
import os
import py_compile
import sys
import tempfile

HUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hub.py")
fail = []


def gate(name, fn):
    try:
        fn()
        print("[PASS] %s" % name)
    except Exception as e:
        fail.append(name)
        print("[FAIL] %s -> %s: %s" % (name, type(e).__name__, e))


def g_compile():
    with tempfile.TemporaryDirectory() as td:
        py_compile.compile(HUB, cfile=os.path.join(td, "hub.pyc"), doraise=True)


def g_ast():
    src = open(HUB, encoding="utf-8").read()
    tree = ast.parse(src)
    # 结构闸：serve_forever 必须在 main 函数体内，不能落进 except 或模块级
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    if not mains:
        raise RuntimeError("找不到 main()")
    hits = [n for n in ast.walk(mains[0]) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == "serve_forever"]
    if not hits:
        raise RuntimeError("serve_forever() 不在 main() 内，main() 尾部缩进又错位了")
    # 结构闸：模块级不得出现裸 serve_forever 调用
    for n in tree.body:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) \
                and getattr(n.value.func, "attr", "") == "serve_forever":
            raise RuntimeError("serve_forever() 落到了模块级")


def g_import():
    scripts = os.path.dirname(HUB)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    if "hub" in sys.modules:
        del sys.modules["hub"]
    import hub  # noqa: F401  模块级代码必须能完整跑完
    if not hasattr(hub, "main"):
        raise RuntimeError("import 后缺 main()")


def g_alive():
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8777/api/status", timeout=3) as r:
            print("       /api/status -> %s（hub 在跑）" % r.status)
    except Exception as e:
        print("       /api/status 不通（hub 可能没在跑，不判失败）：%s" % e)


gate("py_compile", g_compile)
gate("ast.parse+结构检查", g_ast)
gate("import hub", g_import)
g_alive()

if fail:
    print("\n结果：FAIL（%s），禁止交付" % ", ".join(fail))
    sys.exit(1)
print("\n结果：全绿，可以交付")
