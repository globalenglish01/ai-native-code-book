"""Embedding批量请求 + 数量对账 + 超长文本预检截断。

改造自checklist A类"Embedding与向量化处理子类"真实发现的反面案例（对
`anything-chat-rag`真实代码`llm/openai.py`的`openai_embed`设计提炼，
修复其中一处真实bug后重新实现）：

真实bug背景：批量获取向量时，如果供应商返回的向量数量和请求的文字
数量对不上，原版只记一条错误日志、函数隐式返回`None`，调用方（全代码库
几十处调用点）都没有检查返回值，导致这批文档的实体/关系数据静默丢失、
且完全无法被感知。

正确设计原则：
1. **数量对账失败必须是硬错误**，让异常真正传导到能感知和处理它的
   地方，而不是记一条容易被忽略的日志后悄悄返回`None`。
2. **单段文字长度超过Embedding模型处理上限**这件事，必须在真正调用
   API之前就主动截断处理，而不是等供应商那边报错。
"""

# 什么是"embedding"（嵌入向量）？它是把一段文字，转换成一串固定长度的
# 数字（比如1536个浮点数），这串数字在数学空间里的位置能大致代表这段
# 文字的语义——意思相近的文字，转换出来的数字串在空间里的位置也相近。
# 检索系统靠比较这些数字串的相似度，就能找到"意思相关"的内容，而不
# 只是精确的关键词匹配。这个转换过程需要调用专门的embedding模型/API，
# 通常一次可以批量提交多段文字（一次请求，返回多个向量），这个文件
# 就是负责"批量请求"这件事、并且确保过程中不出现数据静默丢失。

from __future__ import annotations

# Awaitable/Callable——用来给"函数类型"本身写类型注解的工具：
# - Callable[[参数类型...], 返回类型] 表示"一个可以被调用的东西（通常
#   就是函数），接收这些类型的参数，返回这个类型的值"。
# - Awaitable[返回类型] 表示"一个可以被`await`的东西"（也就是异步函数
#   调用后返回的那种对象），await完成后得到这个类型的结果。
# 两者组合`Callable[[list[str]], Awaitable[list[list[float]]]]`用在
# 下面`embed_fn`参数上，表示"一个异步函数，接收一批文字列表，返回
# 一批向量列表"。
from collections.abc import Awaitable, Callable

# dataclass——自动生成构造函数等样板代码的装饰器，见文件后面用到的地方。
from dataclasses import dataclass

DEFAULT_MAX_CHARS_PER_TEXT = 32_000
"""单段文字送去embedding之前的字符数上限——不同embedding模型的真实token
上限不同，这是一个保守的默认预检阈值，真实项目应该按具体模型的文档
覆盖这个值。"""
# ↑ `32_000`里的下划线`_`只是Python允许的"数字分组符"，纯粹为了阅读
#   方便（让人一眼看出这是"3万2千"，而不用去数零的个数），对程序运行
#   没有任何影响，等价于直接写`32000`。


class EmbeddingCountMismatchError(RuntimeError):
    """供应商返回的向量数量与请求的文字数量不一致时抛出——不允许静默
    丢弃这批不对账的embedding请求。"""
    # ↑ 自定义异常类，继承自Python内置的`RuntimeError`——表示"这是一个
    #   运行时才能发现的错误"（不是参数一开始就明显不合法，而是要真的
    #   调用了外部API、拿到返回结果之后才能判断出问题）。

    def __init__(self, *, requested: int, received: int) -> None:
        # `__init__`是Python里"构造函数"的固定名字——创建一个类的实例
        # 时（比如`EmbeddingCountMismatchError(requested=3, received=1)`），
        # Python会自动调用这个方法完成初始化。参数列表里的`*`要求
        # `requested`/`received`必须用"参数名=值"方式传入，强制调用处
        # 写清楚"3和1分别代表什么"，避免搞混顺序。
        super().__init__(
            f"embedding count mismatch: requested {requested} texts but received {received} vectors — "
            f"this batch's data would be silently lost if not treated as a hard error"
        )
        # ↑ `super().__init__(...)`——调用父类（`RuntimeError`）自己的
        #   构造函数，把这段说明文字设置成这个异常的"消息"（也就是
        #   `str(异常对象)`或打印这个异常时会显示的内容）。
        self.requested = requested
        # ↑ 把请求的文字数量存成这个异常实例自己的属性，方便调用方
        #   捕获到异常后，通过`exc.requested`读出具体数字做进一步处理
        #   （比如记录到监控指标里），不需要从异常消息字符串里解析。
        self.received = received
        # ↑ 同理，存下实际收到的向量数量。


