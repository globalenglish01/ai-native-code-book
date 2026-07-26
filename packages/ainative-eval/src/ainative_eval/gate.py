"""通用治理门控（FCARS风格）：多维度检查 + 状态机 + 边界复核防噪声。

改造自真实生产项目里验证过的部署前门控设计（原版针对Fairness/Compliance/
Accountability/Reliability/Safety五个固定维度做结构化+LLM评分检查），本版
把"检查什么"完全外置为可注册的`GateCheck`列表（`ainative_core.protocols`），
`Gate`类本身只负责状态机判定逻辑，不内置任何具体检查项。

保留的核心设计：
1. **统一结果schema**：`{dimension, gating, status, detail, evidence}`
   （`ainative_core.protocols.GateResult`），不管检查的是结构化代码扫描
   还是LLM评分，结果形状一致，便于统一渲染报告/统一判定。
2. **`GREEN/YELLOW/RED/UNKNOWN/SKIPPED/NEEDS_REVIEW`六态状态机**：
   `NEEDS_REVIEW`是原版为了应对"LLM评分噪声"引入的关键状态——分数落在
   阈值边界且复核评分不一致时，既不自动放行也不自动拦截，交给人工确认。
3. **边界复核防噪声**（`maybe_recheck_boundary`）：只有分数落在
   `green_min ± boundary_tolerance`容差内才触发一次重新评分（成本只在
   真正需要时付出），两次分差在`recheck_max_diff`内则采信更保守的较低分，
   分差过大则标记`NEEDS_REVIEW`而不是随便采信一次。
4. **拦截判定**（`decide`）：任一`gating=True`的维度为`RED`/`UNKNOWN`/
   `NEEDS_REVIEW`即拦截；`gating=False`（warn-only）维度即便异常也不拦截，
   只作记录。
"""

from __future__ import annotations

# Callable见ainative_core/protocols.py里的详细解释：用来给"一个函数
# 本身"写类型注解。`Callable[[], float | None]`表示"一个不接收任何
# 参数、返回值是float或者None的函数"——这里专门用来描述"重新评分"这个
# 动作本身，把"怎么触发重新评分"这件事抽象成一个可以随意替换的函数，
# 而不是在本文件里写死具体怎么调用LLM。
from collections.abc import Callable

# 从ainative_core包（本仓库更底层的共享协议包）导入这几个已经定义好的
# 类型：GateCheck（一条检查项的描述）、GateDecision（一次完整判定的
# 结果）、GateResult（单一维度的检查结果）、GateStatus（状态的类型
# 别名，只能是六个固定字符串之一）。这几个类型的详细字段定义和注释
# 都写在ainative_core/protocols.py里，这里只是复用。
from ainative_core.protocols import GateCheck, GateDecision, GateResult, GateStatus

# 下面六行是这个状态机的"六种状态"，每一个都是一个普通的字符串常量，
# 但类型标注成`GateStatus`（只能是这六个字符串之一的Literal类型，见
# ainative_core/protocols.py里的详细解释）。把它们定义成模块级常量，
# 好处是后面代码里写`status == GREEN`比直接写`status == "GREEN"`更不
# 容易因为拼错字符串（比如手滑写成"Green"）而产生难以察觉的bug——
# 拼错常量名IDE/类型检查工具能直接报错，拼错字符串字面量则要运行到
# 那一行才会发现"怎么永远进不了这个分支"。
GREEN: GateStatus = "GREEN"
YELLOW: GateStatus = "YELLOW"
RED: GateStatus = "RED"
UNKNOWN: GateStatus = "UNKNOWN"
SKIPPED: GateStatus = "SKIPPED"
NEEDS_REVIEW: GateStatus = "NEEDS_REVIEW"


def status_from_score(score: float | None, green_min: float, yellow_min: float) -> GateStatus:
    """按分数区间映射到状态：``None``→UNKNOWN；``>=green_min``→GREEN；
    ``>=yellow_min``→YELLOW；否则RED。"""
    # `score is None`：`is`是Python里专门用来判断"两个变量是不是同一个
    # 对象"的运算符，和`None`比较时习惯用`is`而不是`==`（这是Python的
    # 编码规范，`None`只有唯一一个实例，用`is`比较更符合语义、也更快）。
    # 分数是None，意味着"这项检查根本没能算出一个具体分数"（比如检查
    # 过程本身出错、或者这项检查压根不适用），归类为UNKNOWN（未知），
    # 而不是当作0分处理——0分和"没有分数"含义完全不同。
    if score is None:
        return UNKNOWN
    # 下面这几个`if`是"依次从最高标准往下检查"的写法：分数达到green_min
    # 门槛，直接返回GREEN，函数在这里就结束了，不会继续往下执行。
    if score >= green_min:
        return GREEN
    # 没达到GREEN门槛，但达到了yellow_min（更低一档的门槛），返回YELLOW。
    if score >= yellow_min:
        return YELLOW
    # 前面两个门槛都没达到，说明分数落在最低一档，返回RED。
    return RED


