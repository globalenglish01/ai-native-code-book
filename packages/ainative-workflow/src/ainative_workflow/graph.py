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

# 让类型注解可以延迟解析，详见ainative_core里的详细解释，这里不再重复。
from __future__ import annotations

# copy——`copy.deepcopy(x)`把x连同它内部所有嵌套的list/dict完整复制一份，
# 详见下面`Workflow.run`/`resume`第一次真正用到的地方展开解释。
import copy

# logging——Python标准库自带的日志模块，用来在程序运行过程中输出
# "这里发生了什么"的记录，比直接用print更规范，可以按级别过滤。
import logging

# Callable——给"一个函数本身"写类型注解的工具，见下面`NodeFn`/`ConditionFn`
# 这两个类型别名的定义处详细解释。
from collections.abc import Callable

# dataclass/field——见ainative_core里的详细解释：`@dataclass`自动生成
# `__init__`等样板代码；`field(default_factory=...)`专门用于"默认值是
# 可变对象（比如列表/字典），需要每个实例各自独立创建一份"的场景。
from dataclasses import dataclass, field

# StrEnum——Python标准库`enum`模块提供的一种"字符串枚举"：枚举成员本身
# 既是一个独立的、类型安全的常量（比如下面`NodeStatus.COMPLETED`），
# 同时它的值又"表现得像一个普通字符串"（可以直接和字符串比较、参与
# 字符串格式化），比普通的`Enum`更适合这种"既要限定取值范围、又想在
# 日志/序列化里直接以字符串形式使用"的场景。
from enum import StrEnum

# Any——表示"任意类型都可以"，用在`context: dict[str, Any]`上，因为
# 共享上下文字典里到底存了什么类型的数据，完全由具体的工作流节点自己
# 决定，本引擎不做任何限定。
from typing import Any

logger = logging.getLogger(__name__)

# 这两行不是定义类，而是给右边这一长串`Callable[...]`类型起一个短名字，
# 之后代码里写`NodeFn`就等同于写这一整串类型，避免反复重复：
# - `NodeFn`：一个节点的执行逻辑长什么样——接收当前的共享context字典，
#   返回任意类型的结果（同步函数直接返回结果；协程函数返回一个需要
#   `await`的对象，`_execute`里会自动识别并等待）。
# - `ConditionFn`：一个条件判断回调长什么样——接收当前context，返回
#   一个布尔值（True表示"满足条件，正常执行"，False表示"跳过这个节点"）。
NodeFn = Callable[[dict[str, Any]], Any]
ConditionFn = Callable[[dict[str, Any]], bool]


class WorkflowPaused(Exception):
    """节点执行时抛出，表示"需要人工介入，在此暂停"（配合`ainative_workflow.hitl`使用）。

    Args:
        payload: 暂停时需要展示给人工审批者的信息，会被记录在`WorkflowRun.pause_payload`。
    """

    def __init__(self, payload: Any = None) -> None:
        # `super().__init__(...)`——调用父类`Exception`自己的构造函数，
        # 把一段固定的说明文字设置成这个异常的"消息"（就是`str(exc)`或
        # 打印这个异常时会显示的文字）。之所以这里的消息是固定文案、而
        # 不是把`payload`塞进去，是因为`payload`可能是任意复杂的数据结构
        # （不适合直接拼进一句异常消息里），需要展示的具体内容通过下面
        # `self.payload`这个属性单独传递。
        super().__init__("workflow paused, awaiting external input")
        # 把调用方传入的`payload`存成这个异常实例自己的属性，这样
        # `_execute`方法捕获到这个异常之后，可以通过`paused.payload`
        # 读取出这份"需要人工看的信息"，写入`WorkflowRun.pause_payload`。
        self.payload = payload


