"""Agent运行期预算/节流中间件——防止单次运行成本失控。

改造自真实生产项目里被验证过的一组纯内存计数器中间件，全部继承
`langchain.agents.middleware.types.AgentMiddleware`，互相之间零耦合，
可以独立选用、任意组合。

提取时修正了原版两处已被发现但尚未修复的真实逻辑缺陷：

1. **`_count_input_tokens` 累计值/估算值混用**：原版把"从`usage_metadata`
   拿到的真实累计token数"和"没有`usage_metadata`时的字符数估算增量"强行
   放进同一个变量用`max()`比较，会被对话早期一条无元数据的大段消息永久
   虚高。本版拆成`last_known_cumulative`（最近一次真实累计值）+ 只对
   该条消息**之后**的新消息做字符数估算增量，语义上不再混淆。
2. **`ConsecutiveRetryGuardMiddleware`合成短路消息污染计数器**：原版
   `wrap_tool_call`把自己短路拦截时生成的合成`ToolMessage`（status="error"）
   又传回`_record`当作"新的一次真实失败"继续累加，导致`status()`快照
   随着AI反复尝试已经被拦截的工具无限膨胀、失真。本版短路路径不再
   调用`_record`——计数器在真正命中上限的那一刻就已经达到`_max`，
   之后的短路不需要、也不应该继续增长这个计数。

`MCPCallLimiterMiddleware`的调用计数发生在`wrap_tool_call`最外层——如果
和`HumanInTheLoopMiddleware`一起使用，需要注意LangChain中间件是按列表
顺序从外到内包裹的：把`MCPCallLimiterMiddleware`排在人工审核中间件
**之后**（更内层），才能保证"用户主动拒绝要求重试"不会被计入调用上限。
这是一个组合/排序注意事项，不是本类自身的实现缺陷。
"""

from __future__ import annotations

# logging是Python标准库自带的"日志"模块——见model_router.py里的详细解释。
import logging

# Awaitable/Callable 用来给"函数本身"写类型注解，见model_router.py里的
# 详细解释。
from collections.abc import Awaitable, Callable

# Any表示"这里可以是任意类型"。
from typing import Any

# 从LangChain这个第三方AI应用开发框架里导入中间件基类和相关类型——见
# model_router.py里对AgentMiddleware/ModelRequest/ModelResponse的详细解释。
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

# ToolCallRequest——LangChain定义的"一次工具调用请求"的完整信息（调用
# 哪个工具、传了什么参数等）。
from langgraph.prebuilt.tool_node import ToolCallRequest

# Command——LangGraph（LangChain底层的图执行引擎）里表示"控制流指令"的
# 类型，工具调用的handler除了能返回一条普通的ToolMessage，某些场景下
# 也可能返回一个Command，用来做更复杂的流程控制（比如跳转到别的节点）。
# 这里出现在类型注解里，说明本文件的中间件需要兼容这两种可能的返回类型。
from langgraph.types import Command

logger = logging.getLogger(__name__)


