"""重排序分数聚合——缺失分数的默认值语义必须诚实，禁用/失败时一致回退。

改造自checklist A类"Reranking重排序设计子类"真实发现的反面案例（对
`anything-chat-rag`真实代码`aggregate_chunk_scores`的逻辑颠倒问题
提炼，本模块直接实现修复后的正确版本）：

真实bug背景：原版重排序没能给某个片段打分时（比如那一批打分请求本身
失败），默认值给了1.0（满分）——导致"评估环节本身出问题、不知道是否
相关"的片段反而必然通过"最低分数过滤"这一关，而真正被认真评估过、
给出低分的片段却会被过滤掉，是一处逻辑完全颠倒的真实缺陷。

正确设计原则：
1. **缺失分数不能默认成满分**——本模块默认给缺失分数最低值（0.0），
   语义上准确对应"这份内容没有被验证过相关性，不应该被优先展示"，
   而不是碰巧混进"最相关"的结果里。
2. **禁用/未配置/调用失败三种情况必须处理一致**——统一退回到原始
   检索顺序，而不是三种情况各自产生不同的、不可预期的行为。
"""

# 什么是"重排序"（reranking）？检索阶段（比如BM25关键词匹配、向量相似度
# 检索）通常追求"速度快、能从海量文档里快速筛出一批候选"，但打分不够
# 精细；重排序则是拿到这一小批候选之后，用一个更精细（但更慢，跑不动
# 全量文档）的模型，对这批候选重新精确打分、重新排序，取长补短。这个
# 文件负责处理重排序打分之后的"聚合"（同一文档多个片段怎么汇总成一个
# 分数）和"降级兜底"（重排序不可用时怎么办）逻辑。

from __future__ import annotations

# math模块是Python标准库自带的数学函数集合，这里用到`math.isnan`（判断
# 一个浮点数是不是"NaN"，即"不是一个数字"这种特殊值）和`math.isinf`
# （判断是不是正/负无穷大），详见下面`ScoredChunk.__post_init__`处的
# 详细解释。
import math

# Callable——给"函数类型本身"写类型注解的工具，见下面`apply_rerank_if_enabled`
# 的`rerank_fn`参数定义处。
from collections.abc import Callable

# dataclass——自动生成构造函数等样板代码的装饰器。
from dataclasses import dataclass

DEFAULT_MISSING_SCORE = 0.0
"""重排序未能给某个片段打分时的默认分数——刻意选择最低值而非满分，
对应"未验证相关性的内容不应该被当作高相关内容对待"这一诚实语义。"""


class InvalidRerankScoreError(ValueError):
    """`rerank_score`是NaN或无穷大时抛出——这两种值会让`aggregate_chunk_scores`
    的"取最高分"比较逻辑永久失效（真实bug背景见下方`ScoredChunk`的
    `__post_init__`）。"""
    # ↑ 自定义异常类，继承自Python内置的`ValueError`——表示"这是一个
    #   参数取值不合法"类型的问题，调用方可以选择用更精确的
    #   `except InvalidRerankScoreError`或更宽泛的`except ValueError`
    #   捕获它。


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: str
    # ↑ 这个片段自己的编号。
    doc_id: str
    # ↑ 这个片段所属的文档标识——同一份文档可能被切成多个片段，重排序
    #   通常是对片段打分，但最终经常需要汇总回"文档"这个粒度展示。
    rerank_score: float | None
    """`None`表示这个片段没有被重排序模型打分（比如那一批请求失败）——
    与"确实打分了、但分数是0.0"是两种不同的情况，聚合时都会被当作
    `DEFAULT_MISSING_SCORE`处理，但保留`None`这个区分是为了让调用方
    需要时能单独统计"重排序覆盖率"。"""
    # ↑ `float | None`表示这个字段要么是一个浮点数，要么是`None`——
    #   `None`在这里承载着明确的业务含义："完全没有被打分"，和"打分
    #   打出来正好是0.0（比如模型认为这段内容毫不相关）"是两种完全
    #   不同的情况，虽然它们在下面的聚合逻辑里最终都会被当作
    #   `DEFAULT_MISSING_SCORE`处理，但保留这个区分能让需要的调用方
    #   （比如做数据质量监控的代码）单独统计"到底有多少片段其实压根
    #   没被重排序覆盖到"。

    def __post_init__(self) -> None:
        # `__post_init__`是`@dataclass`提供的钩子方法：dataclass自动
        # 生成的构造函数会在所有字段都赋值完毕之后，自动调用这个方法
        # （如果定义了的话）——这里用它在对象刚构造完成的那一刻，就
        # 校验`rerank_score`这个字段的值是否合法。
        if self.rerank_score is not None and (math.isnan(self.rerank_score) or math.isinf(self.rerank_score)):
            # `math.isnan(x)`判断x是不是"NaN"（Not a Number，一种特殊的
            # 浮点数值，通常由0/0或无穷减无穷这类未定义运算产生）；
            # `math.isinf(x)`判断x是不是正无穷大或负无穷大。这里先用
            # `self.rerank_score is not None`确认字段确实有值（不是
            # 上面说的"没打分"这种正常情况），再检查这个值本身是不是
            # NaN或无穷——只要满足其一，就认为这是一个不合法的分数。
            # 真实bug背景：`aggregate_chunk_scores`用`score > current_best`
            # 比较取最高分——一旦`current_best`是NaN，Python里"NaN和任何数
            # 比较都是False"，导致这个文档的最高分永久卡在NaN，之后再高的
            # 真实分数都无法覆盖它，且`sorted()`不会报错，只会把这份文档
            # 静默排到一个不确定的位置——这正是本模块想要避免的"看起来正常
            # 但结果全错"的失败模式，必须在源头（构造`ScoredChunk`时）就
            # 拒绝这类不合法的分数，而不是让它进入聚合逻辑之后才出问题。
            raise InvalidRerankScoreError(
                f"rerank_score must be a finite number or None, got {self.rerank_score} "
                f"for chunk_id={self.chunk_id!r}"
            )


