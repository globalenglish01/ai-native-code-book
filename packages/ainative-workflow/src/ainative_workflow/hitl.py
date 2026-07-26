"""Human-in-the-loop 中断检测 + 超时安全默认值。

改造自真实项目里验证过的设计：Agent运行命中需要人工审批的节点时不抛
异常，而是在返回结果里带上一个标记键（原版是LangGraph约定的
`__interrupt__`），调用方必须显式检查这个键，否则会把"等待人工审批"
误判为"执行完成"。

提取时的改动：`interrupt_key`做成可配置参数（默认`"__interrupt__"`
以兼容LangGraph约定），不假设调用方一定用LangGraph——任何返回dict里
带一个"中断标记键"的编排框架都能复用这套检测逻辑。
"""

# Human-in-the-loop（人在回路中，常缩写HITL）指的是：让AI/自动化流程在
# 执行到某些关键、有风险的步骤时暂停下来，把决定权交还给真人，等人明确
# 表态之后再继续——而不是完全放任程序自己做完所有决定。这个文件负责的
# 是"怎么从一次执行结果里，识别出'这次其实是被中断了、在等人处理'，
# 而不是误以为'已经顺利跑完了'"这件事。
from __future__ import annotations

import logging

# Any——来自typing模块的"任意类型"标记，用在类型注解里表示"这里可以是
# 任何类型的值，我们不做限制/不关心具体是什么类型"。
from typing import Any

logger = logging.getLogger(__name__)

# 中断标记键的默认名字。之所以默认用`"__interrupt__"`这个前后带双下划线
# 的古怪名字，是为了兼容LangGraph（一个业界常见的agent编排框架）的约定——
# LangGraph在agent执行结果字典里，就是用这个key来存放"当前发生了中断，
# 需要人工介入"的信息。这里把它做成一个可配置的默认值（而不是写死在函数
# 内部），是因为不是所有调用方都用LangGraph，如果别的框架用了不同的
# key名字，调用方可以在调用时自己传一个不同的`interrupt_key`。
DEFAULT_INTERRUPT_KEY = "__interrupt__"


def extract_interrupt(
    result: dict[str, Any], *, interrupt_key: str = DEFAULT_INTERRUPT_KEY
) -> dict[str, Any] | None:
    """从agent执行结果中提取中断payload（未命中中断则返回`None`）。

    已知限制：中断标记理论上可以是一个列表（多个并行中断），本函数只返回
    第一个元素。这在"每次运行只会挂载一个会触发中断的中间件/节点"这个
    前提下是安全的——如果检测到多个中断，会记录一条WARNING并仍然只返回
    第一个，其余被静默丢弃。如果你的编排里存在多个独立触发中断的分支，
    需要改用能处理`list[dict]`的调用方式，不要依赖本函数的单值返回。
    """
    # 函数参数里单独的`*`同样表示：它后面的`interrupt_key`必须用
    # "参数名=值"的方式传入，不能只按位置传（详见hitl_policy.py里的
    # 详细解释）。

    # `result.get(interrupt_key)` ——从传入的执行结果字典里，尝试取出
    # "中断标记键"对应的值；如果这个字典里压根没有这个key，`.get(...)`
    # 会返回`None`（而不是像`result[interrupt_key]`那样直接报错崩溃）。
    # 之所以要用`.get()`而不是方括号取值，是因为"这次执行根本没有发生
    # 中断"是完全正常、经常发生的情况，不应该被当作错误处理。
    interrupts = result.get(interrupt_key)
    # `if not interrupts:` ——这里的`interrupts`可能是`None`（压根没有
    # 这个key）、空列表`[]`（有这个key但列表是空的）等"假值"，`not`会把
    # 这些情况统一判断为True。也就是说：只要没有真正拿到"至少一个"中断
    # 记录，就直接返回None，告诉调用方"这次运行没有被中断，正常结束"。
    if not interrupts:
        return None
    # 走到这里，说明`interrupts`是一个非空的列表——按docstring里说的，
    # 理论上一次执行可能同时触发多个并行的中断点，但本函数的设计只处理
    # "只有一个中断"这种最常见的场景。
    if len(interrupts) > 1:
        # 检测到有多于1个中断时，不会直接报错，而是记一条WARNING日志
        # 提醒"这里发生了本函数没有完全处理的情况，其余的被丢弃了"，
        # 让问题至少被看见、留痕，而不是悄无声息地丢数据却没人知道。
        logger.warning(
            "[HITL] detected %d parallel interrupts, only the first is handled, %d ignored",
            len(interrupts), len(interrupts) - 1,
        )
    # 只取列表里的第一个中断记录进行处理。
    first = interrupts[0]
    # `first.value if hasattr(first, "value") else first`——这是Python的
    # "三元表达式"（一行内写完的if/else），意思是：如果`first`这个对象
    # 身上有一个叫`value`的属性（`hasattr`用来检查"这个对象有没有某个
    # 属性/方法"），就取出`first.value`；否则直接把`first`本身原样返回。
    # 为什么要这样判断：LangGraph真实返回的中断对象有时是一个包了一层
    # 的`Interrupt`对象（真正的数据在`.value`属性里），有时又直接就是
    # 一个普通字典——这行代码同时兼容这两种情况，不管拿到的是"包装过的
    # 对象"还是"裸字典"，都能正确取出真正想要的数据。
    return first.value if hasattr(first, "value") else first


def count_pending_decisions(interrupt_payload: dict[str, Any] | None, *, requests_key: str = "action_requests") -> int:
    """中断payload里等待人工提交决定的数量。"""
    # 如果传进来的payload本身是None（比如调用方先调用了extract_interrupt，
    # 结果是None，又直接传进了这个函数），直接返回0——表示"没有任何
    # 待处理的人工决定"，这也是最合理的兜底值。
    if not interrupt_payload:
        return 0
    # `interrupt_payload.get(requests_key, [])`——从中断payload字典里，
    # 取出"这次中断具体请求了哪些操作等待批准"这份列表；如果这个key
    # 不存在，就用一个空列表`[]`兜底（而不是报错），然后用`len(...)`
    # 数一下这份列表里有多少项，就是"目前有多少个操作在等人做决定"。
    return len(interrupt_payload.get(requests_key, []))
