# 第5章 —— 别名bug大家族

## 一个看起来"完全正确"的函数

先看一段代码，猜猜它有没有bug：

```python
def merge_configs(*configs: dict) -> dict:
    merged = {}
    for config in configs:
        merged.update(config)
    return merged
```

这段代码逻辑上完全正确——把多个字典合并成一个，后面的覆盖前面同名的key。语法没有任何问题，单元测试如果只检查"合并后的内容对不对"，也会全部通过。

但如果你这样使用它：

```python
template = {"server": {"env": {"TOKEN": "old-token"}}}
merged = merge_configs(template)

merged["server"]["env"]["TOKEN"] = "new-token"

print(template["server"]["env"]["TOKEN"])  # 猜猜输出什么？
```

答案是`"new-token"`——即使你只修改了`merged`，`template`也被跟着改了！这就是本章要讲的"别名bug"（aliasing bug），也是这个框架在这次加固过程中，**在四个完全不同的包里反复发现的同一类bug**。学会识别它，比记住任何具体API都更有价值。

## 为什么会这样：Python里"变量"到底是什么

Python里的变量不是"一个装着值的盒子"，而更像"贴在某个对象上的一张标签"。当你写`merged.update(config)`时，如果`config`里某个value本身是一个字典（比如`{"env": {"TOKEN": "old-token"}}`），`.update()`只是把这个"内层字典"的**引用**（也就是"内存地址"）复制了一份贴到`merged`上——`merged`和`template`此刻指向的是**同一块内存**里的同一个字典对象，只是通过两个不同的"路径"（`merged["server"]["env"]`和`template["server"]["env"]`）都能访问到它。

这在Python里叫"别名"（aliasing）：两个不同的名字，指向同一个对象。修改其中一个"看起来独立"的路径，会同时影响另一个——因为它们压根不是两份数据，是同一份数据的两个入口。

`.update()`这种只复制"最外层"、内部嵌套结构仍然共享引用的复制方式，叫**浅拷贝**（shallow copy）。`dict(x)`、`x.copy()`、`list(x)`都是浅拷贝，都只能解决"最外层"的独立性问题。

## 正确的修复：深拷贝

要让`merged`和`template`彻底独立，需要用Python标准库`copy`模块提供的`copy.deepcopy()`——**递归地**把一个对象内部所有嵌套的字典、列表都重新创建一份全新的、内存地址不同的副本：

```python
import copy

def merge_configs(*configs: dict) -> dict:
    merged = {}
    for config in configs:
        merged.update(copy.deepcopy(config))
    return merged
```

只改了一行，行为却完全不同——现在无论怎么修改`merged`，`template`都不会受到任何影响。

## 这不是一个假设的例子，这是这个框架里真实发生过的bug

这一章开头那段代码，几乎就是`ainative-mcp`包里`merge_mcp_configs`函数曾经的样子。真实场景是这样的：调用方常见的用法是"构造一次MCP server配置模板、合并进最终配置、再按每次请求需要临时修改某个字段（比如刷新一个认证token）"——如果合并结果和原始输入共享同一份嵌套字典引用，修改"合并后的"配置会**静默污染**调用方仍然持有的原始模板对象，导致下一次复用模板时，意外带着上一次请求遗留的token。这类bug的可怕之处在于：**它不会报错，代码表面上"看起来"只改了一个变量，实际上却悄悄改动了另一个看似无关的变量**，往往要过很久、出现了诡异的数据串扰之后才会被发现。

这次加固过程中，同一类bug在完全不同的四个包里被独立发现并修复：

| 包 | 函数 | 泄露的数据 |
|---|---|---|
| `ainative-mcp` | `merge_mcp_configs` | MCP server配置（可能含认证token） |
| `ainative-a2a` | `InMemoryAgentRegistry`的`register`/`get_capability`/`capabilities_of` | Agent能力的input/output schema |
| `ainative-memory` | `InMemoryMemoryStore`的`append`/`load_recent` | 长期记忆的metadata字段 |
| `ainative-workflow` | `Workflow.run`/`resume` | 工作流执行的共享context |

四处bug的修复方式完全一致：**在数据"进入"内部存储和"离开"内部存储的两个边界点，都做一次`copy.deepcopy()`**。以`ainative-a2a`的`InMemoryAgentRegistry`为例：

