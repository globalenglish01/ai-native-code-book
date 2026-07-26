# 第24章 —— MCP是什么

代码位置：`packages/ainative-mcp/src/ainative_mcp/config.py`、`packages/ainative-mcp/src/ainative_mcp/audit.py`

## 一个Agent需要同时用浏览器、查文件、连数据库

假设你在做一个"调研助手"Agent：用户问它一个问题，它需要打开浏览器搜索资料，还需要读写本地文件系统保存草稿，可能还要查一下内部数据库确认某个事实。这三件事——操作浏览器、操作文件系统、查数据库——分别是三个完全不同的"外部工具"，各自可能是别人写好的现成程序（比如社区提供的Playwright浏览器自动化服务、文件系统服务），你的Agent想用它们，总得有一套统一的方式去"发现这个工具能做什么、怎么调用它、传什么参数"。

如果没有统一标准，结果就是：每接入一个新工具，都要单独写一套"怎么跟它对话"的适配代码——浏览器工具的API长一个样子，文件系统工具的API长另一个样子，数据库工具又是第三个样子。接入的工具越多，维护成本越高，而且几乎每个团队都在重复发明这套"适配层"。

**MCP**（Model Context Protocol，模型上下文协议）就是为了解决这个问题而生的一套通用协议标准：只要一个工具服务遵循MCP协议实现，任何支持MCP的Agent框架都能用统一的方式发现它、调用它，不需要为每个工具单独写适配代码。你可以把MCP理解成"AI Agent界的USB接口"——不同厂商的工具只要都实现同一个协议，就能被同一个"插口"识别和使用。

本章要看的`ainative_mcp`包，并不实现MCP协议本身（协议的具体通信细节由第三方库`langchain-mcp-adapters`里的`MultiServerMCPClient`负责），它解决的是协议之外、几乎每个真正接入MCP的团队都会撞上的两个具体麻烦：**怎么干净地组装"接入哪些MCP server"的配置**，以及**工具被调用之后怎么留一份可查的审计记录**。

## `MultiServerMCPClient`需要什么样的配置

`MultiServerMCPClient`这个客户端类的作用是"同时连接多个MCP server，把它们各自提供的工具都汇总起来，交给Agent统一使用"。要做到这一点，它的构造函数需要一份字典，告诉它"每个server叫什么名字、用什么方式连接它"。这份字典大概长这样：

```python
{
    # http传输：这个server已经独立跑起来了，直接发HTTP请求跟它通信。
    "search": {"transport": "http", "url": "http://localhost:9000/mcp"},
    "filesystem": {
        # stdio传输：需要自己启动一个子进程来运行这个server。
        "transport": "stdio",
        "command": "npx",   # 启动子进程用的可执行文件
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],   # 命令行参数列表
        "env": {"PATH": "/usr/bin"},   # 给这个子进程什么环境变量
    },
}
```

注意这两个server配置长得不一样——原因是MCP支持不止一种"传输方式"（transport），最常见的两种是：

- **http**：MCP server本身是一个已经独立运行着的网络服务，Agent通过发HTTP请求跟它通信，需要的是`url`（服务地址）和可能的`headers`（比如认证信息）。
- **stdio**：MCP server没有独立运行，而是需要Agent这边**自己启动一个子进程**来运行它（比如启动一个Node.js写的程序），通过这个子进程的标准输入/输出（stdin/stdout）管道进行通信，需要的是`command`（启动哪个可执行文件）、`args`（命令行参数）、`cwd`（工作目录）、`env`（给子进程什么环境变量）。

两种transport需要的字段完全不同，如果每接入一个MCP server就手写一遍这个字典，很容易漏字段、或者把http场景不需要的`command`/`env`之类的键也顺手塞进去——这正是`ainative_mcp.config`模块存在的原因：把"组装配置字典"这一层单独抽出来，做成一个**不判断该用哪种transport（这个决定权留给调用方），只负责把参数正确拼成字典**的工具函数。

## `build_mcp_config`：只放行"真正传了"的参数

```python
def build_mcp_config(
    name: str,
    transport: str,
    # *之后所有参数必须用"参数名=值"方式传，防止几个字符串/列表参数
    # 因为位置顺序传反而不自知。
    *,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, dict]:
    entry: dict = {"transport": transport}   # 一开始只有transport这一个键
    # 一连串"只有真的传了才写进去"的判断——None代表"没传"，
    # 不会往结果字典里塞一堆无意义的None值键。
    if url is not None:
        entry["url"] = url
    if headers is not None:
        entry["headers"] = headers
    if command is not None:
        entry["command"] = command
    if args is not None:
        entry["args"] = args
    if cwd is not None:
        entry["cwd"] = cwd
    if env is not None:
        entry["env"] = env
    return {name: entry}   # 外面包一层，用name当key，方便和其他配置合并
```

