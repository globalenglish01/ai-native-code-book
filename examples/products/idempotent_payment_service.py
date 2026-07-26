"""产品示例：幂等支付/订单提交服务。

真实产品形态：客户端网络抖动会重试提交同一笔支付请求——如果没有幂等
保护，同一张信用卡可能被重复扣款。真实设计要点（对应checklist D类）：
"占用幂等键"必须一步到位完成（避免两个几乎同时到达的请求都误以为
自己是第一个）；重复请求命中"仍在处理中"和命中"已有最终结果"必须
区分处理（前者告知客户端稍后重试，后者直接返回缓存的支付结果，不
重新扣款）；处理过程中途失败时幂等键必须被释放，否则用户会在整个
TTL窗口期内都无法重新提交同一笔支付，即使故障早就恢复了。

组合的包：ainative-guardrail（幂等键管理）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ainative_guardrail import (
    DuplicateOperationError,
    IdempotencyStatus,
    InMemoryIdempotencyStore,
    idempotent_operation,
)


@dataclass
class PaymentResult:
    charge_id: str
    amount_cents: int
    status: str


class PaymentGatewayError(RuntimeError):
    """模拟下游支付网关暂时不可用。"""


class PaymentService:
    """幂等的支付提交服务——同一个幂等键的重复提交不会重复扣款。"""

    def __init__(self) -> None:
        self.store = InMemoryIdempotencyStore()
        self._charge_attempts = 0

    def _call_payment_gateway(self, order_id: str, amount_cents: int, *, simulate_failure: bool) -> PaymentResult:
        self._charge_attempts += 1
        if simulate_failure:
            raise PaymentGatewayError("payment gateway temporarily unavailable")
        return PaymentResult(charge_id=f"ch_{order_id}", amount_cents=amount_cents, status="succeeded")

    def charge(self, order_id: str, amount_cents: int, *, simulate_failure: bool = False) -> PaymentResult | str:
        """提交一次支付；`order_id`本身就是幂等键——同一个订单号无论
        客户端重试多少次，下游支付网关最多只会被真正调用一次成功。

        Returns:
            成功时返回`PaymentResult`；命中"仍在处理中"的重复请求返回
            一个字符串提示（真实HTTP接口场景下对应429/202这类响应）。
        """
        idempotency_key = f"charge:{order_id}"
        try:
            with idempotent_operation(self.store, idempotency_key):
                result = self._call_payment_gateway(order_id, amount_cents, simulate_failure=simulate_failure)
                self.store.complete(idempotency_key, result, ttl_seconds=24 * 3600)
                return result
        except DuplicateOperationError as exc:
            if exc.record.status is IdempotencyStatus.COMPLETED:
                return exc.record.result  # 直接复用之前的结果，绝不重复扣款
            return "request already in progress, please retry shortly"

    @property
    def total_gateway_calls(self) -> int:
        """真正到达下游支付网关的调用次数——用来验证"重复提交没有导致
        重复扣款"这个核心保证，而不只是看返回值看起来合理。"""
        return self._charge_attempts


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    service = PaymentService()

    # First submission succeeds.
    result1 = service.charge("order-42", amount_cents=5000)
    print(f"first submission -> {result1}")

    # Client retries the same order (e.g. network timeout on the original
    # response) — must NOT trigger a second real charge.
    result2 = service.charge("order-42", amount_cents=5000)
    print(f"retry (client didn't see the first response) -> {result2}")
    print(f"total real gateway calls for order-42: {service.total_gateway_calls}")

    # A different order, mid-operation failure: the idempotency key must be
    # released so the customer can retry once the gateway recovers.
    try:
        service.charge("order-99", amount_cents=1200, simulate_failure=True)
    except PaymentGatewayError as exc:
        print(f"\norder-99 first attempt failed: {exc}")

    retry_result = service.charge("order-99", amount_cents=1200, simulate_failure=False)
    print(f"order-99 retry after gateway recovered -> {retry_result}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
