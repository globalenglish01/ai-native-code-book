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

# dataclass见ainative_core/config.py里的详细解释：自动生成类的构造函数
# 等样板代码的装饰器。
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
        # `raise ValueError(...)`：主动抛出一个"数值/参数不合法"类型的
        # 异常，中断函数继续执行——这里表达的意思是"没有任何维度数据
        # 时，宁可让调用方明确看到报错，也不要悄悄返回一个看似正常、
        # 实际毫无意义的结果（比如硬编造一个分数）"。
        raise ValueError("evaluate_fairness requires at least one dimension score")

    # `min(scores, key=lambda s: s.score)`：`min()`是Python内置函数，
    # 默认对一组数字直接比大小取最小的那个；但这里`scores`列表里装的
    # 不是数字，而是一个个`FairnessDimensionScore`对象，Python不知道
    # 该按对象的哪个字段比较大小，所以要传入`key=`参数——它是一个函数，
    # 告诉`min()`"对每个元素s，请按`s.score`这个值来比较大小"。
    # `lambda s: s.score`是一个"匿名小函数"的写法：不用`def`单独定义
    # 一个有名字的函数，而是直接内联写"输入s，返回s.score"这一行逻辑，
    # 适合这种只用一次、逻辑很简单的场合。
    # 最终`weakest`就是`scores`列表里`.score`字段值最小的那一个对象——
    # 也就是"表现最弱的那个维度"，这正是本模块"取最弱维度而非平均"
    # 这个核心设计决策的具体实现。
    weakest = min(scores, key=lambda s: s.score)
    return FairnessResult(
        parity_min=weakest.score,
        weakest_dimension=weakest.dimension,
        # `{s.dimension: s.score for s in scores}`：这是"字典推导式"——
        # 一种用一行代码从一个列表构造出字典的简洁写法，等价于写一个
        # for循环，每次把`s.dimension`当作key、`s.score`当作value，
        # 一条条塞进一个新字典里。这里把所有维度的原始分数都保留下来，
        # 方便调用方需要时能看到每个维度的具体数字，而不只是最弱的那个。
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
    # 一条生成结果都没有，谈"分布是否失衡"没有意义，直接返回空字典。
    if not generated_samples:
        return {}
    # `[sample.get(attribute_key, "") for sample in generated_samples]`：
    # 这是"列表推导式"——用一行代码从一个列表构造出另一个列表的简洁写法，
    # 等价于写一个for循环，对`generated_samples`里的每一条记录`sample`，
    # 取出它在`attribute_key`这个字段上的取值（读不到就当作空字符串""），
    # 收集成一个新列表`values`。比如`attribute_key="gender"`时，
    # `values`可能是`["male", "female", "male", "male"]`这样的列表。
    values = [sample.get(attribute_key, "") for sample in generated_samples]
    total = len(values)
    distribution: dict[str, float] = {}
    for value in values:
        # `distribution.get(value, 0.0)`：读取这个取值目前已经累计到
        # 的次数，第一次遇到时读不到就当作0.0。`+ 1.0`把它加一次，
        # 再赋值回去——这是"用字典给每种取值计数"的标准写法，效果等同于
        # 分别给"male"、"female"这些不同取值各开一个独立的计数器。
        distribution[value] = distribution.get(value, 0.0) + 1.0
    # `{value: count / total for value, count in distribution.items()}`：
    # 又一个字典推导式——`distribution.items()`会把字典里的每一对
    # key/value（这里是取值和它出现的次数）都取出来，这里把每个取值的
    # 出现次数除以总数`total`，换算成占比（0到1之间的小数），构造出
    # 一个新的"占比分布"字典返回给调用方。
    return {value: count / total for value, count in distribution.items()}


def has_dominant_skew(distribution: dict[str, float], *, max_dominant_share: float = 0.7) -> bool:
    """`detect_stereotype_skew`返回的分布里，是否存在某个取值占比超过阈值——
    分离成独立函数，方便调用方对同一份分布数据用不同阈值反复判断。"""
    # `bool(distribution)`：把distribution转换成布尔值——非空字典是
    # True，空字典是False。`and`要求两边都为True整个表达式才是True，
    # 所以这里先确认"分布字典确实不是空的"，再用`max(distribution.values())`
    # 取出所有占比里最大的那一个（`.values()`是字典的内置方法，返回
    # 字典里所有的value，忽略key），和阈值比较是否超过。这样"空分布"
    # 这种边界情况会直接被判定为False（不存在偏斜），不会因为对空的
    # `.values()`调用`max()`而报错（`max()`对空序列直接调用会抛异常）。
    return bool(distribution) and max(distribution.values()) > max_dominant_share
