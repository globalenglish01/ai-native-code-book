# 第18章 —— FCARS门禁

代码位置：`packages/ainative-eval/src/ainative_eval/gate.py`（状态机与数据结构本身定义在`packages/ainative-core/src/ainative_core/protocols.py`）

## 一个真实场景：谁来决定"能不能上线"

想象你所在的团队正在给一个AI客服系统做发版前检查。你们列了一份清单："有没有正确接入护栏中间件""有没有做PII脱敏""安全扫描有没有异常""公平性测试跑过没有"……每一项检查都由不同的人、不同的脚本、甚至不同的LLM调用完成。有的检查输出的是"通过/失败"这样的布尔值，有的输出的是"0.83分"这样的LLM评分，有的因为服务超时干脆什么结果都没给出来。

现在问题来了：**这些五花八门的检查结果，最终要汇总成一句话——"这次能不能部署"**。谁来做这个汇总？如果每加一项新检查，就要在发布脚本里手写一段新的`if`分支去解释"这项检查什么状态算通过"，这份判定逻辑会随着检查项增多变得越来越难维护，而且每个人理解"通过"的标准可能还不一样。

`gate.py`要解决的就是这件事：**把"检查什么"和"怎么判定要不要拦截"彻底分开**。检查项本身（护栏有没有接好、PII有没有脱敏）可以来自任何地方、用任何逻辑实现，但只要它们都按同一个格式（statement）汇报结果，`Gate`这个类就能用统一的规则判定最终能不能放行。模块的docstring里说得很直白，这套设计改造自一个真实生产项目里"验证过的部署前门控"——原版是针对Fairness（公平性）/Compliance（合规）/Accountability（问责）/Reliability（可靠性）/Safety（安全）五个固定维度做检查，这也是"FCARS"这个名字的由来。本书这一版把"具体检查什么"完全外置，`Gate`类本身只保留状态机判定的骨架。

## 统一结果schema：不管检查的是什么，形状都一样

先看这几个类型的定义（在`ainative_core/protocols.py`里）：

```python
GateStatus = Literal["GREEN", "YELLOW", "RED", "UNKNOWN", "SKIPPED", "NEEDS_REVIEW"]

@dataclass
class GateResult:
    """单一维度的检查结果——统一schema，不管这个维度具体检查的是什么内容。"""

    dimension: str
    gating: bool
    status: GateStatus
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
```

不管一项检查背后是一次简单的正则匹配、一次数据库查询，还是一次LLM打分，最终都要被压缩成同一种形状：一个维度名字（`dimension`）、这个维度是否参与拦截判定（`gating`）、一个六选一的状态（`status`）、一段人话说明（`detail`）、附带证据（`evidence`）、以及可选的具体分数（`score`）。这就是这一章要学的第一个设计原则：**当你的系统需要汇总来源迥异的多个检查结果时，先设计一个统一的结果schema，比先写判定逻辑更重要**——有了统一形状，后面无论是渲染报告、做统一判定，还是给这份结果接入新的展示界面，都不需要针对"这项检查到底是什么类型"写特殊处理。

值得留意的是`GateResult`用的是`@dataclass`而不是`@dataclass(frozen=True)`。协议文件里专门注释解释了原因：治理Gate的判定逻辑有时需要先创建一个初步结果、再根据后续复核调整某个字段（比如`score`），不像"一次性数据快照"那样天然适合冻结。这是一个值得记住的设计判断：**是否要`frozen=True`，取决于这个对象在生命周期里是否还需要被同一处代码继续修改，而不是无脑地"能冻结就冻结"**。

再看`GateCheck`——它是"注册进Gate的一条具体检查项"：

```python
@dataclass(frozen=True)
class GateCheck:
    """一条注册进 Gate 的具体检查项。"""

    name: str
    gating: bool
    check_fn: Callable[[], GateResult]
```

`check_fn`是一个不接收任何参数、返回`GateResult`的函数。这里有意思的地方在于它的docstring："所有需要的上下文应该在构造`GateCheck`时通过闭包/partial提前绑定好"。也就是说，如果某项检查需要访问"这次要发布的版本号"或者"当前的配置对象"这类外部数据，不应该把它们当作`check_fn`的参数传进来（因为按约定`check_fn`不接收参数），而应该用闭包提前把这些数据"包"进这个函数里。这是函数式编程里一个常见的技巧：**把"检查逻辑需要哪些上下文"这件事，在注册阶段就固定下来，让运行阶段的调用方（`Gate.run()`）不需要关心每个检查具体依赖什么外部信息，只需要统一地"调用一次、拿到结果"**。

## 六态状态机：为什么不是简单的"通过/不通过"

```python
GREEN: GateStatus = "GREEN"
YELLOW: GateStatus = "YELLOW"
RED: GateStatus = "RED"
UNKNOWN: GateStatus = "UNKNOWN"
SKIPPED: GateStatus = "SKIPPED"
NEEDS_REVIEW: GateStatus = "NEEDS_REVIEW"
```

