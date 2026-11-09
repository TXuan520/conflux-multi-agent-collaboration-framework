"""Regression tests for paged reads and incomplete-read overwrite protection."""
import confluxconfig as cfg

import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import agent_loop as loop


def main():
    with tempfile.TemporaryDirectory(prefix="file-read-safety-") as temp:
        path = Path(temp) / "sample.txt"
        original = "开头标记" + "甲" * 9992 + "结尾标记"
        assert len(original) == 10000
        path.write_text(original, encoding="utf-8")
        runner = loop.Runner("arch", "读取测试文件", temp)
        runner.roots = (temp.lower(),)

        def read(offset=0, **kwargs):
            return runner.run_tool("read_file", dict(path=str(path), offset=offset, **kwargs))

        def write(content):
            return runner.run_tool("write_file", {"path": str(path), "content": content})

        def reset():
            path.write_text(original, encoding="utf-8")
            runner._read_ranges.clear()

        first = read()
        assert first.split("\n[WARNING:")[0] == original[:4000]
        assert "PARTIAL FILE!" in first and "next offset=4000" in first
        assert "不要拿局部内容去 writefile" in first
        print("PASS first page: 4000/10000 characters; warning; next offset=4000")
        result = write(original[:4000])
        assert result.startswith("[BLOCKED]") and path.read_text(encoding="utf-8") == original
        print("PASS partial-read overwrite blocked; original remains 10000 characters")
        second, third = read(4000), read(8000)
        assert second.split("\n[WARNING:")[0] == original[4000:8000]
        assert third.split("\n[WARNING:")[0] == original[8000:]
        assert "next offset=8000" in second and "EOF" in third
        assert write(original[:4000]).startswith("已写入")
        assert path.read_text(encoding="utf-8") == original[:4000]
        print("PASS all three pages read: shorter overwrite allowed")
        assert write(original[:3999]).startswith("已写入")
        print("PASS successful write refreshes coverage for a second shorter write")

        for covered in (False, True):
            reset()
            if covered:
                read()
            result = runner.run_tool("patch_file", {"path": str(path), "old": "开头标记", "new": "头"})
            assert result.startswith("已替换")
            assert path.read_text(encoding="utf-8") == original.replace("开头标记", "头", 1)
            assert write("头").startswith("已写入")
        print("PASS patch_file allowed both without reads and after partial reads")
        print("PASS successful patch refreshes coverage for subsequent write")

        reset()
        read(0, limit=4000)
        read(8000)
        assert write(original[:9999]).startswith("[BLOCKED]")
        read(2000, limit=6000)
        assert write(original[:9999]).startswith("已写入")
        print("PASS gaps block one-character truncation; overlapping pages merge")

        reset()
        read(0, limit=8000)
        read(8000)
        changed = original[:5000] + "乙" + original[5001:]
        path.write_text(changed, encoding="utf-8")
        assert write(changed[:-1]).startswith("[BLOCKED]")
        assert path.read_text(encoding="utf-8") == changed
        print("PASS same-length external edit invalidates previous read coverage")

        reset()
        other = loop.Runner("mimo", "另一 agent", temp)
        other.roots = runner.roots
        read(0, limit=8000)
        read(8000)
        assert other.run_tool("write_file", {"path": str(path), "content": "短"}).startswith("[BLOCKED]")
        print("PASS read coverage isolated between agents")

        reset()
        capped = read(0, limit=99999)
        assert capped.split("\n[WARNING:")[0] == original[:8000]
        assert "next offset=8000" in capped
        assert read("8000", limit="4000").split("\n[WARNING:")[0] == original[8000:]
        for args in ({"offset": -1}, {"limit": 0}, {"offset": True}, {"limit": 1.5}):
            assert runner.run_tool("read_file", dict(path=str(path), **args)).startswith("[BLOCKED]")
        print("PASS 8000 cap, XML numeric parameters, invalid argument rejection")

        reset()
        path.write_text("小文件", encoding="utf-8")
        assert read() == "小文件"
        assert write("小").startswith("已写入")
        new = Path(temp) / "new.txt"
        assert runner.run_tool("write_file", {"path": str(new), "content": "新文件"}).startswith("已写入")
        print("PASS complete small-file read and new-file write")

        # Exercise the real model loop to ensure read pages survive output fitting.
        reset()
        observed = []
        def chat(messages):
            if not observed:
                observed.append(True)
                return {"role": "assistant", "tool_calls": [{"id": "page", "function": {"name": "read_file", "arguments":
                        json.dumps({"path": str(path), "limit": 8000})}}]}
            page = next(m["content"] for m in messages if m["role"] == "tool")
            assert page.split("\n[WARNING:")[0] == original[:8000]
            assert "next offset=8000" in page
            return {"role": "assistant", "content": "完成"}

        runner.chat = chat
        with patch.object(runner, "_context_block", return_value=""), patch.object(
                runner, "_save_turn_memory"), patch.object(loop, "log"), patch.object(
                loop, "_fit_tool_output", side_effect=AssertionError("page was condensed twice")):
            assert loop.Runner.run.__wrapped__(runner) == "完成"
        print("PASS normal model-loop delivery preserves full page and warning")

        protected = loop.Runner("arch", "保护验证", temp)
        with patch("builtins.open", side_effect=AssertionError("protected path reached open")):
            for token in (cfg.root_path('data/hub/admin.token'), cfg.root_path('data/admin.token')):
                result = protected.run_tool("read_file", {"path": token})
                assert "拒绝访问受保护文件" in result, result
                print("PASS read_file blocked", token)
            for name in ("write_file", "patch_file"):
                result = protected.run_tool(name, {"path": cfg.root_path('data/hub/providers.json'),
                                                   "content": "{}", "old": "x", "new": "y"})
                assert "拒绝改写受保护文件" in result, result
                print("PASS", name, "blocked providers.json before open")

        fake_token = Path(temp) / "admin.token"
        fake_token.write_text("never-expose-this-marker", encoding="utf-8")
        with patch.object(loop, "DENY_READ_FILES", loop.DENY_READ_FILES + (str(fake_token).lower(),)):
            result = runner.run_tool("grep", {"path": temp, "pattern": "never-expose"})
            assert result == "(无命中)", result
        print("PASS recursive grep skips protected token")
        with patch.object(loop.subprocess, "Popen") as popen:
            for command in (cfg.expand('Get-Content ${CONFLUX_ROOT}\\data\\hub\\ADMIN.TOKEN'),
                            cfg.expand('Set-Content ${CONFLUX_ROOT}\\data\\hub\\providers.json "{}"')):
                assert "命令被拒绝" in protected.run_tool("run_cmd", {"cmd": command})
            popen.assert_not_called()
        print("PASS run_cmd blocks token and provider paths before process creation")
        print("ALL PASS")


if __name__ == "__main__":
    main()
