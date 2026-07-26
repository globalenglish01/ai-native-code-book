"""混合检索融合——基于排名的RRF（Reciprocal Rank Fusion），不直接比较原始分数。

改造自checklist A类"混合搜索融合设计子类"真实设计要点（对`anything-chat-rag`
真实代码`bm25.py`的`rrf_fuse`设计提炼）：融合多种不同标准算出来的排序结果
（比如BM25关键词匹配 + 向量相似度检索）时，必须用基于"排名"而不是直接
比较"原始分数"的融合方式——不同算法的分数量纲完全不可比（BM25分数可能
是0-20的任意正数，余弦相似度是0-1），直接相加/加权平均会产生失真的
排序结果。RRF只关心"这个文档在每一路检索结果里排第几名"，天然规避了
量纲不可比的问题。
"""

# 什么是"混合检索"（hybrid search）？很多检索系统会同时跑两种（或更多）
# 完全不同原理的搜索算法——比如BM25（一种基于关键词精确匹配、计算
# 词频统计的经典算法）和"向量检索"（把查询和文档都转成一串数字向量，
# 用向量之间的相似度衡量语义上的接近程度，能找到"意思相近但用词不同"
# 的内容）。两种算法各有所长，混合检索就是把两路结果合并成一份最终
# 排序——这个文件专门负责"怎么合并"（术语叫"融合"，fusion）这件事。

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RRF_K = 60
"""RRF公式里的平滑常数k——业界惯用默认值60（出自RRF原始论文的实验结果），
避免排名靠前的文档因为分母过小而权重被过度放大。"""
# ↑ 后面`rrf_fuse`的公式是`1/(k+rank)`——如果没有k（相当于k=0），排名
#   第1的文档分数是1/1=1.0，排名第2的是1/2=0.5，差距被放得很大；加上
#   k=60之后变成1/61和1/62，差距被"拉平"了很多，减少"仅仅因为在某一路
#   检索里恰好排第1"就获得压倒性优势的情况。


class InvalidRankError(ValueError):
    """`RankedResult.rank`不满足"从1开始"这个约定时抛出。"""
    # ↑ 自定义异常类——`class X(ValueError)`表示"新建一个更具体的异常
    #   类型，同时它仍然算作ValueError的一种"，调用方可以按需选择用
    #   更精确的`except InvalidRankError`还是更宽泛的`except ValueError`
    #   去捕获它。


@dataclass(frozen=True)
class RankedResult:
    """一路检索结果里的一条命中——只需要文档标识和它在这一路检索结果里的排名。"""
    # ↑ `@dataclass`自动生成构造函数等样板代码；`frozen=True`让实例一旦
    #   创建就不能再修改字段——一条"检索排名结果"代表某次检索的既成
    #   事实，不应该在流转过程中被谁悄悄篡改排名。

    doc_id: str
    # ↑ 这条结果对应的文档标识（比如文档ID或文件路径）。
    rank: int
    """从1开始的排名（1表示这一路检索里最相关的结果）。"""

    def __post_init__(self) -> None:
        # `__post_init__`是`@dataclass`提供的一个特殊钩子方法：dataclass
        # 自动生成的构造函数会在把所有字段赋值完毕之后，自动调用这个
        # 方法（如果你定义了它的话）。它的典型用途就是"在对象刚刚构造
        # 完成、字段都已经就位的那一刻，做一次校验"——这里就是检查
        # `rank`这个字段的值是否合法，不合法就立刻拒绝构造。
        if self.rank < 1:
            # 真实bug背景：如果上游代码用`enumerate()`枚举排名时忘记
            # 指定从1开始（`enumerate()`默认从0开始），就会产出
            # `rank=0`的结果传进来。这里如果不校验，会一路传导到下面
            # `rrf_fuse`的`1/(k+rank)`公式——如果k恰好也是0，就是
            # 除以0直接报错崩溃；如果k不是0，则会算出一个不合理的
            # （偏大或偏负）分数，且不会有任何报错提示，是"看起来
            # 正常但结果全错"的典型失败模式。必须在这里、构造对象的
            # 那一刻就直接拒绝，而不是让错误值流入后续的融合计算。
            raise InvalidRankError(
                f"rank must be >= 1 (ranks start from 1), got {self.rank} for doc_id={self.doc_id!r} — "
                f"an off-by-one upstream (e.g. enumerate() starting at 0) would otherwise silently "
                f"cause rrf_fuse's `1/(k+rank)` to divide by zero or produce a negative denominator"
            )


