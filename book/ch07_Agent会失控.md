# 第7章 —— Agent会失控

代码位置：`packages/ainative-guardrail/`

## 一个真实会发生的场景

你给一个Agent配了个"浏览器操作"工具。某次任务里，Agent遇到一个奇怪的网页布局，开始反复点击同一个按钮、反复刷新页面截图，却始终判断"任务还没完成"——这个循环持续了两个小时，调用了几千次工具，直到你的账单提醒才让你发现这件事。

这不是模型"变笨"了，而是大模型作为一个"决策者"天生具备的一种风险：**它没有内置的"我好像卡住了"的自我意识，除非你从外部给它装上这套意识**。传统程序不会有这个问题——一个`for`循环要么按预期结束，要么因为逻辑错误无限循环（这种bug通常在测试阶段就会暴露）。但Agent的"循环"是由大模型的每一次决策驱动的，它的决策路径不可预测，意味着"卡住"这件事可能在生产环境的任何一个未曾设想过的场景下发生。

## `ainative-guardrail`：一组独立的"安全阀"

这个包提供的不是一个大而全的"Agent管理系统"，而是**一组可以独立选用、任意组合的中间件**——这是刻意的设计。看一下它的文件结构：

```
ainative_guardrail/
├── model_router.py       — 按复杂度路由到便宜/主力模型
├── budget_middleware.py  — 5个预算/节流中间件（本章后面几节详细讲）
├── health_monitor.py     — 组合多个中间件的健康状态，给出综合判断
├── idempotency.py        — 幂等键管理，防止重复扣款等副作用
└── backpressure.py       — 队列积压预警 + 限流消费
```

每一个中间件都继承自LangChain的`AgentMiddleware`——一个"能在模型调用/工具调用真正发生的前后插入自己逻辑"的钩子机制。这意味着你可以只用其中一两个（比如只要"连续失败检测"，不要"模型路由"），互相之间没有强制依赖。

## 中间件的核心机制：`wrap_tool_call`

理解这一整个包，最关键的是理解一个模式——LangChain的中间件是怎么"插入"到Agent执行流程里的。以最简单的调用次数限制器`MCPCallLimiterMiddleware`为例：

```python
class MCPCallLimiterMiddleware(AgentMiddleware):
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        return short if short is not None else handler(request)
```

`wrap_tool_call`这个方法名是LangChain框架规定好的"钩子"——每次Agent真正要执行一次工具调用之前，框架会依次调用所有挂载的中间件的这个方法。`handler`参数代表"真正去执行这次工具调用"这个动作本身——**中间件自己决定要不要调用它**。如果中间件判断"这次调用应该被拦下"（比如已经超过调用次数上限），就直接构造一个"合成的"结果返回，压根不调用`handler`，工具实际上根本没有被执行；如果判断"应该放行"，就调用`handler(request)`，让真正的工具执行。

这个模式——**"决定要不要调用下一步，而不是被动接收结果后再处理"**——是本章后面所有中间件共享的核心结构。理解了这一点，`budget_middleware.py`里的几个类看起来就不再是各自独立的黑盒，而是同一个模式在不同判断条件下的具体应用。

## 给AI看的提示，和给人看的提示是两回事

值得留意的一个细节：当中间件决定拦截一次调用时，它构造的"合成消息"里的文字，是特意写给**AI模型自己**看的：

```python
return ToolMessage(
    content=(
        f"[budget] Call cap of {limit} reached for tool '{name}'. "
        f"Use what you already have in this conversation; do NOT "
        f"call '{name}' again in this run."
    ),
    tool_call_id=request.tool_call.get("id", ""),
    name=name,
    status="error",
)
```

这条消息会被塞进对话历史里，模型会"读到"它，并据此调整自己接下来的行为——这不是普通的错误日志，而是**一句用来影响下一步决策的指令**。`status="error"`这个字段也很重要：它明确告诉模型"这次调用没有成功执行"，避免模型误以为自己拿到了一个正常的、可以继续依赖的结果。这提醒我们：设计Agent护栏时，除了"要不要拦截"这个逻辑判断，"拦截之后怎么把这件事清楚地告诉AI本身"同样是设计的一部分——写得含糊，AI可能会继续用别的方式尝试同一件被禁止的事。

## 本章小结

- Agent的"失控"不是传统意义上的代码bug，而是大模型决策路径不可预测导致的一种新型风险，需要从外部专门设计防护机制。
- `ainative-guardrail`用一组彼此独立、可任意组合的中间件应对这个问题，每一个都继承`AgentMiddleware`。
- 核心机制是`wrap_tool_call`/`wrap_model_call`这类"钩子"：中间件拿到"下一步该做什么"（`handler`），自己决定要不要真的调用它。
- 拦截时构造的提示文字是写给AI看的，需要清楚地表明"这次调用失败了、不要再这样做"，这本身也是设计的一部分。

## 动手做

```python
from ainative_guardrail.budget_middleware import MCPCallLimiterMiddleware

limiter = MCPCallLimiterMiddleware(per_tool_limit={"search_web": 2})

class FakeRequest:
    tool_call = {"name": "search_web", "id": "call-1"}

for i in range(4):
    result = limiter.wrap_tool_call(FakeRequest(), handler=lambda r: "real result")
    print(i, result)
```

观察第3次、第4次调用会发生什么——真的执行了`handler`吗？

## 面试可能会问

**问：你会怎么设计一套通用的"Agent运行时护栏"？**

答题思路：先强调"护栏应该是可组合的独立单元，而不是一个大而全的黑盒"，再说明"每个护栏的核心是决定要不要放行下一步动作，而不是事后审查结果"，可以举LangChain中间件`wrap_tool_call(request, handler)`这个具体模式作为例子。加分项：提到"拦截时反馈给AI的信息本身也需要设计"，这是很多候选人会忽略的一层。