class TokenCounter:
    """把"真实累计值"和"字符数估算增量"分开维护，避免语义混淆（ch10-01修复）。

    `TokenBudgetMiddleware`和`ainative_guardrail.health_monitor.GuardHealthMonitorMiddleware`
    共用同一个计数实现，避免两处各自维护一份、可能出现行为不一致的token计数逻辑。
    """

    def __init__(self) -> None:
        # 最近一次从消息的usage_metadata里读到的、供应商官方给出的真实
        # 累计输入token数——一旦读到新的真实值，就会覆盖这个字段（见
        # 下面count()方法里的具体逻辑），代表"目前为止最可信的一次
        # 快照"。
        self._last_known_cumulative = 0
        # 自从上一次真实累计值之后，新出现的、还没有任何usage_metadata
        # 佐证、只能靠"字符数除以4"粗略估算出来的token增量。
        self._estimated_increment = 0
        # `set[int]` ——一个只存整数、且天然去重的集合。这里存的是"已经
        # 统计过的消息的id"，用来避免同一条消息被反复调用count()时重复
        # 计数（见下面count()方法开头的判断）。
        self._counted_message_ids: set[int] = set()

    # @property 装饰器让下面这个方法可以"像访问属性一样被调用"——加了
    # 这个装饰器之后，调用方写`counter.last_known_cumulative`（不用加
    # 括号）就能拿到结果。适合用在"这是一个只读的、概念上更像属性而不是
    # 一个需要传参数的动作"的场景。
    @property
    def last_known_cumulative(self) -> int:
        """最近一次从`usage_metadata`拿到的真实累计输入token数（不含估算增量）。

        注意：这**不是**当前总计——如果自上次真实累计值之后又有新消息只经过
        了字符数估算（还没有反映在任何`usage_metadata`里），这部分不计入这个
        属性。需要"当前最佳估计总量"（与`count()`用于短路判定的口径一致）时，
        用`current_total`。
        """
        return self._last_known_cumulative

    @property
    def current_total(self) -> int:
        """当前最佳估计的总token数（真实累计值 + 尚未被真实值覆盖的估算增量）——
        与`count()`用于短路判定的口径完全一致，适合`status()`这类只读快照使用。
        """
        # 真实累计值加上"真实值之后又新增的、只能靠估算的部分"，就是
        # "目前最合理的总量估计"。
        return self._last_known_cumulative + self._estimated_increment

    def count(self, messages: list) -> int:
        # `for m in messages or []:` ——如果messages本身是None（调用方
        # 没传或者传了空值），用空列表兜底，避免对None做for循环报错；
        # `messages`不是None时按原样遍历。
        for m in messages or []:
            # `id(m)` 是Python内置函数，返回这个对象在内存里的唯一编号
            # （只要对象存活，这个编号就不会重复、不会变化）——用它而不是
            # 消息内容本身作为"是否已经统计过"的判断依据，因为同一条
            # 消息对象在整个对话过程中会被反复传入count()（每一轮都会把
            # 完整历史再传一遍），只有用"对象身份"才能准确判断"这条具体
            # 的消息我是不是已经数过一次了"。
            msg_id = id(m)
            if msg_id in self._counted_message_ids:
                # 这条消息之前已经统计过，跳过，避免重复计数。
                # `continue`表示"结束这一轮循环，直接进入下一轮"。
                continue
            # `getattr(对象, "属性名", 默认值)` ——安全地尝试读取这条
            # 消息是否带有usage_metadata这个属性，读不到就用None代替。
            meta = getattr(m, "usage_metadata", None)
            if meta and isinstance(meta, dict):
                # 这条消息确实带有真实的用量元数据字典。
                # `meta.get("input_tokens") or meta.get("prompt_tokens")`
                # ——不同供应商/版本对"输入token数"这个字段的命名不完全
                # 一致，有的叫input_tokens，有的叫prompt_tokens，这里
                # 优先取前者，前者读不到（或者是None/0，"or"会继续往
                # 后找）再取后者。
                v = meta.get("input_tokens") or meta.get("prompt_tokens")
                if isinstance(v, int):
                    # 这条消息本身就代表"截至目前的真实累计输入token数"——
                    # 之前所有靠字符数估算的增量已经被这个真实值覆盖，清零重来。
                    # `max(self._last_known_cumulative, v)` 是防御性写法：
                    # 理论上新的真实累计值应该总是不小于旧的，但如果消息
                    # 顺序被打乱或出现异常数据，取较大值可以避免累计值
                    # 意外变小。
                    self._last_known_cumulative = max(self._last_known_cumulative, v)
                    self._estimated_increment = 0
                self._counted_message_ids.add(msg_id)
                # 这条消息已经处理完（不管有没有拿到合法的int值），标记
                # 为已统计，跳到下一条消息，不再往下走字符数估算的分支。
                continue
            # 走到这里，说明这条消息没有usage_metadata——先标记为已统计
            # （避免之后又被重复处理一次），再按字符数粗略估算它贡献了
            # 多少token。
            self._counted_message_ids.add(msg_id)
            content = getattr(m, "content", "")
            if isinstance(content, str):
                # 极其粗略的经验估算："大约每4个字符对应1个token"——不
                # 追求精确，只是在完全没有真实数据时给出一个"总比0强"的
                # 保守估计。`//`是整数除法，结果自动向下取整，不会有
                # 小数。
                self._estimated_increment += len(content) // 4
            elif isinstance(content, list):
                # content是"结构化内容块"列表的情况——遍历每一块，尝试
                # 从中提取文本部分再估算。
                for block in content:
                    if isinstance(block, dict):
                        # `block.get("text") or block.get("input") or ""`
                        # ——依次尝试text字段、input字段（工具调用参数
                        # 通常存在这里），都没有就用空字符串兜底。
                        text = block.get("text") or block.get("input") or ""
                        if isinstance(text, str):
                            self._estimated_increment += len(text) // 4
        # 遍历完所有消息后，返回"当前最佳估计的总量"，和current_total
        # 这个属性的口径完全一致。
        return self._last_known_cumulative + self._estimated_increment


