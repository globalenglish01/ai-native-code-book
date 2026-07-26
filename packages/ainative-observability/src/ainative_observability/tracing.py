"""轻量级追踪span记录——不强依赖OpenTelemetry，真实项目可按需接入真实OTel。

改造自真实项目里验证过的设计与真实踩过的坑（`otel.py`的`setup_otel`/
`_make_observable_exporter`）：

1. **导出失败必须自我监控**：追踪数据导出到后端（Tempo/Jaeger等）这个
   动作本身可能失败，如果失败被静默吞掉，会出现"看起来在追踪、实际上
   数据已经悄悄丢失"且长期不被发现的情况——`SpanExporter`协议的实现
   必须能在导出失败时被观察到（见`_ObservableSpanRecorder`的失败计数）。
2. **关联ID要真正在生产路径生效**：真实项目曾出现过`request_id`（HTTP层）/
   `trace_id`（追踪层）/`thread_id`（业务框架层）三套ID体系并行、却没有
   任何代码真正把它们串联起来的情况（`get_log_context()`函数存在但从未
   被调用）。本模块的`Span`把`correlation_id`作为构造时的必填概念，
   而不是事后才补的可选字段。
3. **后台/异步组件的span要挂在正确的父链路下**：`Span.child()`要求显式
   传入父span，不提供"隐式从某个全局变量猜测当前span是谁"的魔法，避免
   后台进程默认新开一条独立无关联链路的问题重演。

本模块不是OpenTelemetry的替代品——是一个足够轻量、demo/测试环境不需要
安装真实追踪后端就能验证"链路正确关联+导出失败可观测"这两个核心设计
原则的最小实现。真实项目如果已经在用OTel，只需要实现`SpanExporter`协议
去调用真实的OTel SDK即可复用本模块的关联/自监控逻辑。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SpanRecord:
    """一个已完成的span——导出到后端的最小schema。"""

    span_id: str
    parent_span_id: str | None
    correlation_id: str
    """串联同一次请求/操作跨层级ID体系的关联字段（对应HTTP层request_id、
    业务框架层thread_id等）——真实项目应该把这个字段设成request_id本身
    或者能反查到request_id的值，而不是另起一套无关的ID。"""

    name: str
    start_time: float
    end_time: float
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error_message: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000


@runtime_checkable
class SpanExporter(Protocol):
    """"已完成的span该被导出到哪里"这件事的抽象接口。

    真实实现应该调用真实的追踪后端SDK（OTel/Jaeger/自建HTTP上报等）；
    本包默认提供`InMemorySpanExporter`（见`memory_backends.py`）。
    """

    def export(self, record: SpanRecord) -> None: ...


class _ObservableSpanRecorder:
    """包装一个`SpanExporter`，导出失败时递增失败计数并调用`on_export_failure`
    回调——这就是ch39"追踪数据导出失败要能被自我监控"这条要求的具体实现，
    而不是让导出异常被静默吞掉。"""

    def __init__(self, exporter: SpanExporter, *, on_export_failure: Callable[[Exception], None] | None = None) -> None:
        self._exporter = exporter
        self._on_export_failure = on_export_failure
        self.export_failure_count = 0

    def export(self, record: SpanRecord) -> None:
        try:
            self._exporter.export(record)
        except Exception as exc:  # noqa: BLE001
            self.export_failure_count += 1
            if self._on_export_failure is not None:
                self._on_export_failure(exc)


class Tracer:
    """构造span并在完成时导出——所有span都要求显式的`correlation_id`，
    子span要求显式传入父span，不提供隐式全局current-span魔法。"""

    def __init__(self, exporter: SpanExporter, *, on_export_failure: Callable[[Exception], None] | None = None) -> None:
        self._recorder = _ObservableSpanRecorder(exporter, on_export_failure=on_export_failure)

    @property
    def export_failure_count(self) -> int:
        return self._recorder.export_failure_count

    @contextmanager
    def span(self, name: str, *, correlation_id: str, parent: SpanRecord | None = None, **attributes: Any):
        """用法::

            with tracer.span("handle_request", correlation_id=request_id) as s:
                ...
                with tracer.span("call_llm", correlation_id=request_id, parent=s):
                    ...
        """
        span_id = str(uuid.uuid4())
        start_time = time.time()
        status = "ok"
        error_message: str | None = None
        record: SpanRecord | None = None
        try:
            placeholder = SpanRecord(
                span_id=span_id, parent_span_id=parent.span_id if parent else None,
                correlation_id=correlation_id, name=name, start_time=start_time, end_time=start_time,
                attributes=dict(attributes),
            )
            yield placeholder
        except Exception as exc:
            status = "error"
            error_message = str(exc)
            raise
        finally:
            end_time = time.time()
            record = SpanRecord(
                span_id=span_id, parent_span_id=parent.span_id if parent else None,
                correlation_id=correlation_id, name=name, start_time=start_time, end_time=end_time,
                attributes=dict(attributes), status=status, error_message=error_message,
            )
            self._recorder.export(record)
