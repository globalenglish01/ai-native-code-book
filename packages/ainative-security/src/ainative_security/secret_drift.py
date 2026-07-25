"""周期性运行时密钥漂移检测——发现配置在启动后意外回退到不安全默认值。

背景：大多数项目的密钥/密码校验（比如Pydantic Settings的
`@model_validator`）只在配置对象被实例化的那一刻触发——也就是应用启动
时刻。这能保证"启动的这一刻配置不是已知不安全的默认值"，但完全没有
机制持续监控"运行过程中，这些配置是否发生了非预期的回退"（比如某次
错误的运维操作意外重置了环境变量、配置管理系统返回了非预期的旧值）。

设计原则：
1. **只读检测+告警，绝不改变应用行为**——不重启、不阻断、不自愈。
2. **规则外置为`list[SecretRule]`**，不硬编码具体项目的字段名——原版
   实现里`detect_secret_drift`直接读`current_settings.jwt_secret_is_default`
   这类项目专属属性，和启动时校验（如果项目里也有一份类似的启动时校验）
   容易各自维护一份判断逻辑、逐渐产生不一致。本版把"规则"本身抽成
   `ainative_core.protocols.SecretRule`（`name`+`is_default`回调+`message`+
   `severity`），启动时校验和运行期巡检可以共用同一份规则列表。
3. **读取调用方传入的配置对象当前的属性值，不重新实例化配置**——本模块
   不假设配置对象长什么样，只要求`is_default`回调能接收这个对象并返回
   bool。这意味着能发现"进程内持有的配置对象被意外改回默认值"，但**不会**
   重新读取环境变量本身——如果环境变量在进程启动后才变化，只有配置对象
   被重新构造（通常发生在进程重启时）才会生效。这是明确的覆盖范围边界。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from ainative_core.protocols import SecretRule

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 6 * 3600


def _drift_check_interval_seconds(env_var: str) -> int:
    try:
        v = int(os.environ.get(env_var, "0"))
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return DEFAULT_INTERVAL_SECONDS


def detect_secret_drift(config: Any, rules: list[SecretRule]) -> list[str]:
    """对当前配置对象做只读检测，返回命中的问题描述列表（空列表表示正常）。

    绝不抛异常——单条规则本身出问题（比如属性不存在）不应该让其余规则
    的检测失败，也不应该影响调用方。
    """
    issues: list[str] = []
    for rule in rules:
        try:
            if rule.is_default(config):
                issues.append(rule.message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[secret-drift] rule '%s' check failed (non-fatal): %s", rule.name, exc)
    return issues


async def run_secret_drift_check_once(
    config: Any,
    rules: list[SecretRule],
    *,
    is_monitored_environment: bool = True,
) -> list[str]:
    """跑一次检测。`is_monitored_environment=False`时直接跳过（比如development环境
    本身允许使用默认值，不需要巡检）。发现漂移时打一条ERROR级结构化日志。
    """
    if not is_monitored_environment:
        return []

    issues = detect_secret_drift(config, rules)
    if issues:
        logger.error(
            "[secret-drift] Detected configuration drift back to insecure defaults: %s",
            "; ".join(issues),
            extra={"security_event": "secret_config_drift", "issues": issues},
        )
    return issues


async def secret_drift_check_loop(
    config: Any,
    rules: list[SecretRule],
    *,
    interval_seconds: int | None = None,
    is_monitored_environment: bool = True,
) -> None:
    """周期性后台循环：先sleep再检查（避免和启动时校验在同一时刻重复告警），
    检测本身的异常只记警告日志、不中断循环。"""
    interval = (
        interval_seconds if interval_seconds is not None
        else _drift_check_interval_seconds("AINATIVE_SECRET_DRIFT_CHECK_INTERVAL_SECONDS")
    )
    while True:
        await asyncio.sleep(interval)
        try:
            await run_secret_drift_check_once(config, rules, is_monitored_environment=is_monitored_environment)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[secret-drift] Check failed (non-fatal): %s", exc)
