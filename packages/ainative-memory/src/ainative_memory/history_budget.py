"""对话历史的token预算裁剪——防止"历史消息不计入预算"这个真实反面案例。

背景：调研中发现一个真实的生产反面案例——某RAG系统里，检索到的文档片段/
实体/关系都有明确的token预算与截断逻辑，唯独`conversation_history`完全
没有任何token计数或截断机制，随对话轮数增长可以无限膨胀，即使其余部分
的预算算得再精确，加上一段未被计入预算的历史后，最终prompt仍可能超出
模型上下文窗口。

本模块把"对话历史必须纳入统一token预算"作为默认行为而非可选项：
`trim_history_to_budget`永远按预算裁剪，调用方如果真的需要不裁剪，
需要显式传一个很大的`max_tokens`，而不是"忘记裁剪"这种默认状态。
"""

# 见ainative_core/config.py的详细解释：让类型注解只被当作字符串暂存，
# 不需要在写代码这一刻真的存在。
from __future__ import annotations

# copy是Python标准库自带的、专门处理"拷贝"的模块。这里用到的是
# `copy.deepcopy`——见下面`trim_history_to_budget`函数末尾第一次真正
# 使用的地方详细解释"深拷贝"是什么、为什么这里必须用它。
import copy

# Any表示"任意类型"——这里用在消息字典的value类型上，因为一条对话
# 消息除了`content`这个文本字段，可能还携带别的任意字段（比如角色、
# 时间戳等），本模块不关心那些字段具体是什么类型。
from typing import Any


def _estimate_tokens(text: str) -> int:
    """粗略估算：字符数/4。与`ainative_guardrail.budget_middleware`保持一致的
    经验估算比例（best-effort，不追求精确到真实tokenizer的程度）。"""
    # 前缀下划线表示这是模块内部使用的辅助函数，不打算被外部代码直接
    # 导入调用（约定俗成的命名规则，Python本身不会强制阻止外部导入）。
    # `len(text)`：字符串的长度（有多少个字符）。`// 4`是"整数除法"——
    # 和普通除法`/`不同，`//`会自动把结果向下取整成整数（比如
    # `10 // 4`得到`2`而不是`2.5`），因为"token数量"应该是一个整数，
    # 不应该出现"2.5个token"这种小数结果。用字符数除以4是一个粗略的
    # 经验估算（真实的token计数需要调用具体模型厂商的tokenizer，这里
    # 为了保持简单、不引入额外依赖，用这个近似比例代替）。
    return len(text) // 4


