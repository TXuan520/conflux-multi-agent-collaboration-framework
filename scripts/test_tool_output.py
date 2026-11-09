# -*- coding: utf-8 -*-
"""tool_output 自测：脱敏、截断、哈希去重三条红线。"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tool_output as T


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  <- " + str(detail)[:200]))
    return bool(cond)


def main():
    tmp = tempfile.mkdtemp(prefix="toolout-")
    ok = True
    try:
        # 1. 短文本原样透传
        s = "短输出"
        ok &= check("短文本原样返回", T.condense(s, cache_dir=tmp) == s)

        # 2. 超长文本落盘 + 头尾预览
        big = "行%d：内容 ABCDEFG\n" % 0 + ("x" * 60 + "\n") * 200
        out = T.condense(big, cache_dir=tmp)
        files = os.listdir(tmp)
        ok &= check("超限落盘一个文件", len(files) == 1, files)
        ok &= check("回填里带路径", "tool_cache" in out or tmp in out)
        ok &= check("回填不含全文", len(out) < len(big), (len(out), len(big)))

        # 3. 哈希去重：同样内容三次只写一份
        before = len(os.listdir(tmp))
        T.condense(big, cache_dir=tmp)
        T.condense(big, cache_dir=tmp)
        ok &= check("同内容不重复落盘", len(os.listdir(tmp)) == before, os.listdir(tmp))

        # 4. 落盘前脱敏：明文 key 不许进文件
        leaky = ("sk-abcdefgh12345678 " + "y" * 5000 + "\n"
                 "api_key = \"supersecretvalue\"\n"
                 "Authorization: Bearer abcdefghijklmnop\n"
                 + ("-" * 5 + "BEGIN RSA PRIVATE KEY" + "-" * 5 + "\nMIIEfake\n" + "-" * 5 + "END RSA PRIVATE KEY" + "-" * 5))
        out2 = T.condense(leaky, cache_dir=tmp)
        dumped = ""
        for fn in os.listdir(tmp):
            if fn.endswith(".txt"):
                dumped += open(os.path.join(tmp, fn), encoding="utf-8").read()
        ok &= check("缓存里无明文 sk- key", "sk-abcdefgh" not in dumped)
        ok &= check("缓存里无明文 password/api_key 值", "supersecretvalue" not in dumped)
        ok &= check("缓存里无 Bearer 值", "abcdefghijklmnop" not in dumped)
        ok &= check("缓存里无私钥块", "MIIEfake" not in dumped)
        ok &= check("回填也无明文", "sk-abcdefgh" not in out2 and "supersecretvalue" not in out2)

        # 5. 脱敏先于哈希：同一份含密钥文本与脱敏后文本落到同一文件
        import hashlib
        plain = "x" * 5000 + " sk-abcdefgh12345678"
        T.condense(plain, cache_dir=tmp)
        want = hashlib.sha256(T.sanitize(plain).encode()).hexdigest()[:16] + ".txt"
        ok &= check("哈希基于脱敏后内容", os.path.exists(os.path.join(tmp, want)), want)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nRESULT:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
