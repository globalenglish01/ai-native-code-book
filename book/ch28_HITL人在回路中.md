# 第28章 —— HITL：人在回路中

代码位置：`packages/ainative-workflow/src/ainative_workflow/hitl.py`、`packages/ainative-workflow/src/ainative_workflow/hitl_policy.py`、`packages/ainative-workflow/src/ainative_workflow/graph.py`

## 一个"暂停"和"失败"完全不是一回事的场景

还是报销审批那个例子：员工提交了一笔8000元的报销，超过了"主管免审"的阈值，系统需要暂停下来，等主管点一下"批准"或"拒绝"，然后才能继续走后面的财务入账流程。

这里有一件事必须想清楚：**这次"暂停"不是任何意义上的错误**。程序没有崩溃，也没有任何一步逻辑执行失败，它只是"正常地、按预期地"走到了一个"需要人来做决定"的节点，然后主动停下来等着。如果你把这种情况和真正的失败（比如数据库连不上、上游服务返回500）混在一起处理，会出很大的问题——一个真正的失败应该被记录、报警、也许要重试；而一次HITL暂停不应该报警，它就是流程设计好的一部分，甚至可能会持续好几个小时、好几天，直到主管有空点开审批页面。

`ainative-workflow`用一个特殊的异常类型把这两种情况彻底分开，这是本章第一个要讲透的机制。

## `WorkflowPaused`：一个"不是失败"的异常

先看这个异常类的完整定义：

```python
class WorkflowPaused(Exception):
    """节点执行时抛出，表示"需要人工介入，在此暂停"（配合`ainative_workflow.hitl`使用）。

    Args:
        payload: 暂停时需要展示给人工审批者的信息，会被记录在`WorkflowRun.pause_payload`。
    """

    def __init__(self, payload: Any = None) -> None:
        super().__init__("workflow paused, awaiting external input")
        self.payload = payload
```

第一次看到这段代码的人可能会有点困惑：**"暂停"为什么要用"异常"（Exception）来实现？**这其实是Python里一种很常见、也很实用的模式，叫"异常驱动的控制流"（exception-based control flow）——异常不仅仅用来表示"出错了"，也可以用来表示"发生了一件需要中断当前正常执行路径的事情"，哪怕这件事本身是完全正常、甚至是设计好要发生的。用异常来实现暂停有一个很大的好处：一个节点的执行函数，可以在调用栈的**任何深度**（不只是最外层）里，随时决定"我要暂停"，只需要`raise WorkflowPaused(...)`，不需要把"是否要暂停"这个信号一层一层手动往外传递——这正是异常机制本身要解决的问题：把"发生了一件特殊情况"从正常的函数返回值路径里剥离出来。

注意构造函数里的一个细节：`super().__init__("workflow paused, awaiting external input")`传给父类的是一段**固定的**说明文字，而真正需要展示给审批者的具体信息（比如"这笔报销是8000元，超过了主管免审的5000元阈值"），是通过`self.payload`这个单独的属性存的，不是拼进异常消息文字里。为什么要这样分开？因为`payload`可能是任意复杂的数据结构（一个嵌套字典、甚至包含要展示的表单结构），不适合被硬塞进一句异常消息的字符串里；而异常消息（`str(exc)`）通常是给"打日志、给人读一眍就知道发生了什么类型的事"用的，两者的受众和用途不一样，分开存更清晰。

## `_execute`循环怎么处理这次"暂停"

回到graph.py的`_execute`方法，专门看它处理`WorkflowPaused`的这一段：

```python
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
```

注意这里有两个`except`分支，`WorkflowPaused`被单独捕获、排在`Exception`前面——因为`WorkflowPaused`本身也是`Exception`的子类，如果把顺序反过来（先写`except Exception`），暂停信号会被"真正的失败"那个分支误吞掉，`WorkflowPaused`携带的`payload`会丢失，整个运行会被错误地标记成`FAILED`。Python的`except`是按书写顺序依次匹配的，**更具体的异常类型必须写在更宽泛的类型前面**，这是一条容易被忽略、却很关键的规则。

