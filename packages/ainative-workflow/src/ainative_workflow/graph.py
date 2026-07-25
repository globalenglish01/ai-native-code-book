"""轻量DAG工作流编排引擎。

这是ainative-workflow的核心新设计——两个被审计的真实生产项目都没有采用
"图编排引擎"这条路径（都选择了"单agent+分阶段提示词+HITL中断"的更轻量
方案，且有真实案例证明过早建设"跨agent网关/编排基础设施"容易变成从未
被使用的负资产）。本模块面向确实需要"多个明确阶段、阶段间有依赖关系、
支持条件分支、支持从某个节点暂停后恢复"这类场景，提供一个足够轻量、
不引入任何外部依赖（不需要Postgres/Redis/专门的workflow服务）的DAG
执行引擎，而不是照搬某个真实项目里不存在的代码。

设计取舍（刻意从简，避免"为了通用而通用"）：
- 节点是纯函数（同步或异步均可），接收一个共享的可变`context: dict`，
  返回值写回`context`的哪个key由节点自己在`WorkflowNode.output_key`声明。
- 依赖关系是显式声明的有向无环图（DAG），执行顺序由拓扑排序决定，
  不支持运行时动态发现新节点（如果需要，说明这不是这个引擎要解决的问题）。
- 条件分支通过`condition`回调实现：返回`False`时跳过该节点（及所有依赖
  于它输出的下游节点也会被跳过，除非下游节点自己声明了`skip_if_dependency_skipped=False`）。
- 支持"暂停点"：节点可以抛出`WorkflowPaused`异常表示"需要人工介入，
  暂停在这里"，`WorkflowRun`会记录暂停状态和已完成的节点，调用方可以
  用`resume()`从暂停点继续（而不是从头重跑）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

NodeFn = Callable[[dict[str, Any]], Any]
ConditionFn = Callable[[dict[str, Any]], bool]


class WorkflowPaused(Exception):
    """节点执行时抛出，表示"需要人工介入，在此暂停"（配合`ainative_workflow.hitl`使用）。

    Args:
        payload: 暂停时需要展示给人工审批者的信息，会被记录在`WorkflowRun.pause_payload`。
    """

    def __init__(self, payload: Any = None) -> None:
        super().__init__("workflow paused, awaiting external input")
        self.payload = payload


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass(frozen=True)
class WorkflowNode:
    """DAG里的一个节点。"""

    name: str
    fn: NodeFn
    """节点的执行逻辑，接收当前的共享`context`，返回值会被写入`context[output_key]`
    （如果`output_key`为None，返回值被丢弃，节点只产生副作用）。可以是同步函数
    或协程函数——`WorkflowRun`会自动识别并`await`协程结果。"""

    depends_on: tuple[str, ...] = ()
    """本节点依赖的上游节点名称——必须等这些节点全部完成（非SKIPPED）才会执行。"""

    output_key: str | None = None
    """节点返回值写入`context`的key；为None表示不写回（纯副作用节点）。"""

    condition: ConditionFn | None = None
    """可选的条件回调，接收当前context，返回False时跳过本节点。留空表示总是执行
    （前提是依赖已满足）。"""

    skip_if_dependency_skipped: bool = True
    """依赖的上游节点被跳过时，本节点是否也跟着被跳过（默认True——大多数场景下
    "上游被跳过"意味着本节点缺少必要输入，也应该跳过）。设为False可以实现
    "即使某个可选步骤被跳过，仍然继续执行"的场景。"""


@dataclass
class WorkflowRun:
    """一次工作流执行的完整状态——支持暂停/恢复。"""

    context: dict[str, Any]
    node_status: dict[str, NodeStatus]
    completed_order: list[str] = field(default_factory=list)
    paused_at: str | None = None
    pause_payload: Any = None
    failed_at: str | None = None
    error: str | None = None

    @property
    def is_paused(self) -> bool:
        return self.paused_at is not None

    @property
    def is_failed(self) -> bool:
        return self.failed_at is not None

    @property
    def is_completed(self) -> bool:
        return not self.is_paused and not self.is_failed and all(
            status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED) for status in self.node_status.values()
        )


def _topological_order(nodes: dict[str, WorkflowNode]) -> list[str]:
    """Kahn算法拓扑排序；检测到环时抛出`ValueError`。"""
    in_degree = {name: 0 for name in nodes}
    for node in nodes.values():
        for dep in node.depends_on:
            if dep not in nodes:
                raise ValueError(f"node '{node.name}' depends on unknown node '{dep}'")
            in_degree[node.name] += 1

    dependents: dict[str, list[str]] = {name: [] for name in nodes}
    for node in nodes.values():
        for dep in node.depends_on:
            dependents[dep].append(node.name)

    ready = sorted(name for name, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for dependent in sorted(dependents[current]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)

    if len(order) != len(nodes):
        remaining = set(nodes) - set(order)
        raise ValueError(f"workflow graph has a cycle involving: {sorted(remaining)}")
    return order


class Workflow:
    """一个已注册好节点的DAG工作流定义（无状态，可重复执行多次）。

    用法::

        wf = Workflow([
            WorkflowNode(name="fetch", fn=fetch_data, output_key="raw"),
            WorkflowNode(name="validate", fn=validate, depends_on=("fetch",), output_key="is_valid"),
            WorkflowNode(
                name="approve", fn=request_approval, depends_on=("validate",),
                condition=lambda ctx: ctx.get("is_valid"),
            ),
        ])
        run = await wf.run({"input": "..."})
        if run.is_paused:
            ...  # persist run, wait for human decision
        run = await wf.resume(run, resume_context={"decision": "approve"})
    """

    def __init__(self, nodes: list[WorkflowNode]) -> None:
        by_name = {n.name: n for n in nodes}
        if len(by_name) != len(nodes):
            raise ValueError("duplicate node names in workflow definition")
        self._nodes = by_name
        self._order = _topological_order(by_name)

    @property
    def node_names(self) -> list[str]:
        return list(self._order)

    async def run(self, initial_context: dict[str, Any] | None = None) -> WorkflowRun:
        run = WorkflowRun(
            context=dict(initial_context or {}),
            node_status={name: NodeStatus.PENDING for name in self._order},
        )
        return await self._execute(run)

    async def resume(self, run: WorkflowRun, *, resume_context: dict[str, Any] | None = None) -> WorkflowRun:
        """从`run.paused_at`记录的暂停点继续执行——已完成的节点不会重跑。

        Args:
            run: 之前调用`run()`或`resume()`返回的、`is_paused=True`的状态对象。
            resume_context: 恢复执行前合并进`run.context`的额外数据（比如人工审批的决定）。
        """
        if not run.is_paused:
            raise ValueError("resume() called on a run that is not paused")
        if resume_context:
            run.context.update(resume_context)
        run.paused_at = None
        run.pause_payload = None
        return await self._execute(run)

    async def _execute(self, run: WorkflowRun) -> WorkflowRun:
        for name in self._order:
            status = run.node_status[name]
            if status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
                continue

            node = self._nodes[name]

            if self._should_skip(node, run):
                run.node_status[name] = NodeStatus.SKIPPED
                continue

            run.node_status[name] = NodeStatus.RUNNING
            try:
                result = node.fn(run.context)
                if hasattr(result, "__await__"):
                    result = await result
            except WorkflowPaused as paused:
                run.node_status[name] = NodeStatus.PAUSED
                run.paused_at = name
                run.pause_payload = paused.payload
                return run
            except Exception as exc:  # noqa: BLE001
                run.node_status[name] = NodeStatus.FAILED
                run.failed_at = name
                run.error = str(exc)
                logger.warning("[Workflow] node '%s' failed: %s", name, exc)
                return run

            if node.output_key is not None:
                run.context[node.output_key] = result
            run.node_status[name] = NodeStatus.COMPLETED
            run.completed_order.append(name)

        return run

    def _should_skip(self, node: WorkflowNode, run: WorkflowRun) -> bool:
        for dep in node.depends_on:
            if run.node_status[dep] == NodeStatus.SKIPPED and node.skip_if_dependency_skipped:
                return True
        return node.condition is not None and not node.condition(run.context)