def aggregate_chunk_scores(
    chunks: list[ScoredChunk], *, missing_score: float = DEFAULT_MISSING_SCORE,
) -> dict[str, float]:
    """按`doc_id`汇总同一文档下多个片段的重排序分数，取该文档下的最高分
    （比片段更细的重排序结果，最终按文档粒度过滤/展示时的常见汇总策略）。

    未打分的片段（`rerank_score is None`）用`missing_score`兜底参与汇总，
    而不是被`if not scores: continue`这类写法直接跳过——跳过会让整份
    文档完全从汇总结果里消失，等价于"当作从未存在过"，比"当作低分处理"
    更容易造成数据静默丢失（真实项目里这两个问题是同一处bug的一体两面）。
    """
    # 参数列表里单独的`*`要求`missing_score`必须用"参数名=值"方式传入，
    # 避免调用时和`chunks`的位置搞混。
    doc_best_scores: dict[str, float] = {}
    # ↑ 用来收集"每份文档目前为止见过的最高分"的字典，key是`doc_id`，
    #   value是这份文档下所有片段里最高的那个分数。
    for chunk in chunks:
        score = chunk.rerank_score if chunk.rerank_score is not None else missing_score
        # ↑ 条件表达式（三元写法）：如果这个片段确实有打分（不是
        #   `None`），就用它本来的分数；如果没打分，就用`missing_score`
        #   兜底——这正是本模块修复的那处真实逻辑颠倒bug的核心：兜底值
        #   默认是`DEFAULT_MISSING_SCORE`（0.0，最低分），而不是原版
        #   错误地给出的1.0（满分）。
        current_best = doc_best_scores.get(chunk.doc_id)
        # ↑ `dict.get(key)`——取出这份文档目前记录的最高分；如果这份
        #   文档是第一次出现（之前没有任何片段贡献过分数），返回`None`
        #   （而不是抛出`KeyError`）。
        if current_best is None or score > current_best:
            # 这份文档还没有记录过分数（第一次遇到），或者这个片段的
            # 分数比目前记录的最高分还要高——两种情况都需要更新
            # `doc_best_scores`里这份文档对应的分数。
            doc_best_scores[chunk.doc_id] = score
    return doc_best_scores


def apply_rerank_if_enabled(
    doc_ids_in_original_order: list[str],
    *,
    enabled: bool,
    rerank_fn: Callable[[list[str]], dict[str, float]] | None,
) -> list[str]:
    """重排序被禁用、未配置、或调用过程本身失败，三种情况统一退回到
    `doc_ids_in_original_order`（原始检索顺序），而不是产生三种互相
    不一致的行为。
    """
    if not enabled or rerank_fn is None:
        # 情况一：调用方明确关闭了重排序功能（`enabled=False`），或者
        # 压根没有配置具体的重排序函数（`rerank_fn is None`）——两种
        # 都视为"不使用重排序"，直接原样返回原始检索顺序的一份拷贝。
        return list(doc_ids_in_original_order)
        # ↑ `list(...)`包一层，返回的是原列表内容的一份新拷贝，而不是
        #   直接把调用方传入的列表对象原样传回——避免调用方后续修改
        #   这个返回值时，意外影响到调用方自己手上原本那份列表（两者
        #   本来是同一个对象的话就会互相影响）。
    try:
        scores = rerank_fn(doc_ids_in_original_order)
    except Exception:
        # 情况二：重排序函数确实被调用了，但过程中抛出了任何异常——
        # 这里故意捕获所有异常类型，而不是只捕获某几种"预期内"的异常。
        # 原因：这个函数的职责是"重排序这个增强功能，不管什么原因失败，
        # 都不能把调用方的主流程一起拖垮"，重排序本质上是锦上添花的
        # 优化步骤，出问题时优雅降级回原始顺序，比让整个检索请求直接
        # 报错崩溃更符合这个函数的设计目的。项目根目录的代码检查规则
        # 里，专门为这一行配置了一条按文件路径生效的例外规则，允许
        # 这里保留这种刻意的、比默认建议更宽泛的异常捕获写法，不会
        # 被代码检查工具持续提示要求收窄。
        return list(doc_ids_in_original_order)
    return sorted(
        doc_ids_in_original_order,
        key=lambda doc_id: scores.get(doc_id, DEFAULT_MISSING_SCORE),
        reverse=True,
    )
    # ↑ 情况三（正常路径）：重排序函数成功返回了一份"文档->分数"字典
    #   `scores`——用它给原始顺序里的每个`doc_id`重新排序。
    #   `key=lambda doc_id: scores.get(doc_id, DEFAULT_MISSING_SCORE)`：
    #   对某个具体的`doc_id`，去`scores`字典里查它的分数；如果这个
    #   `doc_id`压根不在`scores`里（重排序函数可能只对部分文档打了
    #   分），用`DEFAULT_MISSING_SCORE`（最低分）兜底，而不是让程序
    #   因为找不到key而报错——这样"部分文档没有被重排序覆盖"这种
    #   情况，也能得到和上面`aggregate_chunk_scores`一致的、诚实的
    #   处理方式（没打分的排到后面，而不是报错或意外排到前面）。
    #   `reverse=True`让分数从高到低排列，最相关的文档排在最前面。