捕获到`WorkflowPaused`之后做的事：把这个节点的状态标记成`PAUSED`（不是`FAILED`），记录下`paused_at`（暂停在哪个节点）和`pause_payload`（要展示给审批者的信息），然后**直接`return run`**——不再往后跑剩余的节点。调用方拿到这个`run`之后，可以把它完整地持久化下来（存进数据库或者别的地方），然后该干嘛干嘛去，等真人做出决定后，再调用`resume()`继续。这就是为什么`WorkflowRun`要设计成"一份可以被完整保存、之后再复原继续跑"的状态对象——本质上是把"一次长时间运行的流程"，切成了"可以被暂停、序列化、之后恢复"的一段一段。

再往下看`except Exception as exc`这个分支，处理的是**真正的失败**：状态标记成`FAILED`，记下`failed_at`和`error`，还专门打了一条`logger.warning`。这里故意用了很宽泛的`except Exception`（比ruff默认建议的"只捕获明确预期的异常类型"更宽），代码里专门留了一条注释解释这是刻意为之：这个方法的职责就是"不管某个节点内部抛出什么异常，都要把整个工作流安全地转入FAILED终态并记录下来"，不能让某个节点意外抛出的异常，直接把调用方整个程序也带崩溃。这是一种很常见的"边界防护"设计：**在一个会调用大量外部/用户提供代码的地方（这里是`node.fn`，可能是任何人写的任意逻辑），故意用宽泛的异常捕获兜底，把"崩溃范围"限制在这一个节点上，不让它向上蔓延**。

对比这两个分支，能看清`WorkflowRun`里`NodeStatus`为什么要同时存在`PAUSED`和`FAILED`这两种终态，而不是只有一种"没跑完"状态——因为调用方对这两种情况要做的事完全不同：`FAILED`应该报警、排查、可能需要人工介入修代码；`PAUSED`应该被安静地记录下来，等待一个**预期之内**的、来自人的输入。把这两者从状态设计的层面就彻底分开，调用方写业务逻辑的时候可以很自然地分别处理，不需要额外去猜"这次到底是出错了还是本来就该等着"。

## hitl.py：从执行结果里"认出"中断，而不是自己抛异常

`WorkflowPaused`是`ainative-workflow`自己的DAG引擎专用的暂停机制。但`hitl.py`要解决的是另一个、更通用的问题——很多真实项目并不是用`Workflow`这套引擎，而是直接用像LangGraph这样的外部agent编排框架。这类框架的约定往往不是"抛一个异常表示中断"，而是"**执行正常返回，但返回的结果字典里带着一个特殊的key**，表示这次执行其实被中断了"。这就意味着，调用方必须**主动检查**这个特殊key是否存在，否则很容易把"其实是被中断、在等人处理"误判成"顺利执行完成了"。

模块docstring交代了这套设计的来源：

```python
"""Human-in-the-loop 中断检测 + 超时安全默认值。

改造自真实项目里验证过的设计：Agent运行命中需要人工审批的节点时不抛
异常，而是在返回结果里带上一个标记键（原版是LangGraph约定的
`__interrupt__`），调用方必须显式检查这个键，否则会把"等待人工审批"
误判为"执行完成"。
"""
```

这里的`DEFAULT_INTERRUPT_KEY = "__interrupt__"`，之所以用这个前后带双下划线的古怪名字，是为了兼容LangGraph的既有约定；但这个key名字被做成了可配置参数，而不是写死在函数内部，因为不是所有调用方都在用LangGraph，别的框架如果用了不同的key名，调用方可以自己传一个不同的`interrupt_key`。

来看核心函数`extract_interrupt`：

```python
def extract_interrupt(
    result: dict[str, Any], *, interrupt_key: str = DEFAULT_INTERRUPT_KEY
) -> dict[str, Any] | None:
    """从agent执行结果中提取中断payload（未命中中断则返回`None`）。

    已知限制：中断标记理论上可以是一个列表（多个并行中断），本函数只返回
    第一个元素。这在"每次运行只会挂载一个会触发中断的中间件/节点"这个
    前提下是安全的——如果检测到多个中断，会记录一条WARNING并仍然只返回
    第一个，其余被静默丢弃。如果你的编排里存在多个独立触发中断的分支，
    需要改用能处理`list[dict]`的调用方式，不要依赖本函数的单值返回。
    """
    interrupts = result.get(interrupt_key)
    if not interrupts:
        return None
    if len(interrupts) > 1:
        logger.warning(
            "[HITL] detected %d parallel interrupts, only the first is handled, %d ignored",
            len(interrupts), len(interrupts) - 1,
        )
    first = interrupts[0]
    return first.value if hasattr(first, "value") else first
```