class NodeStatus(StrEnum):
    """一个节点在某次运行中的当前状态——六选一，不存在其他中间状态。"""

    PENDING = "pending"
    """还没轮到执行（初始状态）。"""
    RUNNING = "running"
    """正在执行中（进入`_execute`循环、还没得到结果之前的短暂中间状态）。"""
    COMPLETED = "completed"
    """已成功执行完毕。"""
    SKIPPED = "skipped"
    """因为条件不满足，或依赖的上游节点被跳过，本节点被跳过、不会执行。"""
    FAILED = "failed"
    """执行过程中抛出了非`WorkflowPaused`的异常，整个运行就此终止。"""
    PAUSED = "paused"
    """节点抛出了`WorkflowPaused`，运行在此暂停，等待`resume()`。"""


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
    """一次工作流执行的完整状态——支持暂停/恢复。

    注意这个类没有加`frozen=True`（不像上面的`WorkflowNode`）：因为一次
    运行的状态（`context`/`node_status`等）会随着执行不断被原地更新，
    需要是可变的；而`WorkflowNode`代表的是"工作流定义本身"，定义好之后
    不应该再被改动，所以是frozen的。这是本文件里"什么该冻结、什么不该"
    的设计原则。
    """

    context: dict[str, Any]
    """所有节点共享、读写的可变字典——节点之间"传递数据"就是靠读写这同一个字典。"""

    node_status: dict[str, NodeStatus]
    """每个节点当前的状态，key是节点名。"""

    completed_order: list[str] = field(default_factory=list)
    """按实际执行完成的先后顺序记录节点名——不同于`Workflow.node_names`（那是
    拓扑排序算出来的"计划顺序"，这个是"实际发生"的顺序，条件跳过场景下两者可能不同）。"""

    paused_at: str | None = None
    """当前暂停在哪个节点名；`None`表示没有暂停（要么还在跑，要么已经跑完/失败）。"""

    pause_payload: Any = None
    """暂停时`WorkflowPaused`携带的、需要展示给人工审批者的信息。"""

    failed_at: str | None = None
    """执行失败时，是哪个节点名抛出的异常；`None`表示没有失败。"""

    error: str | None = None
    """失败时的错误信息文本（`str(异常对象)`）。"""

    @property
    def is_paused(self) -> bool:
        # `@property`——把一个方法"伪装"成一个普通属性来访问：调用方写
        # `run.is_paused`（不用加括号），而不是`run.is_paused()`。这里
        # 用它是因为"是否暂停"本质上是从`paused_at`这个字段直接派生
        # 出来的简单判断，没必要让调用方自己去写`run.paused_at is not None`，
        # 用一个语义清晰的属性名封装起来更易读。
        return self.paused_at is not None

    @property
    def is_failed(self) -> bool:
        return self.failed_at is not None

    @property
    def is_completed(self) -> bool:
        # "整个运行是否已经完全跑完"：既没有暂停、也没有失败，并且
        # `all(...)`——这是一个"生成器表达式"包在`all()`里的写法，检查
        # `node_status`里是不是每一个节点的状态都属于"完成"或"跳过"这
        # 两种终态之一（还处于PENDING/RUNNING/PAUSED/FAILED的节点存在，
        # `all(...)`就会是False）。三个条件都满足，才算这次运行真正结束。
        return not self.is_paused and not self.is_failed and all(
            status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED) for status in self.node_status.values()
        )


