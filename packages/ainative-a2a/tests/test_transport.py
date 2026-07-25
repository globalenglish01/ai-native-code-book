from __future__ import annotations

import pytest
from ainative_a2a.transport import InProcessTransport
from ainative_core.protocols import A2ATask


def _task(capability="do_thing", sender="caller") -> A2ATask:
    return A2ATask(task_id="t1", capability=capability, payload={"x": 1}, sender_agent=sender)


@pytest.mark.asyncio
async def test_send_calls_registered_handler_and_returns_success():
    transport = InProcessTransport()

    async def handler(task: A2ATask) -> dict:
        return {"result": task.payload["x"] * 2}

    transport.register_handler("worker_agent", handler)
    result = await transport.send("worker_agent", _task())
    assert result.status == "success"
    assert result.output == {"result": 2}
    assert result.task_id == "t1"


@pytest.mark.asyncio
async def test_send_to_unregistered_agent_returns_error():
    transport = InProcessTransport()
    result = await transport.send("nobody", _task())
    assert result.status == "error"
    assert "no handler registered" in result.error_message


@pytest.mark.asyncio
async def test_send_captures_handler_exception_as_error_result():
    transport = InProcessTransport()

    async def broken_handler(task: A2ATask) -> dict:
        raise RuntimeError("handler exploded")

    transport.register_handler("broken_agent", broken_handler)
    result = await transport.send("broken_agent", _task())
    assert result.status == "error"
    assert "handler exploded" in result.error_message