这个函数值得留意的第一处细节是`result.get(interrupt_key)`而不是`result[interrupt_key]`——用`.get()`是因为"这次执行根本没有发生中断"是完全正常、天天都会发生的情况，不应该被当成一次异常/报错来处理；如果这个key压根不存在，`.get()`安静地返回`None`，`if not interrupts`这一行就会把`None`和空列表`[]`这些"假值"都统一判定为"没有中断，正常返回"。

第二处细节，也是这个函数最诚实的地方：**它在docstring里明确写清楚了自己的已知限制**。理论上一次执行可能同时触发多个"并行的"中断点（比如同一批操作里，两个不同的子任务分别都需要人工审批），但`extract_interrupt`的设计只处理"只有一个中断"这个最常见的场景，检测到多个时，不会报错、也不会返回全部，而是记一条`WARNING`日志、然后仍然只返回第一个，其余的被**静默丢弃**。这不是一个bug，而是一个刻意做出的、有明确适用边界的设计——文档里直接告诉你"如果你的编排里存在多个独立触发中断的分支，不要依赖本函数的单值返回"。这是一个很值得学习的工程写作习惯：**当一个函数的设计有已知局限时，与其假装它什么场景都能处理，不如老老实实把边界写进docstring，并且用日志让问题在真实发生时"被看见"，而不是悄无声息地丢数据却没人知道**。

最后一行`first.value if hasattr(first, "value") else first`是一个鸭子类型的兼容处理：LangGraph真实返回的中断对象，有时是包了一层的`Interrupt`对象（真正数据在`.value`属性里），有时又直接是一个普通字典。`hasattr(first, "value")`检查"这个对象身上有没有一个叫`value`的属性"，有就取`first.value`，没有就把`first`本身原样返回——这样不管拿到的是包装过的对象还是裸字典，这个函数都能正确取出真正想要的数据，调用方不需要关心具体是哪种情况。

## hitl_policy.py：把"超时不能默认批准"做成结构性的不可能

现在流程暂停了，等着人来处理。但如果人一直不回应呢？超时之后该怎么办，是这个包要回答的最后一个、也是安全性最重要的问题。模块docstring把这件事的分量说得很清楚：

```python
"""HITL超时业务规则：默认时长读取 + 从代码结构上禁止"超时默认放行"这类危险默认值。

改造自真实项目里验证过的设计：多条独立的HITL执行路径（不同存储/触发
机制）容易各自硬编码一份几乎相同的"超时后怎么办"业务规则，任一处调整
另一处不会感知。核心安全原则：**超时后自动决定绝不能是"批准"**——
`safe_timeout_decision()`从函数签名结构上就不接受"决定类型"这个参数，
杜绝调用方误传一个危险的默认值，而不是靠注释/约定去防范。
"""
```

这段话讲了一个真实存在过的问题：一个系统里往往不止一条HITL路径（可能一条走数据库轮询、一条走消息队列触发，触发机制不同），如果每条路径各自写一遍"超时后怎么处理"的逻辑，很容易出现"改了一处、忘了改另一处"的情况——最危险的走向是，某一条路径的超时逻辑被不小心写成了"超时就当作已批准"（可能是图省事，也可能是复制粘贴时改错了一个字），而这类操作一旦是"自动转账""自动发货"之类有真实业务后果的动作，这种疏漏可能造成实打实的损失。

看这个包给出的解法——先看构造超时决定的函数：

```python
_SAFE_TIMEOUT_DECISION_TYPE = "reject"
_DEFAULT_TIMEOUT_MESSAGE = "Approval timed out (no response within the timeout window); automatically rejected."


def safe_timeout_decision(message: str | None = None) -> dict:
    """构造一个"超时自动决定"。

    安全原则：类型恒为`reject`，调用方无法指定其他类型（本函数不接收
    type参数）——这是唯一被允许的超时默认决定构造入口。
    """
    return {
        "type": _SAFE_TIMEOUT_DECISION_TYPE,
        "message": message or _DEFAULT_TIMEOUT_MESSAGE,
    }
```