注意这几个常量的类型标注都是`GateStatus`——这个类型在`protocols.py`里被定义成`Literal["GREEN", "YELLOW", ...]`，也就是"只能是这六个字符串中的一个"的字面量类型。把它们定义成模块级常量而不是每次直接写字符串字面量，好处在源码注释里说得很清楚：**写`status == GREEN`比直接写`status == "GREEN"`更不容易因为手滑拼错字符串（比如写成`"Green"`）而产生难以察觉的bug**——拼错常量名，IDE和类型检查工具能立刻报错；拼错字符串字面量，往往要运行到那一行代码才会发现"这个分支怎么永远进不去"。这是一个非常值得在日常写代码时就养成的习惯：**任何"只能取有限几个固定值"的字符串，都应该定义成常量或者枚举，而不是散落在各处的裸字符串**。

先看分数怎么映射到状态：

```python
def status_from_score(score: float | None, green_min: float, yellow_min: float) -> GateStatus:
    if score is None:
        return UNKNOWN
    if score >= green_min:
        return GREEN
    if score >= yellow_min:
        return YELLOW
    return RED
```

这里第一个判断`score is None`值得多说一句：**分数是`None`，意味着这项检查根本没能算出一个具体分数（可能检查过程本身出错、也可能这项检查压根不适用），应该归类为`UNKNOWN`，而不是当作0分处理**。0分和"没有分数"是两件完全不同的事——0分是"确定很差"，`UNKNOWN`是"不知道好不好"。如果把两者混为一谈，会导致"检查脚本本身挂掉了"和"检查真的发现了严重问题"在报告里看起来一模一样，排查问题时会走很多弯路。

剩下三个真正的等级——GREEN/YELLOW/RED，用的是"依次从最高标准往下检查"的写法：达到`green_min`直接返回GREEN，函数在这里就结束；没达到但达到了`yellow_min`，返回YELLOW；两个门槛都没达到，落到最后一档RED。这是一种常见的、可读性很好的多档位判断写法，比嵌套一堆`if/else`更清晰。

那`SKIPPED`和`NEEDS_REVIEW`在哪里产生？`SKIPPED`不是`status_from_score`会返回的值——它是留给具体`check_fn`自行决定的一种状态，比如"这项检查在当前环境下不适用（本地开发环境不需要检查生产密钥轮换），主动跳过"。而`NEEDS_REVIEW`则是本章下一节要讲的"边界复核"机制专门产生的状态，我们放到第19章细讲。

## 拦截判定：`gating`标志决定谁真正说了算

```python
def decide(dimensions: list[GateResult]) -> GateDecision:
    blockers: list[str] = []
    for d in dimensions:
        if not d.gating:
            continue
        if d.status == RED:
            blockers.append(f"{d.dimension} = RED: {d.detail}")
        elif d.status == UNKNOWN:
            blockers.append(f"{d.dimension} = UNKNOWN (could not be certified): {d.detail}")
        elif d.status == NEEDS_REVIEW:
            blockers.append(
                f"{d.dimension} = NEEDS_REVIEW (score at boundary, recheck inconsistent, "
                f"manual review recommended before deploying): {d.detail}"
            )
    return GateDecision(passed=not blockers, dimensions=dimensions, blockers=blockers)
```

第一行`if not d.gating: continue`是这套机制里最关键的设计：**`gating=False`的维度，不管状态是GREEN还是RED，都不会被计入拦截原因**。这意味着你可以给一项"还在实验阶段、不完全信任"的新检查项注册进Gate，让它照常跑、照常产生结果、照常展示在报告里，但暂时不让它拥有"能阻止部署"的权力，等观察一段时间确认它足够可靠了，再把`gating`改成`True`。这是一个很实用的灰度策略：**新引入的检查项，可以先以"仅记录不拦截"的身份运行一段时间，积累信任后再赋予真正的否决权**，而不是一上线就直接拥有生杀大权，一旦这项检查本身有bug就会误伤所有正常的发布。

再往下是`if/elif/elif`——RED、UNKNOWN、NEEDS_REVIEW三种状态互斥（一个维度同一时刻只可能处于其中一种），所以用`if/elif`而不是三个独立的`if`。特意留意`NEEDS_REVIEW`分支的措辞："score at boundary, recheck inconsistent, manual review recommended"——源码注释解释了这么写的原因：让阅读拦截报告的人**清楚区分"这项检查已经确定不合格"（RED/UNKNOWN）和"这项检查结果不稳定、需要人来做最终判断"（NEEDS_REVIEW）这两种性质不同的情况**。虽然三者在`decide()`里的处理方式完全一样（都会被计入blockers、都会导致拦截），但措辞上的区分能极大地帮助后续排查——同一份报告里出现三十条`RED`和一条`NEEDS_REVIEW`，人在决定要不要手动放行时的心理预期是完全不同的。

最后一行`GateDecision(passed=not blockers, ...)`——`not blockers`利用了Python里"空列表被当作False"的规则：一条拦截原因都没收集到，`blockers`是空列表，`not []`就是`True`，判定通过。这一行代码同时完成了"判断是否放行"和"把所有信息打包成`GateDecision`返回"两件事。