class MCPCallLimiterMiddleware(AgentMiddleware):
    """限制某个工具在一次运行内最多被调用几次。

    Args:
        per_tool_limit: ``工具名 -> 最大调用次数``的映射，未列出的工具不受限。
        default_limit: 未列在`per_tool_limit`里的工具的兜底上限（`None`表示不限）。
    """

    def __init__(
        self,
        per_tool_limit: dict[str, int] | None = None,
        default_limit: int | None = None,
    ) -> None:
        super().__init__()
        # `per_tool_limit or {}` ——调用方没传（是None）就用空字典兜底，
        # 表示"没有为任何具体工具单独设置上限"。
        self._per_tool_limit = per_tool_limit or {}
        self._default_limit = default_limit
        # 记录"每个工具名到目前为止被调用了多少次"，初始时是空字典
        # （还没有任何工具被调用过）。
        self._counters: dict[str, int] = {}

    def _bump(self, name: str) -> int:
        # `.get(name, 0) + 1` ——读取这个工具目前的计数（没记录过就当作
        # 0），加1之后重新存回去，同时把新的计数值返回给调用方。这是
        # "计数器自增"的标准写法。
        self._counters[name] = self._counters.get(name, 0) + 1
        return self._counters[name]

    def _limit_for(self, name: str) -> int | None:
        # 优先用这个工具专属的上限（如果在per_tool_limit里登记过），
        # 没有登记就用兜底的default_limit（可能仍然是None，表示不限）。
        return self._per_tool_limit.get(name, self._default_limit)

    def status(self) -> dict[str, Any]:
        """只读状态快照：各工具 count/limit 的最大占比。

        用`limit is not None`而不是`if limit:`判断——`limit=0`是"这个工具
        一次都不允许调用"的合法配置（不是"不限"），用真值判断会把它当作
        `None`一样跳过，导致一个已经被完全拦截的工具在这份快照里完全不可见。
        """
        # `worst_tool, max_ratio = "", 0.0` 是Python的"多重赋值"写法，
        # 一行同时给两个变量赋初始值。
        worst_tool, max_ratio = "", 0.0
        # `self._counters.items()` 遍历字典时同时拿到每一对key（工具名）
        # 和value（调用次数）。
        for name, count in self._counters.items():
            limit = self._limit_for(name)
            # 这里特意写`limit is not None`（而不是`if limit:`）——
            # 因为`limit=0`在Python里是"假值"（bool(0)是False），如果
            # 用`if limit:`判断，`limit=0`这种"完全禁止调用"的合法配置
            # 会被误判成"没有限制"而被跳过，正是docstring里强调的坑。
            if limit is not None:
                # `count / limit if limit > 0 else float("inf")` ——
                # 正常情况下算"实际调用次数 ÷ 上限"这个比例；但如果
                # limit恰好是0（上限是"一次都不能调用"），除以0会报错，
                # 这里用`float("inf")`（正无穷大）代替，表示"这个工具
                # 的占比已经不能再高了"。
                ratio = count / limit if limit > 0 else float("inf")
                if ratio > max_ratio:
                    # 遇到更高的占比，就更新"目前最严重的工具是谁、
                    # 占比是多少"。
                    max_ratio, worst_tool = ratio, name
        # 返回一份字典快照：`dict(self._counters)`复制一份计数字典（不
        # 直接返回原始字典引用，避免调用方拿到后修改，意外影响到内部
        # 真实的计数状态）。
        return {"counters": dict(self._counters), "max_ratio": max_ratio, "worst_tool": worst_tool}

    def _maybe_short_circuit(self, request: ToolCallRequest) -> ToolMessage | None:
        # `request.tool_call.get("name", "")` ——这次工具调用请求里，
        # 目标工具的名字（读不到就用空字符串兜底）。
        name = request.tool_call.get("name", "")
        limit = self._limit_for(name)
        if limit is None:
            # 这个工具没有配置任何上限，直接放行——返回None表示"不需要
            # 短路拦截"。
            return None
        count = self._bump(name)
        if count <= limit:
            # 这一次调用之后，累计次数仍然没有超过上限，放行。
            return None
        # 走到这里说明超过上限了——打一条警告日志，并构造一条"合成的"
        # ToolMessage返回给调用方，代替"真的去执行这个工具"。
        logger.warning(
            "[MCPCallLimiter] %s exceeded cap %d (this call #%d) — short-circuiting",
            name, limit, count,
        )
        return ToolMessage(
            # 这段content是特意写给AI模型自己看的提示文字（不是给人类
            # 用户看的），告诉它"这个工具已经到调用上限了，别再调用，
            # 用已有的信息想办法完成任务"。
            content=(
                f"[budget] Call cap of {limit} reached for tool '{name}'. "
                f"Use what you already have in this conversation; do NOT "
                f"call '{name}' again in this run."
            ),
            tool_call_id=request.tool_call.get("id", ""),
            name=name,
            # status="error"——用工具调用"失败"这个状态，让AI意识到这次
            # 调用没有成功执行，需要改变策略，而不是误以为拿到了正常结果。
            status="error",
        )

    # wrap_tool_call 是AgentMiddleware规定好的"钩子方法"名字——LangChain
    # 每次真正发起一次工具调用之前，会依次调用所有挂载中间件的这个方法。
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        # 如果short不是None（说明命中了短路拦截），直接返回这条合成的
        # 提示消息，不再调用handler（也就是不会真的去执行这个工具）；
        # 否则调用handler，让工具真正被执行。这种"三元表达式"写法
        # `A if 条件 else B`是"根据条件在两个值之间选一个"的简洁写法。
        return short if short is not None else handler(request)

    # `async def`定义的异步版本，做的是同样的事，只是配合`await`用在
    # 异步风格调用agent的场景——见model_router.py里对async/await的详细
    # 解释。
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        return short if short is not None else await handler(request)


