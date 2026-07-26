from __future__ import annotations

import pytest
from products.idempotent_payment_service import PaymentGatewayError, PaymentResult, PaymentService


def test_first_charge_succeeds_and_calls_the_real_gateway_once():
    service = PaymentService()
    result = service.charge("order-1", amount_cents=1000)

    assert isinstance(result, PaymentResult)
    assert result.status == "succeeded"
    assert service.total_gateway_calls == 1


def test_duplicate_submission_returns_cached_result_without_recharging():
    """The core guarantee this module exists for: a client retry after a
    successful charge must never trigger a second real charge."""
    service = PaymentService()
    first = service.charge("order-1", amount_cents=1000)
    second = service.charge("order-1", amount_cents=1000)

    assert second == first
    assert service.total_gateway_calls == 1


def test_different_orders_each_get_their_own_charge():
    service = PaymentService()
    service.charge("order-1", amount_cents=1000)
    service.charge("order-2", amount_cents=2000)

    assert service.total_gateway_calls == 2


def test_gateway_failure_propagates_to_caller():
    service = PaymentService()
    with pytest.raises(PaymentGatewayError):
        service.charge("order-1", amount_cents=1000, simulate_failure=True)


def test_retry_after_gateway_failure_succeeds_and_actually_charges():
    """The real-world bug class this guards against: a mid-operation
    failure must release the idempotency key so a retry after the outage
    is resolved is not blocked for the entire TTL window."""
    service = PaymentService()
    with pytest.raises(PaymentGatewayError):
        service.charge("order-1", amount_cents=1000, simulate_failure=True)

    result = service.charge("order-1", amount_cents=1000, simulate_failure=False)

    assert isinstance(result, PaymentResult)
    # Two gateway calls total (one failed, one succeeded) — the key point is
    # that the retry was allowed to reach the gateway at all, instead of
    # being rejected as a duplicate for the full idempotency TTL window.
    assert service.total_gateway_calls == 2
