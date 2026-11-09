"""Per-member recent compressed memory; never part of a session identity."""
from agent_memory import CompressedMemory


def build_recent_memory_block(agent_id, query='', budget=2400):
    content = CompressedMemory().context(agent_id, query, budget=budget)
    return '\n【本人近期压缩记忆·仅供历史参考】\n' + content if content else ''