```python
def register(self, agent_name: str, capability: AgentCapability) -> None:
    bucket = self._capabilities.setdefault(agent_name, {})
    bucket[capability.name] = copy.deepcopy(capability)   # 写入时深拷贝

def get_capability(self, agent_name: str, capability_name: str) -> AgentCapability | None:
    found = self._capabilities.get(agent_name, {}).get(capability_name)
    return copy.deepcopy(found) if found is not None else None   # 读出时也深拷贝
```

为什么两端都要拷贝？因为漏洞可能发生在**任意一端**：调用方注册能力之后继续修改自己手上那份原始对象（写入端泄露），或者调用方拿到查询结果之后修改了它、以为是"自己的独立副本"（读出端泄露）。只做一端，另一端依然是敞开的。

## `frozen=True`能防住这个bug吗？

一个常见的误解是："这个数据类都已经`@dataclass(frozen=True)`了，还会有这种问题吗？" 答案是：**`frozen=True`只阻止字段本身被重新赋值，不阻止字段内容被原地修改**。

```python
@dataclass(frozen=True)
class AgentCapability:
    name: str
    input_schema: dict = field(default_factory=dict)

cap = AgentCapability(name="x", input_schema={"a": 1})
cap.input_schema = {}          # 报错！frozen不允许重新赋值字段
cap.input_schema["a"] = 999    # 完全合法！这是"修改字段指向的对象内容"，不是"重新赋值字段"
```

第二行之所以合法，是因为它没有改变`cap.input_schema`这个字段"指向"哪个字典对象，只是改变了那个字典对象内部的内容——`frozen`管的是"名字和对象的绑定关系"，管不到"对象自己内部可不可变"。这正是为什么`AgentCapability`即使是frozen dataclass，它的`input_schema`/`output_schema`这两个可变字典字段，依然需要在存取时手动深拷贝的原因。

## 怎么在自己的代码里主动排查这类bug

一个实用的自查清单：

1. 你的函数是否接收或返回了一个**嵌套的**可变对象（字典里套字典、列表里套字典）？
2. 如果是，你是用`dict.update()`/`dict(x)`/`list(x)`/直接`return x`这种"浅层"操作，还是用了`copy.deepcopy()`？
3. 想象一下"调用方拿到这个返回值之后，原地修改了它，会不会意外影响到你这边内部存的东西"？反过来，"调用方传给你的参数，之后调用方自己继续修改它，会不会意外污染你已经存进去的东西"？

只要能对这两个问题给出"不会，因为我做了深拷贝"的确定回答，这类bug基本就被排除了。

## 本章小结

- Python变量是"贴在对象上的标签"，不是"独立的盒子"；两个变量可能指向同一个对象，这叫别名（aliasing）。
- 浅拷贝（`dict.update()`/`dict(x)`/`list(x)`）只复制最外层，嵌套的可变对象依然共享引用；深拷贝（`copy.deepcopy()`）递归复制，彻底独立。
- `@dataclass(frozen=True)`只锁定字段的"绑定关系"，锁不住字段指向的可变对象内部被修改。
- 这个框架里`ainative-mcp`/`ainative-a2a`/`ainative-memory`/`ainative-workflow`四个包，都独立发现并修复过这同一类bug——存/取两端都要深拷贝。

## 动手做

打开本章开头的第一段代码，自己在Python里跑一遍，亲眼验证"修改merged，template也变了"这个现象；然后加上`copy.deepcopy()`，验证bug被修复。这个"眼见为实"的过程，会比读十遍文字解释更让你记住这个陷阱。

## 面试可能会问

**问：什么是Python里的浅拷贝和深拷贝，什么场景下浅拷贝会导致bug？**

答题思路：先给出精确定义（浅拷贝只复制最外层容器，嵌套对象共享引用；深拷贝递归复制，完全独立），然后**用一个具体场景说明后果**——比如"一个配置合并函数如果只做浅拷贝，调用方修改合并结果时会意外污染原始配置模板"，如果能进一步提到"哪怕字段本身是frozen dataclass，也不能免疫这个问题，因为frozen锁的是字段绑定关系，不是对象内部可变性"，会显著体现你不是背答案，而是真正理解这个机制的边界。