@dataclass(frozen=True)
class EmbeddingBatchResult:
    vectors: list[list[float]]
    # ↑ 这一批文字对应的向量结果——`list[list[float]]`表示"一个列表，
    #   里面每个元素又是一个由浮点数组成的列表"，也就是"多个向量"，
    #   每个向量本身是一串数字。
    truncated_count: int
    """这一批里有多少段文字在调用API之前被截断过——供调用方决定是否需要
    对这些文档做进一步处理（比如改用支持更长上下文的embedding模型）。"""


async def embed_batch_with_accounting(
    texts: list[str],
    *,
    embed_fn: Callable[[list[str]], Awaitable[list[list[float]]]],
    max_chars_per_text: int = DEFAULT_MAX_CHARS_PER_TEXT,
) -> EmbeddingBatchResult:
    """批量获取向量，对超长文本做预检截断，并强制核对返回的向量数量。

    Args:
        texts: 待向量化的文字列表。
        embed_fn: 实际调用embedding供应商API的函数，接收一批文字、返回
            对应的向量列表——具体供应商由调用方决定，本函数不内置任何
            供应商依赖。
        max_chars_per_text: 单段文字超过这个长度时，在调用API之前就
            主动截断，而不是让供应商那边因为超出上限而报错。

    Raises:
        EmbeddingCountMismatchError: `embed_fn`返回的向量数量与`texts`
            长度不一致——绝不允许把这种情况当作部分成功静默处理。
    """
    # `async def`——定义一个"协程函数"（也叫异步函数）。和普通函数不同，
    # 调用协程函数不会立刻执行完并返回结果，而是需要配合`await`关键字
    # （见下面调用`embed_fn`那一行）"等待"它真正执行完成——这种写法
    # 适合"需要等待网络请求/IO操作"的场景，等待期间程序可以先去做
    # 别的事情，而不是傻等，从而提高整体效率。调用这个函数的地方也
    # 必须写`await embed_batch_with_accounting(...)`。

    truncated_count = 0
    prepared_texts = []
    for text in texts:
        if len(text) > max_chars_per_text:
            # 这段文字长度超过了预设的上限——不等真正调用API报错，
            # 提前主动截断到允许的最大长度，这是本模块要解决的第2条
            # 设计原则："超长文本必须在调用API之前就主动处理"。
            truncated_count += 1
            prepared_texts.append(text[:max_chars_per_text])
            # ↑ `text[:max_chars_per_text]`——Python的"切片"写法，取出
            #   这个字符串从开头到第`max_chars_per_text`个字符为止的
            #   部分，也就是"截断到指定长度"。
        else:
            # 长度没有超限，原样使用，不需要任何改动。
            prepared_texts.append(text)

    if not prepared_texts:
        # 传入的文字列表本身是空的——没有任何内容需要向量化，直接返回
        # 一个"零结果"，不需要真的去调用`embed_fn`（避免对某些API而言
        # "空列表请求"是未定义/报错行为）。
        return EmbeddingBatchResult(vectors=[], truncated_count=0)

    vectors = await embed_fn(prepared_texts)
    # ↑ 真正调用传入的embedding函数，把预处理（截断）过的文字列表交
    #   给它，`await`表示"等待这个异步调用真正完成，拿到最终的向量
    #   列表结果"。

    if len(vectors) != len(prepared_texts):
        # 这正是本模块修复的真实bug所在之处：请求了多少段文字，就必须
        # 拿回同样数量的向量，一一对应——如果数量对不上（供应商那边
        # 出了问题、丢了几条、或者多返回了几条），旧版本只是记一条
        # 日志然后返回None，调用方完全无从感知这批数据已经出问题。
        # 这里改为直接抛出一个明确的硬错误，让问题在第一时间就被发现、
        # 无法被悄悄绕过。
        raise EmbeddingCountMismatchError(requested=len(prepared_texts), received=len(vectors))

    return EmbeddingBatchResult(vectors=vectors, truncated_count=truncated_count)
