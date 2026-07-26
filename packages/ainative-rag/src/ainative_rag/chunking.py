"""文档分块——重叠比例控制 + 防御性参数校验。

改造自checklist A类"文档分块设计子类"真实设计要点（对`anything-chat-rag`
真实代码`chunking_by_token_size`的设计提炼，不照抄实现，重新设计通用版本）：

1. **相邻片段保留合理重叠**（行业惯例10%-20%区间）——检索到某个片段时，
   如果答案恰好横跨两个片段的边界，重叠部分能让边界附近的上下文不会
   被硬切丢失。
2. **防御性校验重叠量**：真实项目里发现过一个隐蔽bug——如果调用方把
   `overlap_size`设置得比`chunk_size`还大/相近，`range(0, n, step)`的
   `step`会变成0或负数，Python对此静默返回空序列——文档"分块成功"但
   实际零片段产出，这种"看起来正常但结果全错"的失败模式比直接报错
   更难排查。本模块在参数校验阶段就直接拒绝这类不合理组合，而不是
   让它在运行时悄悄产出空列表。
3. **语义边界优先切分**的兜底策略：如果按语义边界（如段落分隔符）切出来
   的某一段仍然超出大小限制，必须有明确的兜底（本模块选择：对超长段
   再做一次机械切分，而不是无声地生成一个超限的"片段"）。
"""

# 什么是"分块"（chunking）？大语言模型/检索系统一次能处理的文本长度是
# 有限的，一份很长的文档（比如几万字的手册）不能整份塞给检索或生成模型，
# 需要先切成一个个较短的小段（chunk），检索时按"段"为单位去匹配、
# 排序，生成回答时再把最相关的几段拼起来当作上下文。这个文件就是专门
# 负责"怎么切"这件事的。

# 这一行是Python的"未来特性"声明：让函数签名里的类型注解（比如
# `-> list[Chunk]`）不需要在代码运行到这里时就已经真正定义好，Python
# 只把它们当字符串暂存，真正用到时（IDE/类型检查工具）才解析。好处是
# 类可以在自己的方法签名里提前引用"未来才出现"的类型名。
from __future__ import annotations

# dataclass是Python标准库提供的一个"装饰器"（写在类前面的@xxx），作用是
# 自动帮你生成这个类的构造函数(__init__)、打印格式(__repr__)等样板代码，
# 你只需要声明"这个类有哪些字段"，不用自己手写重复代码。
from dataclasses import dataclass

DEFAULT_OVERLAP_RATIO = 0.15
"""行业惯例的10%-20%重叠区间取中间值作为默认——不是拍脑袋数字，参考了
真实项目文档字符串里"行业推荐的10-20%区间"这一具体表述。"""
# ↑ 写在函数外面、全大写命名的变量，是Python里"模块级公共常量"的传统
#   写法惯例（语法上并不强制不可修改，全靠这种命名约定让使用者自觉遵守
#   "不要随便改它"）。这里表示"两个相邻片段之间，默认让15%的内容重叠"。


class InvalidChunkingParametersError(ValueError):
    """`overlap_size`相对`chunk_size`的比例不合理时抛出——防止`range()`
    步长退化为0/负数导致静默产出空片段列表。"""
    # ↑ 这是"自定义异常类"——`class X(ValueError)`表示"新建一个专门的
    #   异常类型X，它是ValueError的一种"。这样调用方既可以用更具体的
    #   `except InvalidChunkingParametersError`只捕获这一类问题，也可以
    #   用更宽泛的`except ValueError`统一处理各种参数错误，两种写法都对。


@dataclass(frozen=True)
class Chunk:
    """一个文本片段——保留在原文里的起止位置，方便回溯定位。"""
    # ↑ `@dataclass(frozen=True)`：frozen=True表示"冻结"，这个类的实例
    #   一旦创建出来，字段就不能再被修改（比如`chunk.text = "x"`会直接
    #   报错）。一个切好的片段代表"某次分块结果的一个既成事实"，构造完
    #   之后不应该再被谁悄悄改动，用frozen在语言层面强制保证这一点。

    text: str
    # ↑ 这个片段本身的文字内容。
    start: int
    # ↑ 这个片段在原文中的起始字符位置（第几个字符开始）。
    end: int
    # ↑ 这个片段在原文中的结束字符位置（到第几个字符为止，不含）。
    sequence: int
    # ↑ 这个片段是本次分块结果里的第几段（从0开始编号），方便按顺序
    #   还原/展示切分结果。


