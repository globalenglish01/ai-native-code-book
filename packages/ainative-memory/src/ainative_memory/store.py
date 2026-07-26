"""`ainative_core.protocols.MemoryStore`的内存版默认实现。

改造自真实项目里"跨章节书籍记忆"的滑动窗口裁剪设计：记忆的存储/裁剪
与记忆内容的生成（调用LLM提取摘要）完全解耦——本模块只负责"存/取/
按owner删除"，"提取什么、怎么提取"交给调用方（通常搭配
`ainative_prompt.judge`或调用方自己的LLM调用逻辑）。
"""

# 让类型注解可以延迟解析（写法上可以用`X | None`这种"新式"写法而不用担心
# 旧版本Python报错），详见ainative_core里的详细解释，这里不再重复。
from __future__ import annotations

# copy——`copy.deepcopy(x)`把x连同它内部所有嵌套的list/dict完整复制一份，
# 复制出来的新对象和原对象完全独立，改动一个不会影响另一个。详见下面
# 类docstring里"真实bug背景"的解释。
import copy

# dataclasses标准库模块——这里用到的`dataclasses.replace(obj, 字段=新值)`，
# 是专门给"frozen（冻结）dataclass"设计的一个工具函数：因为frozen的实例
# 不能直接用`obj.字段 = 新值`去修改，`replace()`会基于原实例，创建一个
# "除了指定字段用新值，其余字段都保持原样"的全新实例返回，原实例本身
# 不受影响。
import dataclasses

from ainative_core.protocols import MemoryEntry


class InMemoryMemoryStore:
    """`MemoryStore`的内存版实现——用dict存储，按owner_id分组。

    `MemoryEntry`本身是frozen dataclass，但它的`metadata`字段是普通可变
    dict——"frozen"只阻止字段被重新赋值，不阻止字段内容被原地修改。
    `append()`/`load_recent()`都对`metadata`做深拷贝，而不是直接存储/
    返回调用方传入的原始对象：调用方常见的用法是复用同一个可变dict作为
    "元数据模板"分别构造多条`MemoryEntry`，如果内部存的是引用，后续继续
    修改这个模板会静默篡改"已经存进去"的历史记忆条目。
    """

    def __init__(self) -> None:
        # `dict[str, list[MemoryEntry]]`——key是owner_id（比如某个用户/
        # 某次会话的标识），value是这个owner名下所有记忆条目组成的列表。
        # 按owner分组存放，方便后面按owner_id查询/删除时只处理相关的那
        # 一小部分数据，不用扫描全部记忆。
        self._entries: dict[str, list[MemoryEntry]] = {}

    async def append(self, entry: MemoryEntry) -> None:
        # `async def`——这是一个"异步方法"，调用它需要写`await store.append(...)`。
        # 虽然这个内存版实现本身没有任何真正需要等待的操作（不像数据库
        # 版实现需要等网络I/O），但因为`MemoryStore`这个Protocol的所有
        # 实现都要遵循同样的方法签名，所以这里也统一写成async，这样以后
        # 换成真正的数据库实现时，调用方代码不需要任何改动。

        # `dict.setdefault(key, 默认值)`——如果这个key已经存在，直接返回
        # 已有的value；如果key不存在，就先存入"key: 默认值"这一对，再
        # 返回这个默认值。效果是："如果这个owner_id是第一次出现，就给它
        # 建一个空列表；如果已经出现过，就直接拿到它已有的列表，继续往
        # 里面追加"。
        bucket = self._entries.setdefault(entry.owner_id, [])
        # `dataclasses.replace(entry, metadata=copy.deepcopy(entry.metadata))`——
        # 基于传入的entry创建一份新实例，只是把`metadata`字段换成它自己
        # 的深拷贝，其余字段（owner_id/sequence/content等）原样保留。
        # 这样存进`bucket`里的是一份完全独立的记忆条目，之后调用方即使
        # 拿着原始的`entry.metadata`继续修改，也不会影响这里已经存好的
        # 数据（详见类docstring"真实bug背景"的完整解释）。
        bucket.append(dataclasses.replace(entry, metadata=copy.deepcopy(entry.metadata)))

    async def load_recent(
        self, owner_id: str, *, before_sequence: int | None = None, max_items: int = 10
    ) -> list[MemoryEntry]:
        # `self._entries.get(owner_id, [])`——安全地取出这个owner名下的
        # 记忆列表，压根没有这个owner_id时返回空列表（不会报错崩溃）。
        bucket = self._entries.get(owner_id, [])
        # 这是一个"条件表达式"（三元表达式）：如果调用方传了
        # `before_sequence`（想要"只看这个序号之前的记忆"，常用于分页/
        # 翻看更早的历史），就用列表推导式筛出`sequence`小于它的条目；
        # 否则（没传，默认是None）就用`list(bucket)`拷贝一份全部条目
        # （这里用`list(...)`包一层，是为了避免下面的`.sort()`直接在
        # 原始`bucket`上原地排序，误改了内部真正存储的列表顺序）。
        candidates = (
            [e for e in bucket if e.sequence < before_sequence]
            if before_sequence is not None
            else list(bucket)
        )
        # `.sort(key=..., reverse=True)`——按每条记忆的`sequence`（序号，
        # 数字越大代表越新）从大到小排序，也就是"最新的记忆排在最前面"。
        # `key=lambda e: e.sequence`——`lambda`是Python里"写一个没有名字
        # 的简单函数"的语法，这里的意思是"排序时，用每个元素e的
        # `e.sequence`这个值来比较大小"，而不是直接比较`MemoryEntry`
        # 对象本身（对象之间默认不知道该怎么比大小）。
        candidates.sort(key=lambda e: e.sequence, reverse=True)
        # `candidates[:max_items]`——切片写法，取排序后的列表最前面
        # `max_items`条（因为已经按"最新在前"排好序，这就是"最近的N条
        # 记忆"）。再对每一条做一次`dataclasses.replace(..., metadata=深拷贝)`，
        # 原因和`append()`里一样：防止调用方拿到返回值后修改了某条记忆
        # 的`metadata`，意外污染这个类内部真正存储的数据。
        return [dataclasses.replace(e, metadata=copy.deepcopy(e.metadata)) for e in candidates[:max_items]]

    async def delete_by_owner(self, owner_id: str) -> int:
        # `dict.pop(key, 默认值)`——把这个key对应的value从字典里取出并
        # 删除，key不存在时返回给定的默认值（这里是空列表`[]`），不会
        # 报错。效果就是"删除这个owner名下的全部记忆，并且拿到被删掉的
        # 那些记忆"。
        bucket = self._entries.pop(owner_id, [])
        # 返回被删除的记忆条数，方便调用方确认/记日志（比如"这次清理删
        # 除了37条历史记忆"）。
        return len(bucket)
