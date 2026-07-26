"""独立多次评判结果的聚合：中位数分数 + 分歧度不确定性标记。

这是从真实项目里"用同一份prompt独立调用judge模型N次，取中位数、用极差
衡量分歧度"这个设计中提炼出的纯统计聚合层——不关心judge调用本身怎么做
（不同模块可能用不同方式发起judge调用，比如`ainative_prompt.judge`就有
自己的一套调用+解析逻辑），只负责"给定一组已经解析好的分数，怎么聚合成
一个最终判定"。

`ainative_prompt.judge.judge_response`采用的是同一套"中位数+极差判定
分歧度"规则，但为了不让`ainative-prompt`反过来依赖`ainative-eval`
（两个包都只依赖`ainative-core`，互相之间刻意不产生依赖），
`judge_response`内联了自己的一份实现，不直接调用本模块的
`aggregate_scores`。本模块面向的是"已经有一组来自不同来源的分数，
只是想复用同一套聚合规则"这类不经过`judge_response`调用路径的场景。
"""

from __future__ import annotations

# statistics是Python标准库自带的"统计计算"模块，提供median（中位数）、
# mean（平均数）等常见统计量的现成实现，不需要自己手写排序取中间值的逻辑。
import statistics

# dataclass见ainative_core/config.py里的详细解释：一个装饰器，自动帮你
# 生成类的构造函数等样板代码，只需要声明"这个类有哪些字段"。
from dataclasses import dataclass

# 模块级常量：写在函数/类外面、大写命名的变量，代表"整个文件通用的固定
# 默认值"。这里是"分歧度判定"的默认阈值——超过0.4就认为几次独立评判
# 意见分歧较大。之所以做成常量而不是写死在函数参数默认值里的魔法数字，
# 是方便一眼看出这个数字的含义、也方便别处需要引用同一个值时直接复用。
DEFAULT_HIGH_UNCERTAINTY_THRESHOLD = 0.4


# @dataclass(frozen=True)：自动生成构造函数，且创建后字段不可修改
# （"冻结"）——这个类代表"一次聚合计算已经算完的结果快照"，算完之后
# 本就不应该再被谁悄悄改动，所以选择frozen=True。
@dataclass(frozen=True)
class AggregatedJudgment:
    """一组独立评分聚合后的结果。"""

    score: float
    """中位数分数。"""

    score_range: float
    """最高分与最低分之差——分数分散程度的代理指标（不是统计学方差）。"""
    # ↑ "代理指标"的意思是：极差（最大值减最小值）只是"用来近似表示
    #   分散程度"的一个简单量，不是统计学里更严谨的"方差/标准差"这类
    #   指标——选它是因为计算简单、容易直观理解（"最乐观和最悲观的评分
    #   差了多少分"），不追求统计学上的精确性。

    high_uncertainty: bool
    """`score_range`是否超过`high_uncertainty_threshold`——分散度越大，
    说明这几次独立评判的意见越不一致，这次判分的可信度越低。"""

    sample_size: int
    """参与聚合的有效分数个数。"""


def aggregate_scores(
    scores: list[float],
    *,
    high_uncertainty_threshold: float = DEFAULT_HIGH_UNCERTAINTY_THRESHOLD,
) -> AggregatedJudgment | None:
    """把一组独立评分聚合成中位数分数 + 分歧度标记。

    Args:
        scores: 独立评判产生的分数列表（应已经过上游过滤，只包含成功解析的分数）。
        high_uncertainty_threshold: `score_range`超过此值时标记`high_uncertainty=True`。

    Returns:
        `scores`为空时返回`None`（无法聚合），否则返回`AggregatedJudgment`。
    """
    # `not scores`：空列表在Python里会被当作False（这是Python的一个
    # 通用规则——空列表/空字符串/空字典/数字0都会被当作"假"），所以
    # `not []`的结果是True，`not [1, 2]`的结果是False。这里的意思是
    # "如果一个分数都没有，就没法聚合，直接返回None"。
    if not scores:
        return None
    # `statistics.median(scores)`：计算这一组数字的中位数——把所有数字
    # 从小到大排序后，取正中间那个（如果数字总数是偶数，取中间两个的
    # 平均值）。比起用平均数(mean)，中位数不容易被个别极端评分（比如
    # 某一次评分离谱地过高或过低）拉偏，更能代表"大多数评判者的共识"。
    median_score = statistics.median(scores)
    # `max(scores) - min(scores)`：`max()`/`min()`是Python内置函数，
    # 分别返回一组数字里的最大值/最小值。两者相减就是"极差"——衡量这组
    # 分数最乐观和最悲观的评判差了多少。
    # `if len(scores) > 1 else 0.0`：只有一个分数的时候，谈"分散程度"
    # 没有意义（没有第二个数可以比较），直接给0.0（表示"完全没有分歧，
    # 因为压根只有一次评判"），避免这种边界情况下计算出奇怪的结果。
    score_range = (max(scores) - min(scores)) if len(scores) > 1 else 0.0
    return AggregatedJudgment(
        score=median_score,
        score_range=score_range,
        # 极差超过阈值就标记为"高度不确定"——`>`是普通的"大于"比较，
        # 结果直接就是一个True/False值，可以直接赋给bool类型的字段。
        high_uncertainty=score_range > high_uncertainty_threshold,
        # `len(scores)`：Python内置函数，返回列表里有多少个元素。
        sample_size=len(scores),
    )