这里最值得反复咀嚼的是函数的**参数列表本身**：`safe_timeout_decision(message: str | None = None)`——只有一个可选的`message`（给人看的提示文案），完全没有类似`decision_type`这样的参数。这意味着，不管谁在什么地方调用这个函数，它产出的"决定"永远、只能是`{"type": "reject", ...}`这一种类型，**不存在任何一种调用方式能让它返回"approve"**。

这是这一章、也是这两个文件里最重要的一课，值得单独拎出来讲清楚："类型恒为reject"这件事，不是靠写在文档里提醒大家"记得超时要处理成拒绝"，也不是靠代码审查里人工盯着有没有人传错参数——它是靠**函数签名本身的形状**保证的。如果这个函数被设计成`safe_timeout_decision(decision_type: str = "reject", message: str | None = None)`，哪怕默认值也是`"reject"`，依然会存在一条隐患：某个调用方出于某种原因（写错了、复制了别处的代码没改、以为这里可以传别的值）传入了`decision_type="approve"`，代码在语法和类型层面完全合法，运行时也不会报任何错，但业务后果可能是灾难性的。而现在这个函数**从结构上就不给这条路留口子**——它压根不接受一个"类型"参数，调用方就算想传错，也无从传起。这种设计思路有一个专门的说法，叫"safety by construction"（构造即安全）：**不是靠约定、靠人自觉遵守规则来保证安全，而是让"错误的用法"在类型系统/函数签名层面就变得写不出来**。这是比"写注释警告""代码审查里靠人盯"高一个层级的安全保障方式，因为它不依赖任何人的记忆或自觉性。

再看批量版本`safe_timeout_decisions`（注意多了一个`s`）：

```python
def safe_timeout_decisions(count: int, message: str | None = None) -> list[dict]:
    """为`count`个待审批操作各生成一个安全的超时reject决定。"""
    return [safe_timeout_decision(message) for _ in range(max(0, count))]
```

这个函数本身没有引入任何新的安全设计，它只是把`safe_timeout_decision`调用`count`次、包成一个列表——这正是"复用同一个安全构造入口，而不是自己重新拼一份逻辑"的具体体现：一次审批批次里可能同时有好几个操作在等人处理（对照上一节`hitl.py`里`count_pending_decisions`算出来的那个数字），超时之后要给每一个都生成一份"拒绝"决定，而不是给整个批次笼统地生成一个，这个函数就是干这件事的，但它做法上依然是"调用那唯一一个安全入口很多次"，没有另开一条可能被搞错的路径。`max(0, count)`是一处很小的防御性写法：万一调用方不小心传入了负数，`range(max(0, count))`会被强制归零成一个空循环，不依赖`range()`对负数参数"恰好也是空循环"这个隐含行为，把意图写得更明确。

最后看配套的超时时长读取函数，它体现的是另一种"fail-safe"（失败时选择更安全的一侧）思路：

```python
def read_timeout_seconds(env_name: str, *, default: int = DEFAULT_TIMEOUT_SECONDS) -> int:
    """读取指定环境变量的超时秒数；缺失/非法/非正数都回退到`default`（fail-safe）。"""
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("HITL timeout config %s=%r is invalid, falling back to %ds", env_name, raw, default)
        return default
    if value <= 0:
        logger.warning("HITL timeout config %s=%d is non-positive, falling back to %ds", env_name, value, default)
        return default
    return value
```

这个函数处理"运维配置的超时环境变量"，有三层校验：环境变量压根没配置、配置了但不是合法数字、配置了合法数字但是0或负数——三种情况都不会让程序崩溃，而是打一条`WARNING`日志、回退到`default`（默认值是`DEFAULT_TIMEOUT_SECONDS = 86400`，也就是24小时）。这和`safe_timeout_decision`的"结构性安全"是两种不同、但互补的安全思路：`safe_timeout_decision`保证"超时后的决定类型不会被搞错"，`read_timeout_seconds`保证"就算超时时长这个配置项本身被运维配错了，系统也不会直接崩掉，而是回退到一个已知安全的默认值"，两者合在一起，才是一套完整的"人不回应该怎么办"的兜底方案。

