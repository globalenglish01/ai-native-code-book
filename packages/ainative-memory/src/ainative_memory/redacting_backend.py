"""摘要溢出落盘前的PII脱敏代理（Proxy模式）。

改造自真实项目里验证过、几乎零耦合的设计：`SummarizationMiddleware`把
对话历史溢出（overflow）内容落盘之前，需要先做PII脱敏。本模块提供一个
只拦截真正执行"写入"的方法（`write`/`edit`/`awrite`/`aedit`）、其余方法
透传给底层backend的代理类——不关心底层backend具体是本地文件、S3还是
别的什么，只要求它有这几个方法（鸭子类型）。

**关键架构约束（必须遵守，否则会误伤业务文件读写）**：这个代理只应该
传给"负责摘要溢出持久化"的组件（如`SummarizationMiddleware(backend=...)`），
绝不能传给"模型自身文件读写工具"用的backend（如`FilesystemMiddleware`）——
同一个原始backend实例通常会被两处同时使用，调用方必须持有两个独立引用：
原始backend给文件读写工具，`wrap_summarization_backend()`包过的代理给
摘要持久化组件。

`edit()`方法只脱敏`new_string`，不脱敏`old_string`——因为`old_string`
是从磁盘读回的、可能已经脱敏过的旧内容，必须原样传回才能让底层backend
正确定位替换点；脱敏函数本身是幂等的（对已脱敏文本再脱敏一次无害），
所以`new_string`整体脱敏是安全的。
"""

from __future__ import annotations

# Callable——给"一个函数本身"写类型注解，比如`Callable[[str], str]`表示
# "一个函数，接收一个字符串参数，返回一个字符串"。这里用来描述"脱敏
# 函数长什么样子"：喂给它一段原始文本，还给你一段脱敏后的文本。
from collections.abc import Callable

# Any——表示"任意类型都可以"，用在这里是因为`inner`（被包装的底层backend）
# 可能是任何实现了write/edit等方法的对象，本模块不关心它具体是什么类，
# 也没必要限定；同理，这些方法的返回值具体是什么类型，也交给底层backend
# 自己决定，本代理不关心，只负责"转发调用、替换掉要写入的文本内容"。
from typing import Any


class RedactingBackend:
    """包装任意"文件系统式"backend，拦截写入方法做脱敏，其余方法透传。"""

    def __init__(self, inner: Any, redact_fn: Callable[[str], str]) -> None:
        # `inner`——被包装的原始backend（比如一个本地文件系统实现，或者
        # 一个S3客户端封装），下面所有没被显式拦截的方法调用，最终都会
        # 转发给它。`redact_fn`——脱敏函数，本类完全不知道、也不关心具体
        # 的脱敏规则是什么，只负责在写入前调用它一次。
        self._inner = inner
        self._redact_fn = redact_fn

    def __getattr__(self, name: str) -> Any:
        # `__getattr__`是Python的一个"魔法方法"（特殊命名的内置钩子方法），
        # 只有当"正常访问属性失败"时（比如访问了一个这个类自己没有定义
        # 的方法名/属性名）才会被自动调用。这里的作用是实现"透传"：如果
        # 调用方访问的不是下面显式定义的`write`/`edit`/`awrite`/`aedit`
        # 这几个方法（比如调用了`inner`独有的其他方法，或读取某个属性），
        # 就直接转发给`self._inner`去处理——`getattr(self._inner, name)`
        # 意思是"去`self._inner`身上取名字叫`name`的这个属性/方法"。这就是
        # docstring里说的"其余方法透传给底层backend"的具体实现方式，是
        # 一种"代理模式（Proxy Pattern）"的经典写法：对外表现得像是同一
        # 个对象，实际内部把大部分工作转手交给被包装的对象去做。
        return getattr(self._inner, name)

    def write(self, file_path: str, content: str, *args: Any, **kwargs: Any) -> Any:
        # `*args: Any, **kwargs: Any`——这两个是"可变参数"写法：`*args`
        # 收集调用时额外传入的、没有名字的位置参数，打包成一个元组；
        # `**kwargs`收集额外传入的"参数名=值"形式的关键字参数，打包成
        # 一个字典。这里这么写是因为不确定`inner.write`除了`file_path`/
        # `content`之外还接受哪些额外参数（不同的底层backend实现可能不
        # 完全一样），用`*args, **kwargs`可以把"调用方给这个代理传的任何
        # 额外参数"原样再转发给`inner.write`，代理本身不需要逐一知道每个
        # 参数叫什么名字。
        # 核心逻辑就这一行："真正落盘的内容`content`"先经过`self._redact_fn`
        # 脱敏处理，再把脱敏后的结果转发给底层backend真正执行写入。
        return self._inner.write(file_path, self._redact_fn(content), *args, **kwargs)

    def edit(self, file_path: str, old_string: str, new_string: str, *args: Any, **kwargs: Any) -> Any:
        # 注意这里只对`new_string`（将要写入的新内容）调用脱敏函数，
        # `old_string`（用于在文件里定位"要替换的旧内容"）原样传递、不
        # 脱敏——具体原因见本文件顶部docstring的详细解释：`old_string`是
        # 从磁盘读回来的内容，脱敏它反而会导致底层backend在原文件里找不
        # 到匹配的旧内容，替换操作直接失败。
        return self._inner.edit(file_path, old_string, self._redact_fn(new_string), *args, **kwargs)

    async def awrite(self, file_path: str, content: str, *args: Any, **kwargs: Any) -> Any:
        # 这是`write`的"异步版本"（方法名前缀`a`是"async"的常见简写
        # 约定）——逻辑完全一样，只是需要用`await`等待底层backend的异步
        # 写入操作真正完成。
        return await self._inner.awrite(file_path, self._redact_fn(content), *args, **kwargs)

    async def aedit(self, file_path: str, old_string: str, new_string: str, *args: Any, **kwargs: Any) -> Any:
        # `edit`的异步版本，脱敏规则和上面`edit()`完全一致。
        return await self._inner.aedit(file_path, old_string, self._redact_fn(new_string), *args, **kwargs)


def wrap_summarization_backend(backend: Any, redact_fn: Callable[[str], str]) -> RedactingBackend:
    """把`backend`包装成一个只用于摘要持久化路径的脱敏代理。

    Args:
        backend: 原始的文件系统式backend（提供write/edit/awrite/aedit方法）。
        redact_fn: 脱敏函数，比如`ainative_security.pii_redaction.redact_pii_text`——
            本模块不内置任何具体脱敏规则，规则完全由调用方注入。
    """
    # 这是一个很薄的"工厂函数"——本身不做任何额外工作，只是把两个参数
    # 原样转发给`RedactingBackend`的构造函数。存在的意义是让调用方代码
    # 读起来更贴近"我想要一个脱敏代理"这个意图（`wrap_summarization_backend(...)`），
    # 而不用直接写`RedactingBackend(...)`这种更底层的类名。
    return RedactingBackend(backend, redact_fn)