def trim_history_to_budget(
    history: list[dict[str, Any]],
    # `*`：它后面的参数（max_tokens/content_key）必须用"参数名=值"的
    # 方式传入，见checkpoint.py里对这个语法的详细解释。这里这么设计
    # 是为了防止调用方不小心把`max_tokens`和`content_key`的位置传反
    # （两者都可能是意义不明显的裸值，容易搞反顺序）。
    *,
    max_tokens: int,
    content_key: str = "content",
) -> list[dict[str, Any]]:
    """从最近的消息开始保留，直到达到`max_tokens`预算为止（旧消息被丢弃）。

    Args:
        history: 消息列表，每条至少包含`content_key`指定的字段（纯文本）。
        max_tokens: 对话历史允许占用的token预算上限。
        content_key: 消息文本内容所在的字段名。

    Returns:
        从`history`尾部（最近的消息）开始保留、总估算token数不超过`max_tokens`
        的子列表，顺序与原列表一致（时间顺序，不是倒序）——每条消息都是
        深拷贝，不与原始`history`共享任何可变对象。调用方常见的用法是
        "把裁剪结果当成自己的副本，发给模型之前再做一次编辑/脱敏"，如果
        返回的字典和原始`history`是同一个对象，这类"看起来安全的修改"
        会静默改写调用方可能仍然持有、以为完好无损的原始历史记录（比如
        存在checkpoint/会话存储里的那份）。
    """
    # 准备一个空列表，用来收集"决定要保留下来"的消息。
    kept: list[dict[str, Any]] = []
    # 记录"目前为止已经累计用掉了多少token预算"，从0开始累加。
    used = 0
    # `reversed(history)`：把`history`这个列表倒过来遍历，也就是"从
    # 最后一条（最近的消息）开始，往前一直遍历到第一条（最早的消息）"。
    # 之所以要倒着遍历，是因为本函数的策略是"优先保留最近的消息，预算
    # 不够时丢弃最早的消息"——从最近的开始逐条往预算里塞，塞不下了
    # 就停，天然实现了"越新的消息越优先保留"的效果。
    for message in reversed(history):
        # `message.get(content_key, "")`：从这条消息字典里，尝试取出
        # `content_key`（默认是"content"）对应的值；如果这个字段根本
        # 不存在，就用空字符串""兜底，不会因为某条消息缺字段而直接
        # 报错崩溃。`str(...)`再包一层，确保即使这个字段的值不是字符串
        # （比如不小心传了数字），也能安全地当成文本参与后续的长度估算。
        text = str(message.get(content_key, ""))
        # 估算这条消息大概占用多少token（调用上面定义的辅助函数）。
        cost = _estimate_tokens(text)
        # 判断"这条消息还要不要塞进去"：`kept`非空（意味着已经至少保留
        #了一条最近的消息）并且"再加上这条消息的开销会超出预算"时，
        # 就在这里停止（`break`会直接跳出整个for循环，不再继续处理
        # 更早的消息）。这里刻意判断`kept`非空是为了保证"哪怕最新的
        # 这一条消息本身开销就已经超过预算，也至少保留这一条"——避免
        # 因为预算设置得极小，返回一个完全空的历史（那样上下文会完全
        # 丢失最近的对话，比"稍微超一点预算"更糟糕）。
        if kept and used + cost > max_tokens:
            break
        # 决定保留这条消息：加进`kept`列表，并把它的开销累加进已用
        # 预算里。
        kept.append(message)
        used += cost
    # 因为前面是"从最近往最早"倒着遍历、倒着往`kept`里塞的，所以此刻
    # `kept`列表内部的顺序是"最近的消息排在最前面、较早的消息排在
    # 后面"（和原始对话的时间顺序正好相反）。`.reverse()`是列表的
    # 内置方法，会"原地"把列表元素的顺序倒过来（不创建新列表，直接
    # 修改`kept`自身），把顺序恢复成和原始`history`一致的"时间正序"。
    kept.reverse()
    # `copy.deepcopy(kept)`：对`kept`做"深拷贝"——普通的浅拷贝（比如
    # `list(kept)`或`kept[:]`）只会创建一个新的外层列表，但列表里每个
    # 元素（这里是消息字典）仍然是和原始`history`里同一个字典对象；
    # 如果调用方后续对返回结果里的某条消息字典做`result[0]["content"] = "改过的内容"`
    # 这样的原地修改，浅拷贝的情况下会连带改到`history`原始列表里
    # 那条同名的消息（因为它们本质上是同一个字典对象），这是Python里
    # "共享引用"导致的经典陷阱。`deepcopy`会递归地把字典、列表等所有
    # "容器"都重新创建一份全新的、互不共享的副本，保证调用方之后无论
    # 怎么修改返回结果，都不会意外影响到原始的`history`（这份文档
    # 顶部docstring里提到的"调用方常见用法是裁剪后再做一次编辑/脱敏"
    # 这个场景，正是依赖这里的深拷贝才是安全的）。
    return copy.deepcopy(kept)


def estimate_history_tokens(history: list[dict[str, Any]], *, content_key: str = "content") -> int:
    """估算整段历史的token占用，供调用方并入自己的总预算计算。"""
    # 这是一个"生成器表达式"（写在`sum(...)`括号里的
    # `_estimate_tokens(str(m.get(content_key, ""))) for m in history`
    # 这一整段）：对`history`里的每一条消息`m`，重复上面同样的"取字段、
    # 转字符串、估算token"这套逻辑，`sum(...)`把所有消息各自估算出来
    # 的token数依次累加，得到整段历史的估算总token数。这个函数不做
    # 任何裁剪，纯粹是给调用方一个"提前知道这段历史大概要占多少预算"
    # 的只读查询工具，方便调用方把这个数字并入自己更大范围的总预算
    # 计算（比如"历史 + 检索文档 + 系统提示词"三者加起来是否超限）。
    return sum(_estimate_tokens(str(m.get(content_key, ""))) for m in history)
