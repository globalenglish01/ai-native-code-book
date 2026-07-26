"""综合健康度预警——多层护栏各自独立触发终止阈值，但缺乏"任务是否正在滑向
失控"的综合视角。

改造自真实项目里验证过的设计：在每轮model call时计算「递归步数/recursion_limit」
「累计输入token/token预算」相对各自上限的占比，当**两个及以上维度**同时超过
可配置比例（默认0.7）时打一条结构化WARNING，供运维提前关注。

设计约束（保持原版定位不变）：
- 只监控预警，不改变任何护栏的终止判断/阈值——纯旁路观测。
- 每个run最多告警一次（去抖），避免刷屏。
- 递归步数用"本中间件被调用次数"近似，不读取框架内部计数，保持零耦合。

复用`budget_middleware.TokenCounter`（ch10-01已修复累计值/估算值混用问题），
而不是重新实现一份token计数逻辑。
"""

from __future__ import annotations

# logging是Python标准库自带的"日志"模块——见model_router.py里的详细解释。
import logging

# os模块用来读取"环境变量"——见ainative_core.config模块的详细解释。
import os

# Awaitable/Callable 用来给"函数本身"写类型注解，见model_router.py里的
# 详细解释。
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

# 从本包（ainative_guardrail）自己的另一个文件budget_middleware.py里，
# 导入之前已经实现好的TokenCounter——这样就不需要在这个文件里再写一遍
# "怎么统计token用量"的逻辑，两个中间件共用同一份、已经验证过的实现。
from ainative_guardrail.budget_middleware import TokenCounter

logger = logging.getLogger(__name__)


def _default_warn_ratio() -> float:
    """综合健康度预警阈值，可用 AINATIVE_GUARD_HEALTH_WARN_RATIO 覆盖，默认0.7。"""
    # `try/except` 是Python处理"可能会出错的代码"的标准写法：先尝试
    # 执行try代码块，如果真的抛出了指定类型的异常，就转而执行except
    # 代码块，而不是让程序直接崩溃退出。
    try:
        # 读取环境变量（读不到就用字符串"0.7"兜底），再用`float(...)`
        # 尝试把它转换成浮点数——如果环境变量被设成了一个根本不是数字
        # 的字符串（比如"abc"），`float("abc")`会抛出ValueError。
        v = float(os.environ.get("AINATIVE_GUARD_HEALTH_WARN_RATIO", "0.7"))
        # 即使成功转换成了数字，还要检查它是不是一个"合理的比例"——必须
        # 大于0且不超过1（0.7表示"70%"，超过1没有意义）。不满足就还是
        # 回退到默认的0.7，而不是让一个离谱的配置值（比如-5或1000）
        # 真的生效。
        return v if 0.0 < v <= 1.0 else 0.7
    except (TypeError, ValueError):
        # 转换失败（比如环境变量根本不是数字字符串），同样回退到0.7。
        return 0.7


