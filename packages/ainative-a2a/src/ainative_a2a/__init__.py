"""ainative-a2a —— Agent间任务委派、结果回传、能力注册与发现。"""

from __future__ import annotations

from ainative_a2a.dispatcher import DEFAULT_MAX_DELEGATION_DEPTH, DelegationLimitExceeded, Dispatcher
from ainative_a2a.registry import InMemoryAgentRegistry
from ainative_a2a.transport import AgentHandler, InProcessTransport

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MAX_DELEGATION_DEPTH",
    "AgentHandler",
    "DelegationLimitExceeded",
    "Dispatcher",
    "InMemoryAgentRegistry",
    "InProcessTransport",
]
