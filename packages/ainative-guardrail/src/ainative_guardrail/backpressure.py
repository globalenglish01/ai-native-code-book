"""任务队列积压监控 + 匀速消费——背压不是被动发现，而是主动预警。

改造自checklist D类真实设计要点："队列/后台任务积压时是否有监控和背压
机制（提前告警、临时扩容、或在源头限制新请求），而不是让积压无限增长、
最终才被动发现"；以及"消息队列/后台任务是否按下游服务允许的速率匀速
消费，而不是一股脑并发调用撞上限速墙"。

设计原则（沿用`GuardHealthMonitorMiddleware`同一套"纯旁路预警，不改变
终止判断"的定位）：
1. **提前告警而不是事后发现**：`QueueBacklogMonitor`在队列深度超过阈值
   的那一刻就发出结构化WARNING，而不是等到下游超时/内存耗尽才暴露问题；
   每次跨越阈值只告警一次（去抖），避免刷屏。
2. **入队速率限制是"源头限制"而不是"消费端限流"**：`RateLimitedConsumer`
   保证真正被处理的任务不超过下游允许的速率，如果需要处理的任务比
   速率允许的更快到达，调用方必须显式等待（`await acquire()`），而不是
   把所有任务都塞给下游、指望下游自己扛住。
"""

from __future__ import annotations

import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class QueueBacklogMonitor:
    """跟踪队列深度，超过阈值时主动告警——不依赖被动发现积压。

    用法::

        monitor = QueueBacklogMonitor(warn_threshold=100)
        monitor.record_depth(queue.qsize())  # 每次入队/出队后调用
    """

    def __init__(self, *, warn_threshold: int) -> None:
        self._warn_threshold = warn_threshold
        self._warned = False
        self.peak_depth = 0

    def record_depth(self, current_depth: int) -> bool:
        """记录一次队列深度快照；深度超过阈值且此前未告警过时打一条
        结构化WARNING并返回`True`（供调用方决定要不要额外触发扩容/限流
        动作），深度回落到阈值以下后允许再次告警（避免"一次告警后哪怕
        真的持续积压也再也不会提醒"）。"""
        self.peak_depth = max(self.peak_depth, current_depth)
        if current_depth >= self._warn_threshold:
            if self._warned:
                return False
            self._warned = True
            logger.warning(
                "[Backpressure] queue depth %d has reached the warn threshold (%d)",
                current_depth, self._warn_threshold,
                extra={"guard_health_event": "queue_backlog", "depth": current_depth, "threshold": self._warn_threshold},
            )
            return True
        self._warned = False
        return False


class RateLimitedConsumer:
    """限制真正到达下游的调用速率——用滑动窗口计数，而不是固定间隔的
    简单sleep（固定间隔在窗口边界附近容易允许短时间内的突发超速）。
    """

    def __init__(self, *, max_calls: int, window_seconds: float) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._call_times: deque[float] = deque()

    def _prune_expired(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._call_times and self._call_times[0] <= cutoff:
            self._call_times.popleft()

    def time_until_next_slot(self) -> float:
        """距离下一次可以真正发起调用还需要等待多久（秒）；0表示现在
        就可以调用。调用方决定怎么等待（同步sleep/异步sleep/放回队列
        稍后重试），本方法只负责计算，不强制睡眠方式。"""
        now = time.monotonic()
        self._prune_expired(now)
        if len(self._call_times) < self._max_calls:
            return 0.0
        oldest = self._call_times[0]
        return max(0.0, (oldest + self._window_seconds) - now)

    def record_call(self) -> None:
        """真正发起了一次下游调用之后调用——把这次调用计入速率窗口。"""
        self._call_times.append(time.monotonic())

    @property
    def current_call_count(self) -> int:
        """当前速率窗口内已经记录的调用次数——供监控/测试查看瞬时占用。"""
        self._prune_expired(time.monotonic())
        return len(self._call_times)
