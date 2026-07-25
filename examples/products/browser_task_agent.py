"""产品示例：浏览器自动化任务Agent。

真实产品形态：Agent通过MCP调用浏览器工具（click/navigate/snapshot等）完成
一个多步骤任务；护栏中间件防止"工具调用次数失控"（比如卡在某个页面反复
截图）和"连续同一工具失败"（比如同一个选择器反复点不中）——这是真实生产
事故驱动出来的护栏（原型项目里记录过一次真实的单次运行$25成本失控事故）。

组合的包：ainative-guardrail + ainative-mcp。
"""

from __future__ import annotations

from ainative_guardrail.budget_middleware import ConsecutiveCallGuardMiddleware, MCPCallLimiterMiddleware
from ainative_mcp.audit import InMemoryToolCallAuditLog, ToolCallRecord
from ainative_mcp.config import build_mcp_config, build_safe_env
from langchain_core.messages import ToolMessage


class _FakeToolCallRequest:
    """最小化模拟`langgraph.prebuilt.tool_node.ToolCallRequest`——只提供护栏
    中间件实际会读取的`tool_call["name"]`/`["id"]`。"""

    def __init__(self, tool_name: str, call_id: str) -> None:
        self.tool_call = {"name": tool_name, "args": {}, "id": call_id}


class BrowserTaskAgent:
    """模拟一个受护栏保护的浏览器自动化任务执行循环。

    调用链：`call_tool()` -> `MCPCallLimiterMiddleware.wrap_tool_call()` ->
    （放行则继续）-> `ConsecutiveCallGuardMiddleware.wrap_tool_call()` ->
    （放行则真正执行，这里只是模拟）-> 记审计日志。任何一层护栏短路，
    工具调用都不会"真正执行"，只会记一条被拦截的审计记录。
    """

    def __init__(self, agent_name: str = "browser_agent") -> None:
        self.agent_name = agent_name
        self.mcp_config = build_mcp_config(
            "browser", "stdio", command="npx", args=["-y", "@playwright/mcp@latest"], env=build_safe_env(),
        )
        self.call_limiter = MCPCallLimiterMiddleware(per_tool_limit={
            "browser_snapshot": 5, "browser_click": 10, "browser_navigate": 5,
        })
        self.stall_guard = ConsecutiveCallGuardMiddleware(
            stall_tools=frozenset({"browser_snapshot"}),
            progress_tools=frozenset({"browser_click", "browser_navigate"}),
            max_stall_calls=3,
        )
        self.audit_log = InMemoryToolCallAuditLog()
        self._call_counter = 0

    def call_tool(self, tool_name: str, *, simulate_success: bool = True) -> ToolMessage:
        """模拟一次工具调用，经过两层护栏中间件，返回最终的`ToolMessage`
        （护栏短路时是一条`status="error"`的合成消息；真正执行时是模拟的结果）。"""
        self._call_counter += 1
        call_id = f"call-{self._call_counter}"
        request = _FakeToolCallRequest(tool_name, call_id)

        def real_execution(_req: _FakeToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content=f"{tool_name} executed",
                tool_call_id=call_id, name=tool_name,
                status="success" if simulate_success else "error",
            )

        def through_stall_guard(req: _FakeToolCallRequest) -> ToolMessage:
            return self.stall_guard.wrap_tool_call(req, real_execution)

        result = self.call_limiter.wrap_tool_call(request, through_stall_guard)

        blocked_by = None
        if "[budget]" in result.content and "Call cap" in result.content:
            blocked_by = "MCPCallLimiterMiddleware"
        elif "[budget]" in result.content and "without making progress" in result.content:
            blocked_by = "ConsecutiveCallGuardMiddleware"

        self.audit_log.record(ToolCallRecord(
            tool_name=tool_name, agent_name=self.agent_name, status=result.status, duration_ms=0.0,
            error_message=f"blocked by {blocked_by}" if blocked_by else None,
        ))
        return result


async def main() -> None:
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    agent = BrowserTaskAgent()
    print(f"MCP config: transport={agent.mcp_config['browser']['transport']}")

    # Simulate a task loop that repeatedly snapshots without making progress —
    # should get stopped by ConsecutiveCallGuardMiddleware after 3 stall calls.
    for i in range(5):
        result = agent.call_tool("browser_snapshot")
        print(f"call {i}: status={result.status}, content={result.content[:60]}")

    print(f"\naudit log error rate for browser_snapshot: {agent.audit_log.error_rate('browser_snapshot'):.0%}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