## 本章小结

- `WorkflowPaused`是一种"预期内的中断"，不是失败——用异常机制实现是因为它需要能从调用栈任意深度直接中断当前节点的执行，而不需要把"是否暂停"一层层手动往外传递；`_execute`循环必须把`WorkflowPaused`单独捕获在`except Exception`之前，否则暂停信号会被误判成失败、payload也会丢失。
- `NodeStatus.PAUSED`和`NodeStatus.FAILED`是两种完全不同的终态：前者应该被安静记录、等待人工输入；后者应该报警排查——把这两种语义从状态设计层面彻底分开，调用方才能正确地区别对待。
- `hitl.py`的`extract_interrupt`服务于"结果里带标记键"这类约定（如LangGraph），而不是异常机制；它诚实地在docstring里写明了"只处理单个中断，多个并行中断只取第一个并记WARNING"这条已知限制，而不是假装能处理所有场景。
- `hitl_policy.py`最核心的一课：`safe_timeout_decision()`的函数签名压根不接受"决定类型"这个参数，让"超时默认批准"这种危险配置从结构上就不可能被写出来——这是"safety by construction"（构造即安全），比"写文档提醒"或"代码审查靠人盯"更可靠，因为它不依赖任何人的自觉性。
- `read_timeout_seconds`用"缺失/非法/非正数都回退到安全默认值"的fail-safe策略处理配置错误，和`safe_timeout_decision`的结构性安全互为补充，共同构成"人一直不回应时该怎么办"的完整兜底方案。

## 动手做

用真实的API，模拟一次"暂停—持久化—超时兜底—resume"的完整流程：

```python
import asyncio
from ainative_workflow import Workflow, WorkflowNode, WorkflowPaused
from ainative_workflow.hitl_policy import safe_timeout_decision


def fetch(ctx: dict) -> dict:
    return {"amount": 8000}


def request_approval(ctx: dict) -> str:
    if ctx["raw"]["amount"] > 5000:
        raise WorkflowPaused(payload={"amount": ctx["raw"]["amount"], "reason": "超过主管免审阈值"})
    return "auto-approved"


wf = Workflow([
    WorkflowNode(name="fetch", fn=fetch, output_key="raw"),
    WorkflowNode(name="approve", fn=request_approval, depends_on=("fetch",), output_key="decision"),
])

run = asyncio.run(wf.run({}))
assert run.is_paused
print("暂停在:", run.paused_at, "payload:", run.pause_payload)

# 假设主管一直没回应，超时兜底：
timeout_decision = safe_timeout_decision("主管24小时未响应，自动拒绝")
print(timeout_decision)   # {'type': 'reject', 'message': '主管24小时未响应，自动拒绝'}

run = asyncio.run(wf.resume(run, resume_context={"decision": timeout_decision}))
print(run.is_completed, run.context["decision"])
```

跑完之后，试着把`request_approval`改成直接`raise ValueError("网关连接失败")`（模拟一次真正的失败），对比`run.node_status`和`run.is_paused`/`run.is_failed`这几个属性的差异，体会"暂停"和"失败"在状态上到底有什么不同。

## 面试可能会问

**问：如果一个人工审批环节超时没人处理，你会怎么设计"超时后的默认行为"，怎么保证这个默认行为不会被误配置成危险的选项？**

答题思路：先明确业务原则——超时后的默认决定，永远只能偏向更安全/更保守的一侧（拒绝，而不是批准），这是不能靠"文档提醒"或"代码审查里人工盯着"来保证的，因为人会犯错、会复制粘贴出错、会遗忘。然后给出具体的实现思路：把"生成超时默认决定"收敛成唯一一个函数入口，并且这个函数的**参数列表里根本不提供"决定类型"这个参数**，只能返回固定的"拒绝"结果——调用方连误传错误值的机会都没有，这就是"safety by construction"（构造即安全）。如果能进一步提到"这和用try/except、if/else去防范是不同量级的安全保障——前者靠代码结构本身杜绝错误，后者靠人的自觉性和细心"，会显著体现你理解的是这个设计背后的原则，而不只是记住了一个函数名。