def maybe_recheck_boundary(
    score: float,
    green_min: float,
    *,
    boundary_tolerance: float,
    recheck_max_diff: float,
    rescore: Callable[[], float | None],
) -> tuple[float, bool, str]:
    """分数落在gating阈值边界内时，复核一次判定稳定性。

    单次评分噪声可能让本该通过的部署卡在阈值边缘被误拦截，或反之。只在真正
    落入边界容差时才调用`rescore()`重新评分（成本只在需要时付出），两次分差
    在`recheck_max_diff`内则采信更保守的较低分作为最终判定依据；分差过大
    则不采信任一次结果，标记为需要人工复核。

    Args:
        score: 首次评分结果。
        green_min: GREEN状态的判定阈值——同时也是边界复核的中心点。
        boundary_tolerance: 分数与`green_min`的差距在此范围内才触发复核。
        recheck_max_diff: 两次评分的差值超过此值时，判定为"复核不一致"。
        rescore: 重新评分的回调，返回`None`表示复核本身失败。

    Returns:
        ``(final_score, needs_review, note)``——``note``为空字符串表示未触发复核。
    """
    # `abs(score - green_min)`：`abs()`是Python内置函数，返回一个数字
    # 的绝对值（去掉正负号后的数值），这里算的是"这次分数离green_min
    # 门槛线有多远"（不管是高于还是低于，只看距离）。如果这个距离已经
    # 超出了容许的容差范围`boundary_tolerance`，说明这次分数离边界够远，
    # 不太可能是"评分噪声"导致的误判，不需要浪费成本重新评一次，直接
    # 采信首次分数，`needs_review`给False，`note`给空字符串（表示"没有
    # 触发复核这回事"）。
    if abs(score - green_min) > boundary_tolerance:
        return score, False, ""

    # 走到这里，说明分数确实落在门槛线附近的容差区间内，触发一次重新
    # 评分——调用传进来的`rescore`函数（不接收参数，返回一个新的分数
    # 或者None）。
    recheck_score = rescore()
    if recheck_score is None:
        # 复核本身失败（比如调用LLM再评一次时出错），没有第二个分数
        # 可以比较，只能退回采信首次评分，同时在note里写明原因，方便
        # 排查日志时知道"这次为什么没有真正完成复核"。
        return score, False, "(boundary recheck failed to produce a score, using first result)"

    # `round(x, 4)`：Python内置函数，把数字四舍五入保留指定位数的小数
    # （这里是4位），避免浮点数计算天然存在的极小误差（比如0.1+0.2在
    # 计算机里可能算出0.30000000000000004这种结果）让展示出来的分差
    # 看起来不整洁。
    diff = round(abs(score - recheck_score), 4)
    if diff > recheck_max_diff:
        # 两次评分差距太大，说明这次判定不稳定（一次觉得达标、一次觉得
        # 不达标），不应该武断地采信任何一次结果，标记`needs_review=True`
        # 交给人工确认。注意这里`score`（首次分数）仍然原样返回，但
        # 这个返回值实际上不会被下游用来做"是否放行"的决定——因为
        # `needs_review=True`会让上层状态机直接进入NEEDS_REVIEW分支
        # （见下面的Gate类使用场景），这个数值只是用来展示/记录。
        return score, True, (
            f"boundary recheck inconsistent: first={score} recheck={recheck_score} "
            f"(diff {diff} > {recheck_max_diff}), needs manual review"
        )
    # 两次分差在可接受范围内，说明结果基本稳定，`min(score, recheck_score)`
    # 取两者中较低（更保守）的那个作为最终判定依据——治理门控这类"宁可
    # 错杀不可放过"的场景，选择偏保守的数值是刻意的设计倾向。
    conservative = min(score, recheck_score)
    return conservative, False, (
        f"boundary recheck consistent: first={score} recheck={recheck_score} "
        f"(diff {diff} <= {recheck_max_diff}), using conservative value={conservative}"
    )