def rrf_fuse(*result_lists: list[RankedResult], k: int = DEFAULT_RRF_K) -> list[tuple[str, float]]:
    """把多路检索结果按RRF公式融合成一份统一排序，返回`(doc_id, rrf_score)`
    按分数降序排列的列表。

    RRF公式：一个文档的最终分数 = 它在每一路结果里`1/(k+rank)`的总和——
    只在某一路结果里出现的文档，那一路没出现就不贡献分数（不是当作
    最差排名参与计算，避免"完全没被某个检索器召回"和"被检索到但排名
    很靠后"这两种不同情况被混淆处理）。
    """
    # 参数列表里的`*result_lists`——这是Python的"可变参数"写法：调用方
    # 可以传入任意数量的检索结果列表，比如`rrf_fuse(bm25_results,
    # vector_results)`或者`rrf_fuse(a, b, c)`都合法，函数内部会把它们
    # 统一收集成一个元组`result_lists = (bm25_results, vector_results)`，
    # 不需要提前规定死"只能融合两路"。
    scores: dict[str, float] = {}
    # ↑ 用来累计每个文档最终RRF分数的字典，key是`doc_id`，value是累计
    #   出来的分数。
    for results in result_lists:
        # 依次遍历每一路检索结果（比如先遍历BM25那一路，再遍历向量
        # 检索那一路）。
        for result in results:
            # 再遍历这一路里的每一条具体命中结果。
            scores[result.doc_id] = scores.get(result.doc_id, 0.0) + 1.0 / (k + result.rank)
            # ↑ `dict.get(key, default)`——尝试取出这个文档目前累计到
            #   的分数，如果这个文档是第一次出现（之前任何一路都没
            #   累计过），就用0.0作为初始值，避免`KeyError`。然后加上
            #   这一路里这条结果贡献的分数`1/(k+rank)`——排名越靠前
            #   （`rank`越小），这个值越大，贡献的分数就越高。这样
            #   一个文档如果在多路结果里都出现且排名靠前，会自然
            #   累加出更高的总分。
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
    # ↑ `scores.items()`把字典转换成`(doc_id, score)`元组的列表；
    #   `sorted(..., key=lambda item: item[1], ...)`表示"按每个元组的
    #   第二项（也就是分数）排序"——`lambda item: item[1]`是一个简短的
    #   匿名函数，接收一个元组`item`，返回它的第二个元素用作排序依据；
    #   `reverse=True`表示按分数从高到低降序排列（默认sorted是升序）。


def ranked_results_from_scores(doc_scores: dict[str, float]) -> list[RankedResult]:
    """把一份"文档->原始分数"的检索结果，转换成`rrf_fuse`需要的`RankedResult`
    列表——分数降序即排名（从1开始），供调用方在拿到向量检索/关键词检索
    各自的原始打分结果后，统一转换成排名再融合。
    """
    ordered = sorted(doc_scores.items(), key=lambda item: item[1], reverse=True)
    # ↑ 先把"文档->原始分数"字典按分数从高到低排好序，得到一份
    #   `[(doc_id, score), ...]`的有序列表——排在第几位，就对应第几名。
    return [RankedResult(doc_id=doc_id, rank=i + 1) for i, (doc_id, _score) in enumerate(ordered)]
    # ↑ 这是一个"列表推导式"（list comprehension）——用一行代码快速
    #   生成新列表的写法，等价于写一个for循环、每次往空列表append一个
    #   `RankedResult`，但更简洁。`enumerate(ordered)`会在遍历
    #   `ordered`列表的同时，自动给每个元素配上一个从0开始的序号
    #   （`i`），所以`for i, (doc_id, _score) in enumerate(ordered)`
    #   同时拆解出了序号`i`和元组里的`doc_id`/`_score`两部分（元组里
    #   下划线开头的`_score`表示"这个值本身我们不需要用到，只是占位
    #   接收"）。`rank=i + 1`把从0开始的序号换算成本模块要求的
    #   "从1开始"的排名，这正是`RankedResult.__post_init__`里校验会
    #   通过的合法取值。
