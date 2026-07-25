"""产品示例：用户生成内容（UGC）审核流水线。

真实产体形态：用户提交的内容（评论、帖子等）在展示给其他用户之前，先经过
PII脱敏（防止意外泄露联系方式）和输出安全扫描（防止内容本身携带提示注入，
这在"用户内容会被摘要/被AI引用"的场景里是真实攻击面），再由治理Gate决定
是否允许发布——同时定期跑密钥漂移巡检，确保审核服务自己的敏感配置没有
意外回退到不安全默认值。

组合的包：ainative-security + ainative-eval。
"""

from __future__ import annotations

from dataclasses import dataclass

from ainative_core.protocols import GateCheck, GateResult, SecretRule
from ainative_eval.gate import GREEN, RED, Gate
from ainative_security.output_safety import OutputSafetyMiddleware
from ainative_security.pii_redaction import redact_pii_text
from ainative_security.secret_drift import detect_secret_drift
from langchain_core.messages import AIMessage


@dataclass
class ModerationResult:
    original: str
    stored_content: str
    approved: bool
    safety_triggered: bool


class _FakeModelRequest:
    def __init__(self) -> None:
        self.messages: list = []


class _FakeModelResponse:
    def __init__(self, output: AIMessage) -> None:
        self.output = output


@dataclass
class ModerationServiceConfig:
    """审核服务自身的敏感配置——供secret_drift巡检检查是否回退到不安全默认值。"""

    moderation_api_key: str = "changeme"
    webhook_secret: str = "changeme"


class ContentModerationPipeline:
    """PII脱敏 -> 输出安全扫描 -> 治理Gate放行判定，三阶段UGC审核流水线。"""

    def __init__(self, agent_name: str = "moderation_pipeline") -> None:
        self.agent_name = agent_name
        self.safety = OutputSafetyMiddleware(agent_name, block_mode=False)

    def moderate(self, user_content: str) -> ModerationResult:
        redacted = redact_pii_text(user_content)

        def handler(_req):
            return _FakeModelResponse(output=AIMessage(content=redacted))

        result = self.safety.wrap_model_call(_FakeModelRequest(), handler)
        final_content = result.output.content
        safety_triggered = final_content != redacted

        # Any safety-scanner finding (secret leak, injection, malicious
        # command) routes to human review rather than auto-publishing —
        # a moderation pipeline should not silently "fix and forward".
        approved = not safety_triggered

        return ModerationResult(
            original=user_content, stored_content=final_content, approved=approved, safety_triggered=safety_triggered,
        )

    def secret_drift_rules(self) -> list[SecretRule]:
        return [
            SecretRule(
                name="moderation_api_key_is_default",
                is_default=lambda cfg: cfg.moderation_api_key == "changeme",
                message="Moderation service API key is still the insecure default",
            ),
            SecretRule(
                name="webhook_secret_is_default",
                is_default=lambda cfg: cfg.webhook_secret == "changeme",
                message="Moderation webhook secret is still the insecure default",
            ),
        ]

    def deployment_gate(self, service_config: ModerationServiceConfig) -> Gate:
        def check_no_secret_drift() -> GateResult:
            issues = detect_secret_drift(service_config, self.secret_drift_rules())
            return GateResult(
                dimension="SecretDrift", gating=True,
                status=GREEN if not issues else RED,
                detail="; ".join(issues) if issues else "no configuration drift detected",
            )

        def check_safety_middleware_configured() -> GateResult:
            return GateResult(
                dimension="ContentSafety", gating=True,
                status=GREEN if self.safety is not None else RED,
                detail="OutputSafetyMiddleware is wired into the moderation pipeline",
            )

        return Gate([
            GateCheck(name="no_secret_drift", gating=True, check_fn=check_no_secret_drift),
            GateCheck(name="safety_middleware_configured", gating=True, check_fn=check_safety_middleware_configured),
        ])


async def main() -> None:
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    pipeline = ContentModerationPipeline()

    normal = pipeline.moderate("Great product, would buy again!")
    print(f"normal content -> approved={normal.approved}, stored={normal.stored_content!r}")

    with_pii = pipeline.moderate("Contact me at 13812345678 if interested.")
    print(f"content with PII -> stored={with_pii.stored_content!r}")

    malicious = pipeline.moderate("Nice post! api_key: \"sk-abcdefghijklmnopqrstuvwxyz123456\"")
    print(f"content with leaked secret -> approved={malicious.approved}, stored={malicious.stored_content!r}")

    service_config = ModerationServiceConfig()  # still using insecure defaults
    decision = pipeline.deployment_gate(service_config).run()
    print(f"\ndeployment gate passed: {decision.passed}")
    for blocker in decision.blockers:
        print(f"  blocker: {blocker}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