def _topological_order(nodes: dict[str, WorkflowNode]) -> list[str]:
    """Kahn算法拓扑排序；检测到环时抛出`ValueError`。

    "拓扑排序"要解决的问题：给定一堆"谁必须在谁之前执行"的依赖关系，
    算出一个满足所有依赖约束的合法执行顺序。Kahn算法的核心思路是：反复
    找出"当前还没被处理、且所有依赖都已经处理完"的节点，一个个取走，
    直到所有节点都被取走为止——如果最后还剩节点取不出来，说明图里存在
    "环"（比如A依赖B、B又依赖A，谁都无法作为"第一个"被取走）。
    """
    # `in_degree`——"入度"，即"这个节点依赖了多少个别的节点"（不是被多少
    # 节点依赖，是Kahn算法里"入度"的标准含义：指向自己的边的数量）。
    # 先把每个节点的入度都初始化成0，再遍历一遍所有节点声明的依赖关系，
    # 每发现一条"A依赖B"，就把A的入度加1。
    in_degree = {name: 0 for name in nodes}
    for node in nodes.values():
        for dep in node.depends_on:
            if dep not in nodes:
                # 声明依赖了一个压根不存在的节点名——这是工作流定义本身
                # 的错误（比如写错了字符串），必须尽早报错，而不是让它
                # 悄悄通过、到真正执行时才发现依赖找不到。
                raise ValueError(f"node '{node.name}' depends on unknown node '{dep}'")
            in_degree[node.name] += 1

    # `dependents`——和`in_degree`方向相反的"反向索引"：对每个节点，记录
    # "有哪些节点依赖了它"（谁的执行需要等我完成）。构造这份反向索引是
    # 为了下面主循环里，一个节点"取走"之后，能快速找到该去给哪些下游
    # 节点的入度减1，不需要每次都重新扫描全部节点的`depends_on`。
    dependents: dict[str, list[str]] = {name: [] for name in nodes}
    for node in nodes.values():
        for dep in node.depends_on:
            dependents[dep].append(node.name)

    # `ready`——当前"入度为0"（没有任何未满足的依赖，可以立刻执行）的
    # 节点名列表，`sorted(...)`让初始顺序是确定性的字母序（避免因为
    # 字典遍历顺序的细微差异导致每次跑出不同的排序结果，便于测试和排查）。
    ready = sorted(name for name, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while ready:
        # `ready.pop(0)`——从列表最前面取出并移除一个元素（"先进先出"，
        # 配合上面的`sorted()`，让同一批"入度同时归零"的节点按字母序
        # 依次被取走，顺序稳定可预期）。
        current = ready.pop(0)
        order.append(current)
        # 这个节点已经"完成"（在排序意义上），去看有哪些下游节点依赖
        # 它——把它们的入度各减1（表示"你依赖的节点里，又有一个满足了"）。
        for dependent in sorted(dependents[current]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                # 这个下游节点的所有依赖现在都已满足（入度归零），它也
                # 变成"可以执行"的候选，加入`ready`，供下一轮循环取走。
                ready.append(dependent)

    if len(order) != len(nodes):
        # 如果最终`order`收集到的节点数量，少于总节点数，说明有些节点
        # 永远没能等到"入度归零"的那一刻——这只可能发生在图里存在环的
        # 情况下（环上的节点互相依赖，谁的入度都无法真正归零）。
        # `set(nodes) - set(order)`——集合的"差集"运算，算出"哪些节点
        # 在原始节点集合里、但没有出现在已排好序的order里"，这些就是
        # 涉及环的节点，把它们报告出来方便调用方定位问题所在。
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
        # `{n.name: n for n in nodes}`——字典推导式，把传入的节点列表
        # 转换成"按名字查节点"的字典，方便后面`_execute`/`_should_skip`
        # 按名字快速取用，不用每次都遍历整个列表去找。
        by_name = {n.name: n for n in nodes}
        if len(by_name) != len(nodes):
            # 如果转换成字典后长度变短了，说明原始列表里有重复的节点名
            # （字典的key不能重复，后面的会覆盖前面同名的）——这是工作流
            # 定义本身的错误，必须在构造时就报错拒绝，而不是让某个节点
            # 定义被悄悄覆盖丢失。
            raise ValueError("duplicate node names in workflow definition")
        self._nodes = by_name
        # 在构造时就把拓扑排序算好、存起来（而不是每次`run()`都重新算一遍）
        # ——因为节点集合本身在`Workflow`构造之后就不会再变，排序结果也
        # 不会变，提前算好可以避免重复计算，也能让"图里有环"这类定义
        # 错误在构造阶段就暴露，而不是等到真正执行时才发现。
        self._order = _topological_order(by_name)

    @property
    def node_names(self) -> list[str]:
        # `list(self._order)`——返回内部排序列表的一份拷贝，而不是直接
        # 把`self._order`本身暴露出去，防止调用方拿到返回值后修改它，
        # 意外影响到这个`Workflow`实例内部真正使用的执行顺序。
        return list(self._order)

    async def run(self, initial_context: dict[str, Any] | None = None) -> WorkflowRun:
        """开始一次新的运行——`initial_context`会被深拷贝后才存进`WorkflowRun`，
        不与调用方传入的原始dict共享任何嵌套的可变对象。`dict(initial_context)`
        只保护了顶层key不被别名共享，如果调用方传入的字典里某个value本身
        是嵌套dict/list（比如`{"config": {...}}`），节点执行过程中修改
        `ctx["config"]`会直接改到调用方自己手上那份"模板"上，导致同一个
        context对象被复用去发起多次运行时，前一次运行的节点执行结果会
        意外污染后一次运行的初始状态。
        """
        run = WorkflowRun(
            # `copy.deepcopy(initial_context) if initial_context else {}`——
            # 条件表达式：如果调用方传了初始context（且不是空字典，因为
            # 空字典本身是"假值"），就深拷贝一份完全独立的副本存入这次
            # 运行；如果没传（`None`）或传的是空字典，直接用一个新的空
            # 字典起步。深拷贝而不是浅拷贝的原因，详见本方法docstring
            # 里的完整解释——这正是本会话中发现并修复的一处真实aliasing
            # （别名共享）bug。
            context=copy.deepcopy(initial_context) if initial_context else {},
            # `{name: NodeStatus.PENDING for name in self._order}`——字典
            # 推导式，给拓扑排序里的每个节点名，初始化一个"待执行"状态，
            # 作为这次全新运行的起点。
            node_status={name: NodeStatus.PENDING for name in self._order},
        )
        return await self._execute(run)

    async def resume(self, run: WorkflowRun, *, resume_context: dict[str, Any] | None = None) -> WorkflowRun:
        """从`run.paused_at`记录的暂停点继续执行——已完成的节点不会重跑。

        Args:
            run: 之前调用`run()`或`resume()`返回的、`is_paused=True`的状态对象。
            resume_context: 恢复执行前合并进`run.context`的额外数据（比如人工审批的决定）——
                同样会被深拷贝后再合并，理由与`run()`的`initial_context`一致。
        """
        if not run.is_paused:
            # 对一个"根本没有暂停"的运行调用`resume()`没有意义（可能是
            # 调用方逻辑写错了，比如忘记先检查`run.is_paused`）——直接
            # 报错拒绝，而不是静默地什么都不做或产生不可预期的行为。
            raise ValueError("resume() called on a run that is not paused")
        if resume_context:
            # `dict.update(...)`——把`resume_context`里的键值对合并进
            # `run.context`（已存在的key会被覆盖，新key会被新增）。同样
            # 对`resume_context`先做一次深拷贝再合并，防止调用方后续复用
            # 这份`resume_context`字典模板时，被这次合并操作间接污染。
            run.context.update(copy.deepcopy(resume_context))
        # 清空暂停标记——即将重新进入`_execute`循环，如果这次执行又在
        # 别的节点遇到新的暂停，`_execute`会重新设置这两个字段。
        run.paused_at = None
        run.pause_payload = None
        return await self._execute(run)

    async def _execute(self, run: WorkflowRun) -> WorkflowRun:
        # 按照构造时算好的拓扑顺序，依次尝试执行每个节点——这个循环同时
        # 承担"第一次运行"和"从暂停点恢复"两种场景，靠下面的状态检查
        # 自然地跳过已经处理过的节点，不需要为"恢复"单独写一套逻辑。
        for name in self._order:
            status = run.node_status[name]
            if status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED):
                # 这个节点在之前的执行（可能是本次调用更早的循环轮次，
                # 也可能是恢复前那一轮`_execute`）里已经处理完了，直接
                # 跳到下一个节点——这正是"resume()不会重跑已完成节点"
                # 这个承诺的具体实现方式。
                continue

            node = self._nodes[name]

            if self._should_skip(node, run):
                run.node_status[name] = NodeStatus.SKIPPED
                continue

            run.node_status[name] = NodeStatus.RUNNING
            try:
                # 调用节点的执行函数，把当前共享的`run.context`传给它。
                result = node.fn(run.context)
                # `hasattr(result, "__await__")`——检查这个返回值"是不是
                # 一个可以被await的东西"（`__await__`是Python协程对象都
                # 会具备的一个特殊方法）。这样写的好处是`WorkflowNode.fn`
                # 既可以是普通同步函数（直接返回结果，没有`__await__`，
                # 这个if判断为False，`result`原样使用），也可以是`async def`
                # 定义的协程函数（调用后返回一个需要await的对象，这里
                # 检测到后自动await、拿到真正的结果）——调用方不需要提前
                # 告诉引擎"我这个节点是不是异步的"，引擎自己就能识别。
                if hasattr(result, "__await__"):
                    result = await result
            except WorkflowPaused as paused:
                # 节点主动抛出"需要暂停"的信号——这不是一种"失败"，而是
                # 一种"正常的、预期内的中断"，所以单独用`PAUSED`状态记录，
                # 不归入下面的`FAILED`分支。记录下是哪个节点触发的暂停、
                # 以及暂停时携带的信息，然后直接返回当前的`run`（不再往
                # 后继续跑剩余的节点），调用方后续可以持久化这个`run`，
                # 等人工做出决定后再调用`resume()`继续。
                run.node_status[name] = NodeStatus.PAUSED
                run.paused_at = name
                run.pause_payload = paused.payload
                return run
            except Exception as exc:  # noqa: BLE001
                # 这里故意捕获几乎所有异常类型（比ruff默认推荐的"只捕获
                # 明确预期的异常类型"更宽泛）：因为这个方法的职责就是
                # "不管某个节点内部抛出什么异常，都要把整个工作流安全地
                # 转入FAILED终态并记录下来"，不能让某个节点的意外异常
                # 直接把调用方的程序也带崩溃。这一行末尾还有一条给代码
                # 检查工具ruff看的抑制警告标记，显式告诉它"这处的宽泛
                # 捕获是刻意设计，不是疏忽遗漏"，避免每次代码检查都被
                # 重复提示同一个已知的、经过深思熟虑的选择。
                run.node_status[name] = NodeStatus.FAILED
                run.failed_at = name
                run.error = str(exc)
                logger.warning("[Workflow] node '%s' failed: %s", name, exc)
                return run

            if node.output_key is not None:
                # 节点声明了要把结果写回context的哪个key——只有声明了
                # （不是None）才写入；没声明`output_key`的节点视为"纯
                # 副作用节点"（比如只是发一条通知），它的返回值被直接
                # 丢弃，不污染共享的context。
                run.context[node.output_key] = result
            run.node_status[name] = NodeStatus.COMPLETED
            # 额外记录一份"真实执行完成的顺序"——见`WorkflowRun.completed_order`
            # 字段的注释，这份顺序在有节点被跳过时，可能不同于`self._order`
            # 这份"理论上的计划顺序"。
            run.completed_order.append(name)

        # 循环正常走完（没有在中途因为暂停或失败提前return），说明所有
        # 节点都已经处理完毕（要么完成，要么被跳过）。
        return run

    def _should_skip(self, node: WorkflowNode, run: WorkflowRun) -> bool:
        # 检查这个节点依赖的每一个上游节点：只要有一个上游被跳过了、
        # 并且当前节点自己声明了"上游被跳过我也要跟着跳过"
        # （`skip_if_dependency_skipped`默认就是True），就判定"应该跳过"。
        for dep in node.depends_on:
            if run.node_status[dep] == NodeStatus.SKIPPED and node.skip_if_dependency_skipped:
                return True
        # 走到这里说明"因为依赖被跳过而连带跳过"这条路径不成立，接下来
        # 看节点自己声明的`condition`回调：`node.condition is not None`——
        # 如果压根没有声明条件回调，视为"总是执行"，不跳过；如果声明了，
        # 就调用它、传入当前context，回调返回False就意味着"不满足条件，
        # 跳过这个节点"。
        return node.condition is not None and not node.condition(run.context)