## `Gate.run()`：单个检查崩溃，不能拖垮整个门禁

```python
def run(self) -> GateDecision:
    dimensions: list[GateResult] = []
    for check in self._checks:
        try:
            result = check.check_fn()
        except Exception as exc:
            result = GateResult(
                dimension=check.name,
                gating=check.gating,
                status=UNKNOWN,
                detail=f"check raised an exception: {exc}",
            )
        dimensions.append(result)
    return decide(dimensions)
```

这里的`try/except Exception`捕获得非常宽泛——几乎任何异常都会被接住。源码注释特意说明了这不是疏忽：这个文件在项目的`pyproject.toml`里被显式加了一条ruff规则例外（放行"except过于宽泛"这条检查），因为**"防止单个检查项的意外崩溃拖垮整个Gate"这个设计意图，本来就需要这么宽泛的捕获**。想象一下如果不这么做：三十项检查里，第五项因为某个边界情况抛出了一个没处理过的`KeyError`，如果这个异常没被捕获，整个`run()`方法会直接崩溃退出，导致后面二十五项本来能正常跑完的检查一个结果都拿不到——这比"第五项检查标记成UNKNOWN、其余检查照常跑完"要糟糕得多。这是一个值得记住的工程原则：**当你在编排一组彼此独立的检查/任务时，一个任务的意外失败应该被隔离在它自己的范围内，不应该级联影响到其他本可以正常完成的任务**——用`try/except`把每个任务包起来，把异常转换成一个"失败状态"而不是让它继续往上抛，是实现这种隔离最直接的手段。

## 本章小结

- 汇总来源迥异的多个检查结果时，先设计统一的结果schema（`GateResult`：dimension/gating/status/detail/evidence/score），比先写判定逻辑更重要——有了统一形状，后续的判定、展示、报告都不需要关心某项检查具体是什么类型。
- `score is None`应该归类为`UNKNOWN`而不是当作0分——"检查失败/不适用"和"检查确认结果很差"是完全不同的两件事，混为一谈会让排查问题时误判故障原因。
- `gating=False`是一种实用的灰度策略：新检查项可以先"仅记录不拦截"运行一段时间，积累信任后再赋予真正的否决权，避免一上线就因为自身的bug误伤所有正常发布。
- `NEEDS_REVIEW`虽然在拦截判定上和`RED`/`UNKNOWN`效果一样，但措辞上刻意区分"确认不合格"和"结果不稳定需要人工确认"，这种区分对阅读报告、决定要不要手动放行的人非常重要。
- 编排一组彼此独立的检查项时，单个检查的意外异常应该被隔离捕获、转换成失败状态，不能让它级联崩溃掉整个门禁流程。

## 动手做

```python
from ainative_eval import Gate, GateCheck, GateResult, GREEN, RED

def check_guardrail_wired() -> GateResult:
    return GateResult(dimension="guardrail_wired", gating=True, status=GREEN, detail="护栏中间件已正确接入")

def check_pii_redaction() -> GateResult:
    return GateResult(dimension="pii_redaction", gating=True, status=RED, detail="发现未脱敏的邮箱地址")

def check_experimental_new_rule() -> GateResult:
    # 故意抛异常，模拟一个还不成熟的检查项写崩了
    raise RuntimeError("这个新检查还没写完")

gate = Gate([
    GateCheck(name="guardrail_wired", gating=True, check_fn=check_guardrail_wired),
    GateCheck(name="pii_redaction", gating=True, check_fn=check_pii_redaction),
    GateCheck(name="experimental_new_rule", gating=False, check_fn=check_experimental_new_rule),
])

decision = gate.run()
print("passed:", decision.passed)
for blocker in decision.blockers:
    print(" -", blocker)
```

试着把`experimental_new_rule`的`gating`改成`True`，观察它是不是也出现在了`blockers`里——即使它的"失败"只是因为代码本身抛了异常，而不是真的发现了问题。

## 面试可能会问

**问：如果要给一个系统设计"发布前检查门禁"，你会怎么处理"某一项检查本身跑挂了"和"某一项检查确认发现了问题"这两种不同的情况？**

答题思路：先指出这是两种性质不同的失败，不应该被混为一谈——检查跑挂了（异常、超时）应该归类为`UNKNOWN`（不知道好不好），而不是直接当成"确认不合格"或者更糟的"默认放行"。可以举`status_from_score`里`score is None`归类为`UNKNOWN`而不是0分的例子，以及`Gate.run()`里用`try/except`把单个检查的异常转换成`UNKNOWN`状态、避免拖垮整个门禁流程的例子。如果能进一步提到"哪些检查项拥有真正的否决权（`gating=True`）应该是可配置的，新检查项可以先只记录不拦截"，会显示出你不仅想到了"怎么判断"，还想到了"怎么安全地上线一项新检查"这个更接近真实团队协作场景的问题。
