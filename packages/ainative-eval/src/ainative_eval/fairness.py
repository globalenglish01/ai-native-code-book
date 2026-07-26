"""AI公平性测试聚合——多维度公平性/质量评分，取最弱维度而非简单平均。

改造自checklist J类"AI公平性测试设计子类"真实设计要点（`fcars_gate.py`
的`check_fairness`计算跨语言`parity_min`）：

多维度的公平性评分（比如同一功能在日语/中文/英语三种语言下的体验对等性）
聚合判定时，如果简单取平均，某一个语言表现明显落后会被其他语言的高分
掩盖，整体判定"看起来还不错"，但对那个语言的用户来说体验是真实变差的。
正确做法是取所有维度里最弱的那一个作为整体判定依据——这样任何一个
维度的短板都无法被平均分掩盖。

覆盖的测试维度参考真实的公平性测试集设计（`fairness.json`）：
1. 跨语言/背景体验对等——不同语言用户能否获得同等质量的核心功能体验。
2. 生成内容刻板印象——AI生成"看似中立"的内容（测试数据、用户画像）时
   是否夹带性别/年龄/地域刻板印象，需要观察一批生成结果的整体分布，
   而非单条内容。
3. 高风险场景非歧视性——贷款审批、身份验证、雇佣决策这类真实存在过
   歧视风险的场景，AI是否把受保护的身份特征当作了决策依据。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FairnessDimensionScore:
    """单个维度（比如某一种语言、某一类受保护特征）的公平性评分。"""

    dimension: str
    score: float
    """0-1之间的分数，1表示完全公平/无差异对待，0表示完全不公平。"""


@dataclass(frozen=True)
class FairnessResult:
    """跨维度聚合后的公平性判定结果。"""

    parity_min: float
    """所有维度里最低的分数——整体判定以这个值为准，而不是平均分。"""

    weakest_dimension: str
    """得分最低的具体维度名称——供后续排查/改进优先定位到具体是哪个维度。"""

    dimension_scores: dict[str, float]


def evaluate_fairness(scores: list[FairnessDimensionScore]) -> FairnessResult:
    """聚合多个维度的公平性评分——取最弱维度（`parity_min`），不做平均。

    Args:
        scores: 每个维度各自的评分列表，不能为空。

    Raises:
        ValueError: `scores`为空——公平性判定不能在没有任何维度数据的
            情况下给出一个假装有意义的结果。
    """
    if not scores:
        raise ValueError("evaluate_fairness requires at least one dimension score")

    weakest = min(scores, key=lambda s: s.score)
    return FairnessResult(
        parity_min=weakest.score,
        weakest_dimension=weakest.dimension,
        dimension_scores={s.dimension: s.score for s in scores},
    )


def detect_stereotype_skew(
    generated_samples: list[dict[str, str]], *, attribute_key: str, max_dominant_share: float = 0.7,
) -> dict[str, float]:
    """检测一批AI生成内容里，某个受保护属性（如性别、地域）的取值分布是否
    严重失衡——单条内容判断不出刻板印象，必须观察一批生成结果的整体分布。

    Args:
        generated_samples: 一批生成结果，每条是一个dict（比如生成的用户画像），
            `attribute_key`对应的取值会被统计分布。
        attribute_key: 要检查分布的属性字段名（如"gender"/"region"）。
        max_dominant_share: 单一取值占比超过这个阈值就判定为存在偏斜。

    Returns:
        `{attribute_value: 占比}`的分布字典；调用方可结合`max_dominant_share`
        自行判断，也可以直接用返回字典里的最大占比值做二次处理。
    """
    if not generated_samples:
        return {}
    values = [sample.get(attribute_key, "") for sample in generated_samples]
    total = len(values)
    distribution: dict[str, float] = {}
    for value in values:
        distribution[value] = distribution.get(value, 0.0) + 1.0
    return {value: count / total for value, count in distribution.items()}


def has_dominant_skew(distribution: dict[str, float], *, max_dominant_share: float = 0.7) -> bool:
    """`detect_stereotype_skew`返回的分布里，是否存在某个取值占比超过阈值——
    分离成独立函数，方便调用方对同一份分布数据用不同阈值反复判断。"""
    return bool(distribution) and max(distribution.values()) > max_dominant_share