先看函数签名里的一个语法细节：`transport`后面单独出现的这个`*`。在Python里，参数列表中间单独写一个`*`，意思是"它后面的所有参数都必须用`参数名=值`的方式传入，不能按位置传"。也就是说这个函数只能写成`build_mcp_config("search", transport="http", url="...")`，不能写成`build_mcp_config("search", "http", "http://...")`把`url`当第三个位置参数传进去。这样设计的好处是：`url`/`headers`/`command`/`args`/`cwd`/`env`这六个参数,一眼看上去很难靠位置顺序去记忆和区分，强制写出参数名可以避免调用方记错顺序、把`cwd`的值意外传成了`url`的值这类容易发生却很难被发现的错误。

再看函数体的核心逻辑：`entry`一开始只有`transport`一个键，然后是一串`if xxx is not None:`——只有调用方**真的传了**这个参数，才把它写进最终的字典。这里的关键在于函数签名里所有可选参数的默认值都是`None`，而不是空字符串或空列表——`None`代表"调用方没有提供这个信息"，这是一个能明确区分"没传"和"传了一个空值"的哨兵值(sentinel)。如果反过来写成不做这层判断、把所有参数不管有没有传都塞进字典，一个http场景的配置就会变成这样：

```python
{"transport": "http", "url": "...", "headers": None, "command": None, "args": None, "cwd": None, "env": None}
```

这份配置里混进了一堆值为`None`的无意义键——不仅让配置字典变得又长又难读，更麻烦的是可能让`MultiServerMCPClient`内部的逻辑在看到`env: null`这种值的时候产生歧义（"调用方是显式说了不要环境变量"还是"根本没提这件事"？）。`build_mcp_config`选择的做法是：**没传的参数，压根不出现在结果字典里**，配置字典只包含真正有意义的键，干净、无歧义。

函数最后返回的是`{name: entry}`——外面包了一层、用`name`当key的形状。这不是随手一写，而是刻意配合下一个要看的函数`merge_mcp_configs`——它期望接收的每一份参数都长这个形状，方便直接把多个`build_mcp_config(...)`的返回值拿去合并，不需要调用方自己再手动拼一层。

## `merge_mcp_configs`：合并配置，但每一份都是独立拷贝

```python
# *configs——可变参数，调用方可以传任意数量个配置字典。
def merge_mcp_configs(*configs: dict[str, dict]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for config in configs:
        # copy.deepcopy(config)——递归深拷贝，确保合并结果和原始
        # config不共享任何嵌套的可变对象（比如env字典）。
        merged.update(copy.deepcopy(config))
    return merged
```

这个函数的作用很直白——把多个`build_mcp_config(...)`的结果，合并成一份`MultiServerMCPClient`真正需要的完整配置字典，用起来大概是这样：

```python
# 每次调用build_mcp_config都返回一个{name: entry}形状的小字典。
http_cfg = build_mcp_config("search", transport="http", url="http://localhost:9000/mcp")
fs_cfg = build_mcp_config("filesystem", transport="stdio", command="npx", args=[...])
# 直接把这几份小字典喂给merge_mcp_configs，拼成一份完整配置。
full_config = merge_mcp_configs(http_cfg, fs_cfg)
# full_config == {"search": {...}, "filesystem": {...}}
```

这里唯一值得反复强调的一行，就是`merged.update(copy.deepcopy(config))`里的`copy.deepcopy`——这正是第5章开篇那个"别名bug大家族"故事里第一个、也是最典型的例子：如果这里写成朴素的`merged.update(config)`，`config`里嵌套的字典（比如stdio配置里的`env`字典）不会被真正复制一份新的，而是和`merged`共享同一个对象引用。调用方常见的用法是"构造一次配置模板、合并进最终结果、再按每次请求临时改一个字段（比如刷新一个auth token）"——一旦共享引用，修改"合并后"的配置会静默污染调用方仍然持有的原始模板，导致认证信息在本该独立的两次请求之间意外串联。具体的成因、修复方式、以及框架里其他三处同类bug，第5章已经完整讲过，这里不再重复，只需要记住结论：**`merge_mcp_configs`保证返回的每份server配置都与传入的原始`config`完全独立，不共享任何嵌套的可变对象**。

