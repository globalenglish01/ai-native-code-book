"""产品示例：跨会话长期记忆的个人助理。

真实产品形态：用户在不同会话（不同thread_id/不同天）里提到的偏好/事实
被提取并存进长期记忆，下次对话开始时，把"最近N条记忆"渲染进system prompt
的上下文里，让助理"记得"之前提到过的信息——同时对话历史本身要纳入
token预算（不能无限增长），用户要求"忘记我"时，必须能彻底删除这个人
的全部记忆（GDPR式遗忘权，MemoryStore协议把delete_by_owner设计成
一等公民正是为了这个场景）。

组合的包：ainative-memory + ainative-prompt。
"""

from __future__ import annotations

from ainative_core.protocols import MemoryEntry
from ainative_memory.history_budget import trim_history_to_budget
from ainative_memory.rendering import render_memory_entries
from ainative_memory.store import InMemoryMemoryStore
from ainative_prompt.store import InMemoryPromptStore, load_prompt


class PersonalAssistant:
    """每次对话开始时加载最近记忆 + 裁剪历史 + 加载system prompt。"""

    def __init__(self, agent_name: str = "personal_assistant", *, max_memories: int = 5) -> None:
        self.agent_name = agent_name
        self.max_memories = max_memories
        self.memory_store = InMemoryMemoryStore()
        self.prompt_store = InMemoryPromptStore()
        self._sequence_counters: dict[str, int] = {}

    async def remember(self, user_id: str, fact: str) -> None:
        """记住一条关于用户的事实（真实项目里，这条内容通常是LLM从对话里提取出的摘要）。"""
        seq = self._sequence_counters.get(user_id, 0)
        self._sequence_counters[user_id] = seq + 1
        await self.memory_store.append(MemoryEntry(owner_id=user_id, sequence=seq, content=fact))

    async def start_session(self, user_id: str, thread_id: str) -> str:
        """开始一次新会话：加载system prompt + 拼装最近记忆，返回完整的上下文前缀。"""
        system_prompt = await load_prompt(
            self.prompt_store, self.agent_name, thread_id=thread_id,
            default="You are a helpful personal assistant.",
        )
        recent_memories = await self.memory_store.load_recent(user_id, max_items=self.max_memories)
        memory_block = render_memory_entries(recent_memories, header_template="## Fact #{sequence}")

        if memory_block:
            return f"{system_prompt}\n\nWhat you remember about this user:\n{memory_block}"
        return system_prompt

    def build_bounded_history(self, history: list[dict[str, str]], *, max_tokens: int) -> list[dict[str, str]]:
        """把对话历史裁剪到token预算内——这是默认行为，不是可选项（呼应
        ainative-memory的设计原则：动态内容必须纳入统一token预算）。"""
        return trim_history_to_budget(history, max_tokens=max_tokens)

    async def forget_user(self, user_id: str) -> int:
        """用户行使"被遗忘权"时调用——彻底删除这个人的全部长期记忆，返回删除条数。"""
        return await self.memory_store.delete_by_owner(user_id)


async def main() -> None:
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    assistant = PersonalAssistant()
    user_id = "user-42"

    # Session 1: user mentions some preferences.
    await assistant.remember(user_id, "User prefers concise answers, no bullet points.")
    await assistant.remember(user_id, "User is working on a Python project called 'ai-native-framework'.")
    await assistant.remember(user_id, "User's timezone is UTC+8.")

    context1 = await assistant.start_session(user_id, thread_id="session-1")
    print("=== Session 1 context (first time, no memories yet at session start in a real flow) ===")
    print(context1)

    # Session 2 (a different day, different thread_id): the assistant should
    # now recall what it learned in session 1.
    context2 = await assistant.start_session(user_id, thread_id="session-2")
    print("\n=== Session 2 context (recalls facts from session 1) ===")
    print(context2)

    # Simulate an unbounded-growth conversation history and confirm it gets trimmed.
    long_history = [{"role": "user", "content": "message " * 50} for _ in range(30)]
    trimmed = assistant.build_bounded_history(long_history, max_tokens=500)
    print(f"\nhistory trimmed from {len(long_history)} to {len(trimmed)} messages to fit the token budget")

    # User exercises their right to be forgotten.
    deleted = await assistant.forget_user(user_id)
    print(f"\nuser exercised right-to-be-forgotten: {deleted} memories deleted")
    context3 = await assistant.start_session(user_id, thread_id="session-3")
    print(f"session 3 context after forgetting (no memory block): {context3!r}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
