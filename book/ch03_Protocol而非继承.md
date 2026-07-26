# 第3章 —— Protocol而非继承

代码位置：`packages/ainative-core/src/ainative_core/protocols.py`

## 先讲一个反例

假设你要设计"记忆存储"这个功能，最直觉的写法可能是这样：

```python
class MemoryStore:
    def append(self, entry): ...
    def load_recent(self, owner_id): ...

class PostgresMemoryStore(MemoryStore):
    def append(self, entry):
        # 真的连接Postgres写入
        ...
```

这么写有什么问题？表面上看没问题——`PostgresMemoryStore`继承了`MemoryStore`，实现了它的方法。但如果团队里有人写了一个`RedisMemoryStore`，却忘了写`class RedisMemoryStore(MemoryStore):`，只是手动实现了同样的方法名，那么想用"这个对象是不是一个`MemoryStore`"这个问题去做类型检查时，就会得到`False`——即使它的行为和`MemoryStore`一模一样。

更麻烦的是：如果`MemoryStore`基类里未来加了一个新方法，所有继承它的子类都要跟着改，即使这个子类根本用不上新方法。继承会把"父类"和"子类"死死绑在一起。

## Protocol 解决的是什么问题

Python的`Protocol`（来自标准库`typing`模块）提供了一种不同的思路：**只描述"需要具备哪些方法"，不要求任何显式的继承关系**。这叫"结构化类型"（structural typing），民间常用一句谚语形容它："如果它走起来像鸭子、叫起来像鸭子，那就把它当鸭子"——不需要血缘关系证明，只需要行为符合。

来看`ainative-core`里真实的写法（[protocols.py:218-230](../packages/ainative-core/src/ainative_core/protocols.py)）：

```python
@runtime_checkable
class MemoryStore(Protocol):
    async def append(self, entry: MemoryEntry) -> None:
        """追加一条新记忆。"""
        ...

    async def load_recent(
        self, owner_id: str, *, before_sequence: int | None = None, max_items: int = 10
    ) -> list[MemoryEntry]:
        ...

    async def delete_by_owner(self, owner_id: str) -> int:
        ...
```

注意每个方法体里只有一个`...`（Ellipsis）——这不是"没写完"，而是Protocol的标准写法：**这里只是一份"合同"，不含任何真正的实现**。任何一个类，只要真的实现了这三个同名、同参数签名的方法，Python就会认为它"满足了`MemoryStore`这个协议"，完全不需要写`class MyStore(MemoryStore):`去显式声明继承关系。

## 为什么这个设计对"可插拔框架"特别重要

`ainative-memory`包里的`InMemoryMemoryStore`（我们在第六部分会详细讲）就是这样一个"满足`MemoryStore`协议，但完全没有继承它"的类。这意味着：

1. **真实项目接入时，完全不需要依赖`ainative-core`的具体类**——只需要照着`protocols.py`里的方法签名，自己实现一个连接Postgres/Redis的版本，框架的其他部分（比如`ainative-workflow`如果要用到记忆）就能直接把这个自定义类传进去用，因为它"长得像"一个`MemoryStore`。
2. **框架本身不会意外产生循环依赖**——`ainative-core`定义协议，其他包只依赖协议本身（一份纯数据/纯方法签名的定义），不依赖任何具体实现，天然避免了"包A依赖包B的具体实现类，包B又反过来需要包A的东西"这种纠缠。

## `@runtime_checkable`：什么时候需要它

`Protocol`默认只在你写代码、用类型检查工具（比如mypy）的时候起作用——运行时程序并不会真的去检查"这个对象符不符合协议"。加上`@runtime_checkable`装饰器后，你可以在程序运行时用`isinstance(obj, MemoryStore)`来判断一个对象是否满足这份协议：

```python
from ainative_core.protocols import MemoryStore

def use_store(store: MemoryStore) -> None:
    print(isinstance(store, MemoryStore))  # True，只要store有对应的方法
```

`ainative-core`里几乎所有Protocol都加了这个装饰器，因为框架希望调用方能在运行时做一个"防御性检查"（比如"你传进来的这个对象，看起来不太像一个MemoryStore，是不是传错了？"）。

## `@dataclass`：另一个反复出现的写法

`protocols.py`里除了Protocol，还大量使用了`@dataclass`——这是Python标准库另一个"减少样板代码"的工具。比如：

```python
@dataclass(frozen=True)
class MemoryEntry:
    owner_id: str
    sequence: int
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

你不需要自己写`__init__`方法，`@dataclass`会根据字段声明自动生成。`frozen=True`表示"创建之后不能再修改字段"——这份文件里绝大多数dataclass都选择了`frozen=True`，因为它们代表的是"一次性传递的数据快照"（比如一次调用的结果），理应创建之后就不再改变。

这里有个容易踩的坑，值得单独说一句：`metadata: dict[str, Any] = field(default_factory=dict)`为什么不能直接写成`metadata: dict = {}`？因为Python的可变默认参数（列表、字典）只会在**类定义的时候**创建一次，之后所有实例如果都不显式传这个参数，会共享同一个字典对象——这是Python里一个非常经典、也非常容易忽略的陷阱。`default_factory=dict`的意思是"每次创建新实例时，调用一次`dict()`生成一个全新的空字典"，从根本上避免了这个陷阱。

## 本章小结

- `Protocol`定义的是"接口约定"，不是可以直接实例化的类；任何类只要实现了同名同参方法，就自动"满足"这份协议，不需要显式继承。
- `@runtime_checkable`让协议支持运行时`isinstance`检查。
- `@dataclass(frozen=True)`用于"一次性数据快照"，自动生成构造函数；可变类型的默认值必须用`field(default_factory=...)`，不能直接写字面量。
- 这套设计让`ainative-core`能定义一整套接口，而其余12个包和真实项目的具体实现，都只需要"长得像"这些接口，不需要产生真正的代码依赖。

## 动手做

打开Python交互式解释器，验证一下"鸭子类型"到底有多"松"：

```python
from ainative_core.protocols import MemoryStore

class FakeStore:
    async def append(self, entry): pass
    async def load_recent(self, owner_id, *, before_sequence=None, max_items=10): return []
    async def delete_by_owner(self, owner_id): return 0

print(isinstance(FakeStore(), MemoryStore))  # 猜猜是True还是False？
```

你会发现即使`FakeStore`完全没有提到`MemoryStore`这个名字，`isinstance`依然返回`True`——这就是结构化类型的威力（也是需要警惕的地方：拼写完全正确、逻辑却是空的假实现，也会"骗过"这个检查）。

## 面试可能会问

**问：Python里的`Protocol`和抽象基类（`abc.ABC`）有什么区别，你会怎么选？**

答题思路：抽象基类要求显式继承（`class Foo(ABC):`），编译期/导入期就能确定"这个类到底有没有继承它"；`Protocol`是结构化类型，不要求继承关系，只要方法签名匹配就算数，更适合"我只想约束接口形状，不想强迫使用者依赖我这个包"的场景——这正是一个多包框架里，被依赖的一方（`ainative-core`）想要达到的效果：让依赖它的包，只需要"形状对得上"，而不必真的import它的具体类。可以直接引用这个框架"12个包只依赖`ainative-core`的Protocol定义，不依赖具体实现"这个真实设计作为例子。