def decide(dimensions: list[GateResult]) -> GateDecision:
    """任一`gating=True`维度为RED/UNKNOWN/NEEDS_REVIEW即拦截。

    `NEEDS_REVIEW`不自动放行——既不采信首次评分也不采信复核评分，需要人工
    确认后再决定是否部署，因此和RED/UNKNOWN一样计入拦截项，但拦截说明会
    明确区分"需人工复核"而非"确认有问题"。`gating=False`（warn-only）维度
    即便异常也不拦截，只作记录。
    """
    # 准备一个空列表，用来收集"最终导致拦截部署的具体原因"——每个元素
    # 是一段人类可读的文字说明，不是布尔值，方便直接展示给使用者看
    # "到底是哪几项检查没通过、具体原因是什么"。
    blockers: list[str] = []
    # `for d in dimensions:`：依次取出每一个维度的检查结果，挨个判断。
    for d in dimensions:
        # `if not d.gating: continue`：`d.gating`是False，说明这个维度
        # 被标记为"仅记录、不参与拦截判定"（比如实验性的新检查项），
        # `continue`是"跳过这次循环剩下的代码，直接进入下一次循环"的
        # 关键字——不管这个维度状态是好是坏，都不会被计入blockers。
        if not d.gating:
            continue
        # 依次判断这个gating维度具体处于哪种"应该拦截"的状态，分别拼出
        # 不同措辞的说明文字，追加进blockers列表。用`if/elif/elif`
        # （只有第一个满足的分支会执行）而不是三个独立的`if`，是因为
        # 这三种状态互斥——一个维度同一时刻只可能处于其中一种状态。
        if d.status == RED:
            blockers.append(f"{d.dimension} = RED: {d.detail}")
        elif d.status == UNKNOWN:
            blockers.append(f"{d.dimension} = UNKNOWN (could not be certified): {d.detail}")
        elif d.status == NEEDS_REVIEW:
            # NEEDS_REVIEW的措辞特意强调"需要人工复核"而不是"确认有
            # 问题"——见本函数docstring里的解释，这是为了让阅读拦截
            # 报告的人清楚区分"这项检查已经确定不合格"和"这项检查结果
            # 不稳定、需要人来做最终判断"这两种性质不同的情况。
            blockers.append(
                f"{d.dimension} = NEEDS_REVIEW (score at boundary, recheck inconsistent, "
                f"manual review recommended before deploying): {d.detail}"
            )
    # `not blockers`：blockers是空列表时为True（见judge_aggregation.py
    # 里"空列表在Python里被当作False"的详细解释）。`passed=not blockers`
    # 的意思是"只要一条拦截原因都没收集到，就判定为通过"，这一行同时
    # 完成了"判断是否放行"和"把判断结果和所有维度、所有拦截原因打包
    # 成一个GateDecision对象返回"两件事。
    return GateDecision(passed=not blockers, dimensions=dimensions, blockers=blockers)


class Gate:
    """按注册的`GateCheck`列表跑一轮检查，返回统一的`GateDecision`。

    用法::

        gate = Gate([
            GateCheck(name="guardrail_wired", gating=True, check_fn=check_guardrail_wired),
            GateCheck(name="pii_redaction", gating=True, check_fn=check_pii_redaction),
        ])
        decision = gate.run()
        if not decision.passed:
            print(decision.blockers)
    """

    def __init__(self, checks: list[GateCheck]) -> None:
        # 把传进来的检查项列表存到`self._checks`——前缀下划线表示这是
        # "内部实现细节"的命名约定（见ainative_core/memory_backends.py
        # 里的详细解释），提示外部代码不应该直接访问/修改这个属性，
        # 而应该通过`run()`方法来使用它。
        self._checks = checks

    def run(self) -> GateDecision:
        # 准备一个空列表，逐个收集每一项检查跑完之后得到的GateResult。
        dimensions: list[GateResult] = []
        for check in self._checks:
            # `try/except`：见ainative_core/model_factory.py里的详细
            # 解释——先尝试执行try代码块，真的抛出异常就转而执行except
            # 代码块，避免整个程序直接崩溃退出。这里的意图是：单独一项
            # 检查函数（`check.check_fn`，调用方自己写的逻辑）本身如果
            # 写崩了、抛了意料之外的异常，不应该导致整个Gate直接崩溃、
            # 让后面所有检查项都跑不完——而是把这一项检查本身标记成
            # UNKNOWN（既然它连正常跑完都做不到，自然也判断不出真实
            # 状态是好是坏），继续往下跑完剩下的检查项。
            try:
                result = check.check_fn()
            except Exception as exc:
                # `except Exception as exc:`：捕获几乎所有类型的异常
                # （`Exception`是Python里绝大多数内置异常的共同父类），
                # `as exc`把这个具体的异常对象记下来，方便后面把它的
                # 内容（`{exc}`，Python会自动调用异常对象的字符串表示）
                # 拼进detail说明里，方便排查具体是哪里出错。
                # 注意：这个except分支之所以被允许捕获这么宽泛的异常
                # 类型，是本文件在项目根目录pyproject.toml里被显式加了
                # 一条ruff规则例外（对gate.py单独放行BLE001这条"except
                # 过于宽泛"的检查），因为这里正是"防止单个检查项的意外
                # 崩溃拖垮整个Gate"这个设计意图所必需的宽泛捕获，不是
                # 疏忽。
                result = GateResult(
                    dimension=check.name,
                    gating=check.gating,
                    status=UNKNOWN,
                    detail=f"check raised an exception: {exc}",
                )
            dimensions.append(result)
        # 所有检查项都跑完之后，把收集到的全部维度结果，一次性交给
        # 上面定义的`decide()`函数，得到最终的GateDecision并返回。
        return decide(dimensions)