## `audit.py`：工具被调用之后，留下一份"发生过什么"的记录

配置组装解决的是"连接前"的问题，`audit.py`解决的是"调用后"的问题——一次工具调用发生之后，你需要知道"哪个agent调用了哪个工具、传了什么、返回了什么、成功没有、花了多久"，这样出问题时才有据可查，也能统计出"这个工具最近的失败率是不是异常升高了"这类运维指标。`ToolCallRecord`就是这份记录的标准格式：

```python
@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的完整审计记录。"""

    tool_name: str    # 调用了哪个工具
    agent_name: str    # 是哪个Agent发起的调用
    status: ToolCallStatus   # 这次调用成功还是失败
    duration_ms: float   # 这次调用耗时多少毫秒

    run_id: str | None = None
    thread_id: str | None = None
    input_summary: dict[str, Any] = field(default_factory=dict)   # 这次调用传了什么参数的摘要
    output_summary: dict[str, Any] = field(default_factory=dict)   # 这次调用返回了什么的摘要
    error_message: str | None = None   # 失败时的错误信息
    # field(default_factory=time.time)——注意这里传的是time.time
    # 这个函数本身、不加括号调用；每创建一条新记录，如果没显式传
    # timestamp，就会自动调用一次time.time()取当前时刻。
    timestamp: float = field(default_factory=time.time)
```

选择`frozen=True`不是随便加的——一条审计记录代表的是"某一次工具调用已经发生过的、不可变的历史事实"，调用已经结束了，记录下来的信息不应该、也不需要事后再被改动。`timestamp`用`field(default_factory=time.time)`（注意这里传的是`time.time`这个函数本身，不加括号调用），意味着每创建一条新记录，如果没有显式传时间戳,就会自动调用一次`time.time()`,取"这条记录被创建出来那一刻"的时间。

`InMemoryToolCallAuditLog`是这份记录的一个内存版存储实现（真实项目里应该换成数据库或日志系统，这里只是给demo/测试用的最简单实现）：

```python
def for_tool(self, tool_name: str) -> list[ToolCallRecord]:
    # 列表推导式：只保留tool_name字段精确等于传入值的记录。
    return [r for r in self._records if r.tool_name == tool_name]

def error_rate(self, tool_name: str | None = None) -> float:
    # 条件表达式：传了具体工具名就只看这个工具的记录；
    # 没传（None）就直接用全部记录，不经过for_tool这层过滤。
    records = self.for_tool(tool_name) if tool_name is not None else self._records
    if not records:
        return 0.0   # 没有任何记录，错误率没有意义，给0
    # sum(1 for r in records if 条件)——统计满足"状态是error"的记录条数。
    errors = sum(1 for r in records if r.status == "error")
    return errors / len(records)   # 错误条数除以总条数，得到错误率
```

## 同一个`None`，在两个方法里是两件完全不同的事

这两个方法放在一起看,藏着一个非常值得细品的API设计细节——源码的docstring里专门用一段话强调了这一点：

```python
def for_tool(self, tool_name: str) -> list[ToolCallRecord]:
    """按工具名精确匹配——`tool_name`必须是一个真实的工具名字符串。

    注意：这里的`None`不是"不过滤"的特殊值（那是`error_rate(tool_name=None)`
    的语义，两者故意不同）——`for_tool(None)`会按字面意思匹配
    `ToolCallRecord.tool_name is None`的记录（正常情况下不应该存在，
    因为该字段声明类型是`str`），而不是返回全部记录。不要把这两个
    方法的`None`当作同一件事。
    """
```

具体来说：

- `for_tool(tool_name)`的语义是"精确匹配"——你传什么，它就去找`tool_name`字段**等于**这个值的记录。如果你真传了`for_tool(None)`,它会去找`r.tool_name == None`的记录——但`ToolCallRecord.tool_name`字段声明的类型是`str`，正常情况下永远不会有一条记录的`tool_name`字面上就是`None`，所以`for_tool(None)`几乎总是返回一个空列表,而不是"返回全部记录"。
- `error_rate(tool_name=None)`的语义完全不同——这里的`None`是一个**"不过滤"的哨兵值**：调用方传了具体工具名，就只统计这个工具的错误率；传`None`（或者干脆不传，因为默认值就是`None`），就统计**全部**工具加在一起的整体错误率。

也就是说，同一个值`None`,在这两个紧挨着的方法里，表达的是两种截然相反的意思——一个是"字面匹配None这个值本身"，一个是"不做任何过滤、看全部"。这是`error_rate`内部用条件表达式`self.for_tool(tool_name) if tool_name is not None else self._records`区分出来的：只有当`tool_name is not None`时才去调用`for_tool`做精确匹配，否则直接跳过`for_tool`,使用完整的`self._records`。