def chunk_by_token_estimate(
    text: str, *, chunk_size: int = 1000, overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[Chunk]:
    """按估算token数（字符数//4，与`ainative-memory`保持一致的估算比例）切分文本，
    相邻片段保留`overlap_ratio`比例的重叠内容。

    Args:
        text: 待切分的原文。
        chunk_size: 每个片段的目标token数上限。
        overlap_ratio: 相邻片段的重叠比例（0-1之间，行业惯例10%-20%）。

    Raises:
        InvalidChunkingParametersError: `overlap_ratio`不在`[0, 1)`区间内——
            等于或超过1会让"重叠量"达到甚至超过片段本身大小，导致切分
            步长退化为0或负数。
    """
    # 参数列表里单独的这个`*`，表示它后面的参数（`chunk_size`/`overlap_ratio`）
    # 必须用"参数名=值"的方式传入，比如`chunk_by_token_estimate(text,
    # chunk_size=500)`，不能只按位置写`chunk_by_token_estimate(text, 500)`。
    # 好处：调用时代码自带说明性（一看就知道500是chunk_size还是别的），
    # 也避免以后给函数加新参数时打乱位置对应关系。

    # "token"是大语言模型处理文本时的基本计量单位（大致可以理解成"一个
    # 词或词的一部分"，比真实的"字符数"更贴近模型实际的处理开销，但这里
    # 用一个简化估算：假设平均每4个字符约等于1个token，不去调用真正的
    # 分词器（tokenizer），换取实现简单、不依赖额外库。
    if not (0.0 <= overlap_ratio < 1.0):
        # 校验1：重叠比例必须落在[0, 1)区间——0表示完全不重叠，1或以上
        # 在数学上意味着"重叠掉整段甚至更多"，会导致下面算出的`step`
        # （每次向前推进的字符数）变成0或负数。
        raise InvalidChunkingParametersError(
            f"overlap_ratio must be in [0, 1), got {overlap_ratio} "
            f"(a ratio >= 1 makes the step size <= 0, silently producing zero chunks)"
        )
    if chunk_size <= 0:
        # 校验2：片段大小必须是正数——0或负数的"目标片段大小"没有任何
        # 业务意义，必须在源头拒绝，而不是让后面的计算产出无意义的结果。
        raise InvalidChunkingParametersError(f"chunk_size must be positive, got {chunk_size}")

    chars_per_token = 4
    # ↑ 估算换算比例：约4个字符算1个token（英文平均情况下的粗略经验值）。
    chunk_chars = chunk_size * chars_per_token
    # ↑ 把"目标token数"换算成"目标字符数"，后续所有切分计算都直接用
    #   字符数进行，不再涉及token这个抽象单位。
    overlap_chars = int(chunk_chars * overlap_ratio)
    # ↑ 按比例算出"重叠区域"具体是多少个字符——`int(...)`把浮点数结果
    #   向下取整成整数（字符数不能是小数）。
    step = chunk_chars - overlap_chars
    # ↑ "步长"：每切完一个片段，下一个片段的起点相对上一个片段起点要
    #   往前推进多少个字符。如果整段大小是1000字符、重叠150字符，那么
    #   步长就是850——下一段从"上一段起点+850"处开始，保证两段之间有
    #   150字符是重复覆盖的内容。前面两处校验正是为了保证这个`step`
    #   永远是正数，不会变成0或负数导致`while`循环死循环/不推进。

    if not text:
        # 空字符串直接返回空列表——没有内容可切，不需要走后面的循环逻辑。
        return []

    chunks: list[Chunk] = []
    # ↑ 用来收集切分结果的列表，类型注解`list[Chunk]`表示"一个元素类型
    #   都是Chunk的列表"。
    start = 0
    sequence = 0
    while start < len(text):
        # 只要还没切到文本末尾，就继续切下一段。
        end = min(start + chunk_chars, len(text))
        # ↑ 这一段的结束位置：正常情况下是"起点+目标片段字符数"，但如果
        #   这样算出来会超过文本总长度，就用`min(...)`截断到文本实际末尾，
        #   避免切出一个"超出原文范围"的越界片段。
        chunks.append(Chunk(text=text[start:end], start=start, end=end, sequence=sequence))
        sequence += 1
        if end >= len(text):
            # 这一段已经切到了文本末尾——说明全部内容都覆盖完了，跳出
            # 循环，不再需要继续切更多段（否则会不断产出重复的末尾片段）。
            break
        start += step
        # ↑ 只有在"还没切完"的情况下，才把起点往前推进`step`个字符，
        #   开始下一轮循环，准备切出下一个（与上一个有重叠的）片段。
    return chunks


def chunk_by_semantic_boundary(
    text: str, *, separator: str = "\n\n", chunk_size: int = 1000, overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[Chunk]:
    """优先按语义边界（默认段落分隔符）切分；任何一段仍然超出`chunk_size`时，
    对那一段单独做`chunk_by_token_estimate()`机械二次切分兜底——不允许
    任何一个最终片段悄悄超出大小限制却不被处理。
    """
    # "语义边界"指的是文本本身天然的分隔点（比如段落之间的空行），按这
    # 种边界切分出来的片段，内容通常是完整的一个意思单元，比"每隔固定
    # 字符数硬切一刀"（上面`chunk_by_token_estimate`的做法）更符合人类
    # 阅读习惯、检索时也更容易命中语义完整的内容。
    chars_per_token = 4
    chunk_chars = chunk_size * chars_per_token

    paragraphs = text.split(separator)
    # ↑ `str.split(separator)`：按指定的分隔符（默认是"\n\n"，即空行，
    #   代表段落之间的分隔）把整段文本切成一个字符串列表，每个元素是
    #   一个段落（分隔符本身不会出现在结果里）。
    chunks: list[Chunk] = []
    sequence = 0
    cursor = 0
    # ↑ `cursor`用来追踪"当前处理到原文的第几个字符位置"，因为
    #   `str.split()`本身不会告诉你每个段落在原文里的起止位置，需要
    #   自己一边遍历一边手动累加，才能让最终的`Chunk.start`/`end`
    #   准确对应回原文位置（供调用方回溯定位）。
    for paragraph in paragraphs:
        para_start = cursor
        # ↑ 这个段落在原文里的起始位置——就是遍历到目前为止`cursor`
        #   累加到的位置。
        cursor += len(paragraph) + len(separator)
        # ↑ 提前把`cursor`往前推进"这个段落的长度+分隔符的长度"，为
        #   处理下一个段落做准备（注意：最后一个段落后面其实没有真实
        #   的分隔符，这里会多算一点，但因为不再有下一个段落用到它，
        #   不影响结果的正确性）。
        if not paragraph:
            # 空段落（比如原文里连续多个空行导致split出现空字符串）
            # 直接跳过，不产出对应的片段，也不递增`sequence`编号。
            continue
        if len(paragraph) <= chunk_chars:
            # 这个段落本身没有超出目标片段大小——整段直接作为一个
            # 片段，不需要再进一步切分，保留了最完整的语义单元。
            chunks.append(Chunk(text=paragraph, start=para_start, end=para_start + len(paragraph), sequence=sequence))
            sequence += 1
        else:
            # 这个段落本身就超出了`chunk_size`（比如一大段没有换行的
            # 长文字）——语义边界切分在这里"帮不上忙"，必须有明确的
            # 兜底处理：调用上面的`chunk_by_token_estimate`对这一段
            # 单独做机械二次切分，而不是放任它作为一个超限的"片段"
            # 被悄悄产出（这正是本模块docstring里强调的第3条设计原则）。
            for sub in chunk_by_token_estimate(paragraph, chunk_size=chunk_size, overlap_ratio=overlap_ratio):
                chunks.append(Chunk(
                    text=sub.text, start=para_start + sub.start, end=para_start + sub.end, sequence=sequence,
                ))
                # ↑ 注意`start=para_start + sub.start`：`sub.start`是
                #   相对"这个段落内部"的位置（因为`chunk_by_token_estimate`
                #   并不知道这个段落在原文里从哪开始），需要加上
                #   `para_start`换算回"相对整篇原文"的绝对位置。
                sequence += 1
    return chunks