class GuardHealthMonitorMiddleware(AgentMiddleware):
    """当多个护栏维度同时接近上限时提前告警，纯旁路观测不改变任何终止判断。

    Args:
        recursion_limit: 本次运行的递归步数上限（用于计算占比，不强制终止）。
        token_budget: 本次运行的输入token预算上限（同上）。
        warn_ratio: 单一维度视为"接近上限"的占比阈值，默认0.7。
    """

    def __init__(
        self,
        *,
        recursion_limit: int,
        token_budget: int,
        warn_ratio: float | None = None,
    ) -> None:
        super().__init__()
        # `max(1, int(recursion_limit))` ——先把传入的值转成整数，再
        # 保证结果至少是1，避免调用方不小心传了0或负数，导致后面用它
        # 做除法分母时出现"除以0"或者比例永远是负数这类荒谬结果。
        self._recursion_limit = max(1, int(recursion_limit))
        self._token_budget = max(1, int(token_budget))
        # `warn_ratio if warn_ratio is not None else _default_warn_ratio()`
        # ——调用方显式传了值就用调用方的，没传（是None）就调用上面的
        # 辅助函数，从环境变量读取默认值。
        self._warn_ratio = warn_ratio if warn_ratio is not None else _default_warn_ratio()
        # 记录"到目前为止，这次运行已经经过了多少轮模型调用"——用这个
        # 数字近似表示"递归步数"，不需要读取LangGraph框架内部真正的
        # 递归计数器，保持这个中间件和框架内部实现细节零耦合。
        self._steps = 0
        # 记录"这次运行是否已经告警过"——保证"每个run最多告警一次"这个
        # 去抖设计（避免同一次运行反复打印相同的警告日志刷屏）。
        self._warned = False
        self._counter = TokenCounter()

    def _evaluate(self, request: ModelRequest) -> dict | None:
        # 每次这个方法被调用（也就是每发起一次模型调用），步数加1。
        self._steps += 1
        messages = getattr(request, "messages", None) or []
        tokens = self._counter.count(list(messages))
        # 构造一个字典，记录"递归步数"和"token用量"这两个维度各自的
        # (当前值, 上限, 占比)——用元组把三个相关的数字打包在一起，
        # 方便后面统一处理。
        dims = {
            "recursion": (self._steps, self._recursion_limit, self._steps / self._recursion_limit),
            "tokens": (tokens, self._token_budget, tokens / self._token_budget),
        }
        # `{k: v for k, v in dims.items() if v[2] >= self._warn_ratio}`
        # 是一个"字典推导式"（写法和列表推导式类似，只是收集结果时用
        # `{key: value for ...}`的形式，最终得到一个新字典而不是列表）。
        # 这里的意思是："只保留dims里占比（v[2]，也就是元组的第三项）
        # 达到或超过警戒阈值的那些维度"——`hot`就是"当前处于危险区间的
        # 维度集合"。
        hot = {k: v for k, v in dims.items() if v[2] >= self._warn_ratio}
        # 只有当"危险维度数量达到2个或以上"且"这次运行还没告警过"，
        # 才真正触发告警——单一维度接近上限，其他护栏中间件自己就会
        # 处理，不需要这里重复提醒；只有"多个维度同时告急"才是这个
        # 中间件真正想捕捉的"综合性风险信号"。
        if len(hot) >= 2 and not self._warned:
            self._warned = True
            # `"，".join(f"{k} {cur}/{lim}（{ratio:.0%}）" for k, (cur, lim,
            # ratio) in dims.items())` ——这是一个生成器表达式，遍历
            # dims里的每一项，拼出类似"recursion 45/60（75%）"这样的
            # 描述文字，再用中文顿号"，"把所有维度的描述连接成一句话。
            # `{ratio:.0%}`是f-string的"格式说明符"写法：`.0%`表示"按
            # 百分比格式显示，不保留小数位"，比如0.75会被显示成"75%"。
            desc = "，".join(f"{k} {cur}/{lim}（{ratio:.0%}）" for k, (cur, lim, ratio) in dims.items())
            logger.warning(
                "[GuardHealth] 任务接近多重护栏边界（%d 个维度超过 %.0f%%）：%s",
                len(hot), self._warn_ratio * 100, desc,
                # `extra={...}` 是Python logging模块的一个参数，允许在
                # 打印文字日志的同时，附加一份结构化的数据字典——如果
                # 日志系统配置了"结构化日志"输出（比如写入支持JSON查询
                # 的日志平台），这些额外字段可以被单独检索/过滤，而不是
                # 只能靠人工阅读拼好的文字描述。
                extra={
                    "guard_health_event": "multi_guard_near_limit",
                    "hot_dimensions": list(hot.keys()),
                    "warn_ratio": self._warn_ratio,
                    # 这又是一个字典推导式：把dims转换成"每个维度是一个
                    # 更详细的小字典"的形式（分别标注current/limit/ratio
                    # 三个字段名），方便日志平台按字段名精确查询，而不是
                    # 只有一个笼统的元组。
                    "dimensions": {
                        k: {"current": current, "limit": limit, "ratio": ratio}
                        for k, (current, limit, ratio) in dims.items()
                    },
                },
            )
            return dims
        # 没有触发告警条件（要么危险维度不足2个，要么这次运行已经告警
        # 过了），返回None。
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        # 这个中间件是"纯旁路观测"——不管_evaluate返回什么，都会继续
        # 调用handler，让真正的模型调用照常发生；_evaluate的返回值本身
        # 在这里也没有被使用（只是为了触发它内部可能打印的警告日志）。
        self._evaluate(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self._evaluate(request)
        return await handler(request)
