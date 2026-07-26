"""产品示例：带背压监控的后台任务处理器。

真实产品形态：一条共享的后台任务队列（比如"生成报告""调用下游模型API"
这类异步任务）——如果队列积压没有主动监控，运维只能在下游超时/内存
耗尽之后被动发现问题；如果消费速度完全不受限，突发的任务高峰会让
一堆并发请求同时砸向下游服务，直接撞上限速墙。本示例展示"提前告警
积压 + 按下游允许的速率匀速消费"这套组合，而不是任由队列无限增长或
不受控地并发调用下游。

组合的包：ainative-guardrail（队列积压监控 + 速率限制消费）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from ainative_guardrail import QueueBacklogMonitor, RateLimitedConsumer


@dataclass
class JobResult:
    job_id: str
    processed: bool
    waited_seconds: float


class BackpressureAwareJobProcessor:
    """一个内存版任务队列处理器——积压超过阈值主动告警，消费速率不超过
    下游允许的上限。"""

    def __init__(self, *, backlog_warn_threshold: int, max_calls_per_window: int, window_seconds: float) -> None:
        self.queue: deque[str] = deque()
        self.backlog_monitor = QueueBacklogMonitor(warn_threshold=backlog_warn_threshold)
        self.rate_limiter = RateLimitedConsumer(max_calls=max_calls_per_window, window_seconds=window_seconds)
        self.downstream_call_log: list[str] = []

    def enqueue(self, job_id: str) -> bool:
        """入队一个任务；返回这次入队是否触发了新的积压告警（供调用方
        决定要不要额外触发扩容/限流动作）。"""
        self.queue.append(job_id)
        return self.backlog_monitor.record_depth(len(self.queue))

    def process_next(self) -> JobResult | None:
        """处理队列头部的一个任务——如果当前速率窗口已满，不强行调用
        下游，而是让调用方感知需要等待多久，由调用方决定睡眠/重新排队。"""
        if not self.queue:
            return None
        wait = self.rate_limiter.time_until_next_slot()
        if wait > 0:
            time.sleep(wait)
        job_id = self.queue.popleft()
        self.rate_limiter.record_call()
        self.downstream_call_log.append(job_id)
        self.backlog_monitor.record_depth(len(self.queue))
        return JobResult(job_id=job_id, processed=True, waited_seconds=wait)


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    processor = BackpressureAwareJobProcessor(
        backlog_warn_threshold=5, max_calls_per_window=3, window_seconds=0.3,
    )

    # A burst of jobs arrives all at once — enqueueing past the threshold
    # triggers an early warning instead of silently accumulating.
    for i in range(7):
        job_id = f"job-{i}"
        triggered = processor.enqueue(job_id)
        marker = " <- backlog warning triggered" if triggered else ""
        print(f"enqueued {job_id}, queue depth={len(processor.queue)}{marker}")

    print(f"\npeak backlog depth reached: {processor.backlog_monitor.peak_depth}")

    # Draining the queue is rate-limited — no more than 3 downstream calls
    # per 0.3s window, regardless of how many jobs are waiting.
    print("\ndraining queue at the rate limiter's pace:")
    start = time.monotonic()
    while processor.queue:
        result = processor.process_next()
        elapsed = time.monotonic() - start
        print(f"  processed {result.job_id} at t={elapsed:.2f}s (waited {result.waited_seconds:.2f}s for a rate slot)")

    print(f"\ntotal downstream calls made: {len(processor.downstream_call_log)}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