class ConsecutiveRetryGuardMiddleware(AgentMiddleware):
    """同一个工具连续失败达到上限后短路拦截，逼停无效重试循环。

    Args:
        max_consecutive_errors: 同一工具连续失败几次后触发短路。
    """

    def __init__(self, max_consecutive_errors: int = 2) -> None:
        super().__init__()
        self._max = max_consecutive_errors
        # 记录"每个工具名当前连续失败了多少次"。
        self._errors: dict[str, int] = {}
        # 记录"上一次真正被调用的工具是哪个"——用来判断"这次调用和上次
        # 是不是同一个工具"，如果换了别的工具，说明之前那个工具的连续
        # 失败链条已经中断，需要清零（见下面_record_real_result的逻辑）。
        self._last_tool: str = ""

    def status(self) -> dict[str, Any]:
        """只读状态快照：最严重工具的 errors/max 占比。"""
        worst_tool, worst = "", 0
        for name, cnt in self._errors.items():
            if cnt > worst:
                worst, worst_tool = cnt, name
        return {
            "errors": dict(self._errors),
            "max": self._max,
            # `worst / self._max if self._max else 0.0` ——如果_max是0
            # （理论上不太可能，但防御性地处理一下），避免除以0报错，
            # 直接给0.0。
            "max_ratio": (worst / self._max if self._max else 0.0),
            "worst_tool": worst_tool,
        }

    def _short_circuit_message(self, request: ToolCallRequest, count: int) -> ToolMessage:
        name = request.tool_call.get("name", "")
        return ToolMessage(
            content=(
                f"[budget] '{name}' has failed {count} times in a row. "
                f"Stop retrying with variations of the same args. "
                f"Either try a different approach, or finalize the run."
            ),
            tool_call_id=request.tool_call.get("id", ""),
            name=name,
            status="error",
        )

    def _record_real_result(self, request: ToolCallRequest, result: Any) -> None:
        """只记录真正执行过的调用结果——短路生成的合成消息不应传入这里（ch12-01修复）。"""
        name = request.tool_call.get("name", "")
        if not name:
            # 拿不到工具名，没法记录，直接返回，不做任何事。
            return
        # 如果上一次调用的是别的工具（不是这次的name），且那个工具之前
        # 确实累计过失败次数，就把它清零——因为"连续失败"这个概念是
        # "针对同一个工具连续发生"，中间插入了别的工具的调用，就说明
        # 连续链条已经断了。
        if self._last_tool and self._last_tool != name and self._last_tool in self._errors:
            self._errors[self._last_tool] = 0
        # 更新"最近一次真正被调用的工具"记录，供下一次调用时比较。
        self._last_tool = name
        # `getattr(result, "status", None) if isinstance(result, ToolMessage)
        # else None` ——只有在result确实是ToolMessage类型时，才尝试读取
        # 它的status字段；如果result是别的类型（比如Command），直接当作
        # None处理，不强行读取一个可能不存在的属性。
        status = getattr(result, "status", None) if isinstance(result, ToolMessage) else None
        if status == "error":
            # 这次真实调用失败了，把这个工具的连续失败计数加1。
            self._errors[name] = self._errors.get(name, 0) + 1
        elif status == "success":
            # 这次真实调用成功了，说明连续失败链条中断，清零。
            self._errors[name] = 0

    def _maybe_short_circuit(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        if not name:
            return None
        count = self._errors.get(name, 0)
        if count < self._max:
            # 还没达到上限，放行。
            return None
        logger.warning("[RetryGuard] short-circuiting %s (errors=%d max=%d)", name, count, self._max)
        return self._short_circuit_message(request, count)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        if short is not None:
            # 命中短路——直接返回合成消息，注意这里**不调用**
            # `_record_real_result`：这正是模块docstring里提到的ch12-01
            # 修复点——短路生成的合成失败消息不能被当作"新的一次真实
            # 失败"继续累加计数，否则计数会随着AI反复重试已被拦截的
            # 工具而无意义地持续膨胀。
            return short
        # 没有命中短路，真正调用handler执行这个工具。
        result = handler(request)
        # 只有真正执行过的结果，才会被记录进连续失败计数器。
        self._record_real_result(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        if short is not None:
            return short
        result = await handler(request)
        self._record_real_result(request, result)
        return result


class ConsecutiveCallGuardMiddleware(AgentMiddleware):
    """连续多次"停滞类"工具调用（只读探索、无实际进展）后短路拦截。

    Args:
        stall_tools: 视为"停滞/探索"的工具名集合。
        progress_tools: 视为"确实在推进任务"的工具名集合，出现即清零停滞计数。
        max_stall_calls: 连续停滞调用超过几次后触发短路。
    """

    # 这两个是"类属性"（写在类的body里、不在任何方法内部的变量）——
    # 所有实例默认共享同一份，除非构造函数里用实例专属的_stall/_progress
    # 覆盖它（见下面__init__的写法）。作为"默认工具清单"，写成类属性
    # 而不是每次创建实例都重新构造一份，可以避免重复创建相同内容的
    # frozenset对象。
    DEFAULT_STALL: frozenset[str] = frozenset({"ls", "glob", "read_file", "grep"})
    DEFAULT_PROGRESS: frozenset[str] = frozenset(
        {"write_file", "edit_file", "run_tests"}
    )

    def __init__(
        self,
        *,
        stall_tools: frozenset[str] | None = None,
        progress_tools: frozenset[str] | None = None,
        max_stall_calls: int = 3,
    ) -> None:
        super().__init__()
        # `stall_tools if stall_tools is not None else self.DEFAULT_STALL`
        # ——注意这里特意用`is not None`而不是直接写
        # `stall_tools or self.DEFAULT_STALL`：因为`frozenset()`（一个
        # 空集合）在Python里也是"假值"，如果调用方明确想传一个空集合
        # （表示"不认为任何工具是停滞类"），用`or`写法会把这个合法的
        # 空集合误判成"没传"，转而错误地用上默认值。`is not None`才能
        # 准确区分"真的没传（None）"和"传了一个恰好是空的集合"这两种
        # 不同情况。
        self._stall = stall_tools if stall_tools is not None else self.DEFAULT_STALL
        self._progress = progress_tools if progress_tools is not None else self.DEFAULT_PROGRESS
        self._max = max_stall_calls
        # 记录"当前连续停滞调用了多少次"。
        self._stall_count = 0

    def status(self) -> dict[str, Any]:
        return {
            "stall_count": self._stall_count,
            "max": self._max,
            "ratio": (self._stall_count / self._max if self._max else 0.0),
        }

    def _evaluate(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        if name in self._progress:
            # 这次调用的是"推进任务类"工具——说明AI确实在往前走，把
            # 停滞计数清零，放行这次调用。
            self._stall_count = 0
            return None
        if name in self._stall:
            # 这次调用的是"停滞/探索类"工具——累加停滞计数。
            self._stall_count += 1
            count = self._stall_count
            if count > self._max:
                logger.warning(
                    "[CallGuard] %d consecutive stall calls (last: %s) — short-circuiting",
                    count, name,
                )
                # `", ".join(sorted(self._stall))` ——`sorted(...)`把
                # 集合按字母顺序排成一个列表（集合本身是无序的，直接
                # 遍历顺序不固定，排序后日志/提示文字里的顺序才是稳定、
                # 可预测的），再用逗号+空格拼接成一句话，方便嵌入提示
                # 文字里展示给AI看。
                stall_names = ", ".join(sorted(self._stall))
                return ToolMessage(
                    content=(
                        f"[budget] You have called exploration tools "
                        f"({stall_names}) {count} times in a row without making "
                        f"progress. Stop exploring and take a concrete action, "
                        f"or produce a final summary of what you found."
                    ),
                    tool_call_id=request.tool_call.get("id", ""),
                    name=name,
                    status="error",
                )
        # 这次调用的工具既不属于"推进类"也不属于"停滞类"（比如某个没有
        # 被归类的工具），不影响停滞计数，正常放行（返回None）。
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        short = self._evaluate(request)
        return short if short is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        short = self._evaluate(request)
        return short if short is not None else await handler(request)


class TokenBudgetMiddleware(AgentMiddleware):
    """累计输入token超过预算后，短路整个模型调用循环。

    Args:
        max_total_input_tokens: 单次运行的输入token预算上限。
    """

    def __init__(self, max_total_input_tokens: int = 200_000) -> None:
        super().__init__()
        self._budget = max_total_input_tokens
        # 复用上面定义好的TokenCounter类来做实际的token计数——不重复
        # 实现一份计数逻辑（模块docstring里也提到，这个计数器同时被
        # health_monitor.py里的GuardHealthMonitorMiddleware共用）。
        self._counter = TokenCounter()

    def status(self) -> dict[str, Any]:
        spent = self._counter.current_total
        return {"spent": spent, "budget": self._budget, "ratio": (spent / self._budget if self._budget else 0.0)}

    def _check(self, request: ModelRequest) -> ModelResponse | None:
        messages = getattr(request, "messages", None) or []
        spent = self._counter.count(list(messages))
        if spent < self._budget:
            # 还没花完预算，放行（返回None表示"不需要短路"）。
            return None
        logger.warning("[TokenBudget] spent=%d budget=%d — short-circuiting", spent, self._budget)
        # 超预算了——不再调用真正的模型，而是直接构造一条"合成的"AI
        # 回复消息，告诉用户预算已经耗尽、这次运行到此为止。
        return ModelResponse(
            result=[
                AIMessage(
                    content=(
                        f"[budget] Token budget exhausted "
                        f"({spent}/{self._budget} input tokens). "
                        f"Stopping agent run. Please open a new conversation "
                        f"if more work is needed."
                    )
                )
            ],
            structured_response=None,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        short = self._check(request)
        return short if short is not None else handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        short = self._check(request)
        return short if short is not None else await handler(request)
