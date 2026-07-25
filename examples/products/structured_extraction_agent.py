"""产品示例：结构化数据抽取Agent（发票/收据信息抽取）。

真实产品形态：把非结构化文本（发票、收据、报销单）抽取成结构化字段
（金额、日期、供应商名）——LLM抽取结果不总是一次就对（金额格式错误、
必填字段缺失），需要校验失败后带着"具体哪里错了"的反馈重试，而不是
盲目重试相同的prompt；同时重试次数要有上限（复用guardrail的
`AgentLimits`，按"这个抽取任务类型"而不是"某个具体agent"注册护栏），
避免对着解析不出来的输入无限重试。最终抽取结果要过一道治理Gate才能
自动入库，而不是抽取"看起来跑通了"就直接写数据库。

组合的包：ainative-guardrail（重试上限）+ ainative-eval（入库前校验Gate）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ainative_core.protocols import GateCheck, GateResult
from ainative_eval.gate import GREEN, RED, Gate
from ainative_guardrail.limits import AgentLimits

REQUIRED_FIELDS = ("vendor", "amount", "date")
_AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ExtractionResult:
    fields: dict[str, str]
    attempts: int
    validation_errors: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors


def validate_extraction(fields: dict[str, str]) -> list[str]:
    """校验抽取结果，返回具体错误列表（空列表表示校验通过）——这份错误列表
    会原样喂给下一次重试的prompt，而不是笼统地说"再试一次"。"""
    errors = []
    for field in REQUIRED_FIELDS:
        if not fields.get(field):
            errors.append(f"missing required field '{field}'")
    if "amount" in fields and fields["amount"] and not _AMOUNT_RE.match(fields["amount"]):
        errors.append(f"'amount' must be a plain decimal number, got {fields['amount']!r}")
    if "date" in fields and fields["date"] and not _DATE_RE.match(fields["date"]):
        errors.append(f"'date' must be in YYYY-MM-DD format, got {fields['date']!r}")
    return errors


class StructuredExtractionAgent:
    """带校验反馈重试的结构化抽取agent——重试次数上限由`AgentLimits`管理。"""

    def __init__(self, extract_fn, *, task_name: str = "invoice_extraction") -> None:
        self.extract_fn = extract_fn
        self.task_name = task_name
        self.limits = AgentLimits()
        self.limits.register(task_name, max_consecutive_errors=3)

    def extract(self, document_text: str) -> ExtractionResult:
        max_attempts = self.limits.max_consecutive_errors(self.task_name)
        errors: list[str] = []
        fields: dict[str, str] = {}

        for attempt in range(1, max_attempts + 1):
            fields = self.extract_fn(document_text, previous_errors=errors)
            errors = validate_extraction(fields)
            if not errors:
                return ExtractionResult(fields=fields, attempts=attempt, validation_errors=[])

        return ExtractionResult(fields=fields, attempts=max_attempts, validation_errors=errors)

    def filing_gate(self, result: ExtractionResult) -> Gate:
        def check_extraction_valid() -> GateResult:
            status = GREEN if result.is_valid else RED
            detail = "all required fields present and well-formed" if result.is_valid else "; ".join(result.validation_errors)
            return GateResult(dimension="ExtractionValidity", gating=True, status=status, detail=detail)

        return Gate([GateCheck(name="extraction_valid", gating=True, check_fn=check_extraction_valid)])


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # A scripted "LLM" that gets the amount format wrong on the first try,
    # then corrects it once it sees the specific validation error.
    call_count = 0

    def flaky_extract(_document_text: str, *, previous_errors: list[str]) -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        if not previous_errors:
            return {"vendor": "Acme Corp", "amount": "$1,250.00", "date": "2026-07-20"}
        return {"vendor": "Acme Corp", "amount": "1250.00", "date": "2026-07-20"}

    agent = StructuredExtractionAgent(flaky_extract)
    result = agent.extract("Invoice from Acme Corp for $1,250.00 dated July 20, 2026")
    print(f"extraction succeeded after {result.attempts} attempt(s): {result.fields}")

    decision = agent.filing_gate(result).run()
    print(f"filing gate passed: {decision.passed}")

    # An extraction that never converges within the retry budget.
    def always_broken_extract(_document_text: str, *, previous_errors: list[str]) -> dict[str, str]:
        return {"vendor": "Mystery Vendor", "amount": "not-a-number", "date": "07/20/2026"}

    stubborn_agent = StructuredExtractionAgent(always_broken_extract)
    stubborn_result = stubborn_agent.extract("garbled unreadable document")
    print(f"\nstubborn extraction gave up after {stubborn_result.attempts} attempts")
    print(f"validation errors: {stubborn_result.validation_errors}")

    stubborn_decision = stubborn_agent.filing_gate(stubborn_result).run()
    print(f"filing gate passed: {stubborn_decision.passed}")
    for blocker in stubborn_decision.blockers:
        print(f"  blocker: {blocker}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