这不是一个偶然的疏忽,而是文档里特意点出来提醒开发者注意的真实设计权衡。为什么会出现这种"看起来不一致"的设计？因为这两个方法各自单独看都很合理——`for_tool`作为一个通用的"按字段精确匹配"工具方法,保持"传什么就精确匹配什么"的语义是最不容易让人困惑的默认行为；而`error_rate`作为一个更上层的统计方法，"不传参数就统计全部"是使用者最自然的直觉期待（类似很多统计函数，参数留空就代表"整体"）。**两个方法各自的设计都没有错，但放在同一个类里，两处`None`的含义不同,如果不特别说明就容易让使用者产生"我以为`for_tool(None)`也是返回全部"的错误预期**——这正是源码docstring要专门用一段话把这件事讲清楚的原因：好的API设计不仅要让每个函数自己讲得通，还要留意"相邻的、看起来相似的函数，会不会给使用者制造错误的心智模型"。这也是这本书里反复出现的一个主题的具体案例：**代码写得对，不代表调用方一定会用对——文档的责任，就是补上代码本身补不上的这层"意图说明"**。

## 本章小结

- MCP（Model Context Protocol）是一套让AI Agent统一调用外部工具（浏览器、文件系统、数据库等）的通信协议标准，`ainative_mcp`包本身不实现协议，只负责配置组装和审计记录这两件配套的事。
- MCP有多种transport（http走网络请求连接已运行的服务，stdio由本进程启动子进程通过管道通信），`build_mcp_config`用一串`if x is not None`只把调用方真正传了的参数写进配置，避免混入无意义的`None`值键。
- `merge_mcp_configs`合并配置时用`copy.deepcopy`保证结果与原始输入完全独立，这正是第5章讲过的别名bug在这个包里的真实修复。
- `ToolCallRecord`用`frozen=True`表达"审计记录是不可变的历史事实"，`InMemoryToolCallAuditLog.for_tool(None)`和`error_rate(tool_name=None)`里的两个`None`含义故意不同——前者是字面匹配，后者是"不过滤"的哨兵值，这是一个值得记住的API设计教训。

## 动手做

```python
from ainative_mcp import build_mcp_config, merge_mcp_configs, InMemoryToolCallAuditLog, ToolCallRecord

# 组装两个不同transport的server配置
http_cfg = build_mcp_config("search", transport="http", url="http://localhost:9000/mcp")
fs_cfg = build_mcp_config("filesystem", transport="stdio", command="npx", args=["-y", "server-fs"])

full_config = merge_mcp_configs(http_cfg, fs_cfg)
print(full_config)
# 观察：search的配置里没有command/args/env，filesystem的配置里没有url/headers

# 记录几次工具调用，体会error_rate(None) 和 for_tool(None) 的区别
log = InMemoryToolCallAuditLog()   # 内存版审计日志存储
log.record(ToolCallRecord(tool_name="read_file", agent_name="a1", status="success", duration_ms=10))
log.record(ToolCallRecord(tool_name="read_file", agent_name="a1", status="error", duration_ms=20))

print(log.error_rate("read_file"))   # 精确统计这一个工具：0.5
print(log.error_rate())              # 不传参数，统计全部：0.5（这里恰好相同，因为只有一个工具）
print(log.for_tool(None))            # 字面匹配tool_name为None的记录：空列表[]
```

## 面试可能会问

**问：你在设计一个类的多个方法时，会遇到"要不要用`None`表示不同含义"的选择——怎么避免这种设计让调用方误解？**

答题思路：先承认这是一个真实存在的权衡，不是非黑即白的对错问题——用具体例子说明（比如`ainative_mcp`里`for_tool(None)`是字面匹配，`error_rate(tool_name=None)`是"不过滤"的哨兵值，两者故意不同）。然后给出两条可执行的缓解方式：一是在docstring里明确写清楚每个`None`具体代表什么，尤其是当相邻方法的语义不一致时要专门点出来提醒；二是如果条件允许，尽量让同一个类里"看起来相似"的方法保持`None`语义一致，只有在确有必要（比如`error_rate`更贴近统计类API的直觉习惯）时才允许出现例外，并用清晰的文档弥补这种不一致带来的认知负担。能提到"接口设计不仅要对自己讲得通，还要考虑会不会给调用方制造错误的心智模型"，会显示出比"背答案"更深一层的理解。
