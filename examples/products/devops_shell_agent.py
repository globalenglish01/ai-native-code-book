"""产品示例：DevOps运维Shell工具Agent。

真实产品形态：Agent执行诊断类shell命令（读日志、查配置、跑`git log`等）
来排查生产问题——这类命令的输出是不可信的：日志文件里可能意外打印过
一次性调试用的API Key，配置文件回显可能带着数据库连接串。这些输出会
被原样喂回LLM的上下文（甚至可能被总结后展示给on-call工程师），如果不
清洗，敏感信息就从"日志文件里的一行"变成了"聊天记录/事件复盘文档里的
一行"——这是真实发生过的凭据泄露路径，不是假设性风险。每次工具调用
无论是否触发了清洗，都要留下审计记录，供事后复查。

组合的包：ainative-security（输出清洗）+ ainative-mcp（调用审计）。
"""

from __future__ import annotations

import time

from ainative_mcp.audit import InMemoryToolCallAuditLog, ToolCallRecord
from ainative_security.output_safety import strip_injection


class DevOpsShellAgent:
    """执行只读诊断命令，清洗输出中的敏感信息，并记录审计日志。"""

    def __init__(self, agent_name: str = "devops_agent") -> None:
        self.agent_name = agent_name
        self.audit_log = InMemoryToolCallAuditLog()

    def run_command(self, command: str, raw_output: str) -> str:
        """执行一条命令（这里`raw_output`模拟命令的真实stdout），返回清洗后的
        安全输出，同时记录审计日志（清洗前后不同即视为触发了敏感信息拦截）。"""
        start = time.monotonic()
        safe_output = strip_injection(raw_output)
        triggered = safe_output != raw_output

        self.audit_log.record(ToolCallRecord(
            tool_name="run_shell_command", agent_name=self.agent_name, status="success",
            duration_ms=(time.monotonic() - start) * 1000,
            input_summary={"command": command},
            output_summary={"redacted": triggered},
        ))
        return safe_output

    def calls_with_redaction(self) -> list[ToolCallRecord]:
        """审计日志里所有触发过敏感信息清洗的调用——供安全团队复查具体是哪些
        命令曾经差点把密钥泄露进上下文。"""
        return [r for r in self.audit_log.all() if r.output_summary.get("redacted")]


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    agent = DevOpsShellAgent()

    safe_log = agent.run_command("tail -n 20 /var/log/app.log", "2026-07-26 12:00:01 INFO request handled in 42ms")
    print(f"tail app.log -> redacted? {'no' if safe_log == '2026-07-26 12:00:01 INFO request handled in 42ms' else 'yes'}")
    print(f"  output: {safe_log}")

    leaked_config = agent.run_command(
        "cat /etc/app/config.env",
        'DATABASE_URL=postgres://user:pass@host/db\napi_key: "sk-abcdefghijklmnopqrstuvwxyz123456"',
    )
    print(f"\ncat config.env -> output: {leaked_config!r}")

    git_log = agent.run_command("git log --oneline -5", "a1b2c3d fix: resolve login timeout\nd4e5f6a feat: add retry logic")
    print(f"\ngit log -> output: {git_log!r}")

    flagged = agent.calls_with_redaction()
    print(f"\n{len(flagged)} of {len(agent.audit_log.all())} commands triggered secret redaction:")
    for record in flagged:
        print(f"  - {record.input_summary['command']}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
