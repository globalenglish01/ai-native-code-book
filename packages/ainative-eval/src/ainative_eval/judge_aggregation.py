"""独立多次评判结果的聚合：中位数分数 + 分歧度不确定性标记。

这是从真实项目里"用同一份prompt独立调用judge模型N次，取中位数、用极差
衡量分歧度"这个设计中提炼出的纯统计聚合层——不关心judge调用本身怎么做
（不同模块可能用不同方式发起judge调用，比如`ainative_prompt.judge`就有
自己的一套调用+解析逻辑），只负责"给定一组已经解析好的分数，怎么聚合成
一个最终判定"。

`ainative_prompt.judge.judge_response`内部就是这个逻辑的一个具体应用
（针对"评判某个响应是否满足criteria"这个场景）；`ainative_eval`把这层
聚合抽出来，方便其他不经过`judge_response`调用路径的场景（比如已经有
一组来自不同来源的分数，只是想复用同一套"取中位数+判定分歧度"规则）
直接复用，而不用重新实现一遍。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

DEFAULT_HIGH_UNCERTAINTY_THRESHOLD = 0.4


@dataclass(frozen=True)
class AggregatedJudgment:
    """一组独立评分聚合后的结果。"""

    score: float
    """中位数分数。"""

    score_range: float
    """最高分与最低分之差——分数分散程度的代理指标（不是统计学方差）。"""

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
    if not scores:
        return None
    median_score = statistics.median(scores)
    score_range = (max(scores) - min(scores)) if len(scores) > 1 else 0.0
    return AggregatedJudgment(
        score=median_score,
        score_range=score_range,
        high_uncertainty=score_range > high_uncertainty_threshold,
        sample_size=len(scores),
    )
