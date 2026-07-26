"""MCP server配置组装 + stdio子进程环境变量安全白名单。

改造自真实项目里验证过的设计：多个agent各自手写`MultiServerMCPClient`
需要的server配置字典（transport判断、env白名单过滤），任何调整都要人工
同步到每一处，容易遗漏。本模块统一"如何拼装配置字典/如何过滤白名单
环境变量"这一层，不判断/决定该用哪种transport（http/stdio/sse）——
由调用方决定，保持各agent现有的传输方式选择不变。
"""

# MCP 是 "Model Context Protocol"（模型上下文协议）的缩写——一套让大模型
# Agent能够统一地去调用"外部工具/外部服务"的通信协议，比如让Agent能操作
# 浏览器（Playwright）、读写文件系统、查数据库等。`MultiServerMCPClient`
# （来自第三方库langchain-mcp-adapters）就是"同时连接多个MCP server、
# 把它们的工具都汇总给Agent使用"的客户端，它需要一份"每个server叫什么
# 名字、怎么连接它"的配置字典，本文件就是负责组装这份配置字典的。

# 这一行是Python的"未来特性"声明：让下面所有类型注解（比如函数参数、
# 返回值上写的类型）都不需要在写代码的时候真正存在，Python解释器只把它们
# 当作字符串暂存，等真正需要检查类型时（比如IDE/mypy）才去解析。
# 好处：写`dict | None`这种"新式"类型写法时，不加这一行在旧版本Python
# 里可能会在某些场景报错，加了就没问题，兼容性更好。
from __future__ import annotations

# copy模块是Python标准库自带的，提供"复制一份数据"的工具函数。这里用到
# 的`copy.deepcopy`——"深拷贝"——见下面`merge_mcp_configs`函数里的详细
# 解释，这是本文件最关键的一个安全细节。
import copy

# os模块是Python标准库自带的，用来读取"环境变量"——环境变量是在启动程序
# 之前，由操作系统/终端/部署平台设置好的一些"全局小纸条"，比如
# ANTHROPIC_API_KEY=sk-xxx 这种，程序运行时可以读到它。这里用
# `os.environ`来读取"当前进程已有的所有环境变量"，为下面的白名单过滤
# 做准备。
import os

# `frozenset` 是Python内置的"不可变集合"类型——普通的`set`（集合）创建
# 之后还可以增删元素，`frozenset`一旦创建就不能再改动。这里用它来存
# "允许透传给子进程的环境变量名单"，用不可变类型可以防止某处代码不小心
# 往白名单里"偷偷加了一个不安全的变量名"，而且集合类型判断"某个值在不在
# 里面"（比如下面的`k in safe_keys`）比列表快得多。
SAFE_ENV_KEYS: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "DISPLAY", "LANG", "LC_ALL",
    "PLAYWRIGHT_BROWSERS_PATH", "NODE_PATH",
    "TMPDIR", "TEMP", "TMP",
    "SystemRoot", "SYSTEMROOT", "ComSpec", "USERPROFILE",  # Windows
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",  # Windows
})
"""stdio模式下只把这些变量传给子进程的默认安全白名单，防止API Key等密钥
通过环境变量泄露给子进程。这是一份可覆盖的默认上限，不是唯一的安全边界——
需要更多变量（比如让Playwright能找到浏览器/Node路径）的调用方可以通过
`build_safe_env(extra=...)`按需扩展。"""
# ↑ 为什么要有白名单这件事本身：MCP的"stdio"传输方式，本质是Python
# 进程在本机启动了另一个"子进程"（比如一个Node.js写的MCP server），
# 而子进程默认情况下会"继承"启动它的父进程的全部环境变量——如果父进程
# （也就是当前跑Agent的这个Python程序）里通过环境变量存了
# ANTHROPIC_API_KEY等密钥，全部原样传给子进程，一旦这个子进程代码本身
# 有问题（或被恶意篡改），密钥就有可能通过子进程泄露出去（比如被打印
# 到日志、被发送到某个远程地址）。"白名单"策略就是明确只放行少数几个
# 已知安全、且子进程正常运行确实需要的变量（比如PATH让子进程能找到可
# 执行文件），凡是没写在白名单里的变量（包括几乎所有密钥类变量），
# 一律不透传给子进程——即使父进程环境里确实配置了。


def build_safe_env(
    extra: dict[str, str] | None = None,
    *,
    safe_keys: frozenset[str] = SAFE_ENV_KEYS,
) -> dict[str, str]:
    """按白名单过滤当前进程环境变量，再叠加`extra`（自定义键优先，会覆盖白名单同名值）。

    只应在真正需要把宿主环境变量透传给stdio子进程时调用；不需要宿主环境
    透传的场景，直接传一个普通dict给`build_mcp_config(env=...)`即可。
    """
    # 函数签名里的 `*` 单独出现在参数列表中间，表示它后面的参数
    # （这里是`safe_keys`）必须用"参数名=值"的方式传入，比如
    # `build_safe_env(extra={...}, safe_keys={...})`，不能写成
    # `build_safe_env({...}, {...})`（按位置传）。这样强制调用方在
    # 传`safe_keys`这种"改变安全边界"的参数时，必须显式写出参数名，
    # 不会因为参数顺序记错而不小心传错、悄悄放宽了白名单。

    # 这是一个"字典推导式"（dict comprehension）——`{表达式 for 变量 in 可迭代对象 if 条件}`
    # 的简写写法，等价于写一个for循环、每次判断条件为真就往一个新字典里塞一条，
    # 但更简洁。这里的意思是：遍历当前进程的所有环境变量
    # （`os.environ.items()`会给出一串`(变量名, 变量值)`的配对），只保留
    # 变量名`k`存在于白名单集合`safe_keys`里的那些，组成一个新的、只含
    # 安全变量的字典——凡是没在白名单里的变量（绝大多数密钥都属于这类）
    # 直接被过滤掉，不会出现在结果里。
    safe_env = {k: v for k, v in os.environ.items() if k in safe_keys}
    # `extra`是调用方明确希望额外传给子进程的变量（比如某个MCP server
    # 确实需要一个专属的配置值），不受白名单限制——这是调用方的显式选择，
    # 不是"意外泄露"。
    if extra:
        # `.update(...)` 是字典的一个方法：把另一个字典里的键值对，逐一
        # 合并进当前字典——如果键名已存在就覆盖旧值，不存在就新增。这里
        # 意味着如果`extra`里的键名和白名单过滤后的`safe_env`重复，
        # `extra`里的值会"获胜"（覆盖掉从宿主环境读到的值），这正是
        # docstring里说的"自定义键优先"。
        safe_env.update(extra)
    return safe_env


def build_mcp_config(
    name: str,
    transport: str,
    *,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, dict]:
    """组装`MultiServerMCPClient({...})`需要的单个server配置字典（按`name`为key）。

    只负责把参数拼成字典，不判断该用哪种transport——由调用方决定。省略的
    可选参数不会出现在结果字典里（避免给http配置塞入stdio专属的
    `command`/`env`等无意义键）。
    """
    # `transport`——传输方式，比如"http"（走网络请求连接一个已经在运行的
    # MCP server）或"stdio"（本进程自己启动一个子进程，通过它的标准输入/
    # 输出管道通信）。`url`/`headers`是http方式需要的参数；
    # `command`/`args`/`cwd`/`env`是stdio方式需要的参数（要启动哪个
    # 可执行文件、传什么命令行参数、在哪个工作目录下启动、给子进程什么
    # 环境变量）——同一个函数两种transport共用，靠"这个参数有没有传"来
    # 决定要不要把它塞进最终配置。

    # `entry: dict = {...}` 这里的`entry`是一个"局部变量"（只在这个函数
    # 执行期间存在），类型注解写成`dict`，初始值是一个只有一个键
    # "transport"的字典——后面根据传了哪些可选参数，逐步往这个字典里
    # 追加更多键。
    entry: dict = {"transport": transport}
    # 下面这一串`if xxx is not None:`的写法，是本函数的核心逻辑：只有
    # 调用方真的传了这个参数（没传的话，函数签名里写的默认值就是
    # `None`），才把它写进最终的配置字典里。这样http场景下省略
    # `command`/`env`等参数，最终配置字典里就完全不会出现这些键——
    # 而不是出现一堆值为`None`的无意义键，让配置更干净、也避免下游
    # 代码看到`env: null`这种值产生歧义。
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
    # 返回值是"一个只有一个键的字典，键是server名字，值是刚拼好的配置"——
    # 这个"外面再包一层、用name当key"的形状，正好和`merge_mcp_configs`
    # 期望接收的每个参数的形状一致，方便直接把多个`build_mcp_config(...)`
    # 的返回值传给`merge_mcp_configs(...)`合并。
    return {name: entry}


def merge_mcp_configs(*configs: dict[str, dict]) -> dict[str, dict]:
    """把多个`build_mcp_config()`的结果合并成一份`MultiServerMCPClient`需要的完整配置字典。

    后面的配置在server名重复时会覆盖前面的（与普通dict合并语义一致），
    调用方应确保各server名唯一，不依赖这里的覆盖行为掩盖命名冲突。

    返回的每个server配置都是深拷贝，不与传入的原始`configs`共享任何
    可变对象（列表/嵌套dict如`headers`/`env`）——调用方常见的用法是
    "构造一次配置模板、合并进最终配置、再按每次请求需要临时修改某个
    字段（比如刷新一个auth token）"，如果合并结果和原始输入共享同一份
    嵌套dict引用，修改"合并后的"配置会静默污染调用方仍然持有的原始
    模板对象，导致下一次复用模板时意外带着上一次的修改（比如认证信息
    在看似独立的请求之间发生泄漏）。
    """
    # 函数参数写成`*configs`——前面带一个`*`——表示"可以传任意个数的位置
    # 参数，都会被自动收集进一个名叫`configs`的元组"。也就是说调用方可以
    # 写`merge_mcp_configs(cfg1, cfg2, cfg3)`，函数内部`configs`就是
    # `(cfg1, cfg2, cfg3)`这个元组，不需要调用方自己先拼成一个列表再传。

    # `merged: dict[str, dict] = {}` 声明一个空字典，用来存放"合并到目前
    # 为止的结果"，随着下面的循环逐步被填充。
    merged: dict[str, dict] = {}
    for config in configs:
        # 这一行是本文件"真实bug背景"里修复的关键点，值得完整讲清楚：
        #
        # 【问题】如果这里写成 `merged.update(config)`（不做深拷贝），
        # 字典的`.update()`方法在合并嵌套字典时，只是把"指向同一份数据
        # 的引用"复制过去，并不会真正复制一份新的数据。举例说明：假设
        # `config = {"fs": {"transport": "stdio", "env": {"PATH": "/usr/bin"}}}`，
        # 那么 `merged.update(config)` 之后，`merged["fs"]["env"]` 和
        # `config["fs"]["env"]` 其实是内存里同一个字典对象，只是有两个
        # 名字都能访问到它——这在Python里叫"别名"（aliasing）。
        # 如果调用方后续对`merged["fs"]["env"]`做了修改（比如
        # `merged["fs"]["env"]["TOKEN"] = "new-token"`刷新一个认证令牌），
        # 这个修改会"隔空"同时体现在原始的`config`变量上（因为它们本来
        # 就是同一个对象），如果`config`是调用方保留下来准备复用的
        # "配置模板"，模板就被意外污染了——下次调用方以为自己又用了一份
        # "干净的模板"，实际上却带着上一次请求遗留的token，造成认证信息
        # 在两次本该独立的请求之间意外串联。这类bug往往很难排查，因为
        # 代码表面上看"只改了merged，没碰config"，实际上两者指向同一块
        # 内存。
        #
        # 【修复】`copy.deepcopy(config)`——"深拷贝"——会递归地把
        # `config`里所有嵌套的字典/列表都重新创建一份全新的、内存地址
        # 不同的副本，而不是仅复制"最外层"、内部嵌套结构仍共享引用（那种
        # 只复制最外层的做法叫"浅拷贝"，比如`dict(config)`或
        # `config.copy()`都是浅拷贝，不能解决这个问题）。深拷贝之后，
        # `merged`里的每一份配置都是完全独立的新对象，之后不管调用方
        # 怎么修改`merged`，都不会影响到原始传入的`config`。
        # `.update(...)`本身仍然是"后面的配置覆盖前面同名的键"的合并
        # 语义（普通字典合并规则），这里只是确保被合并进去的是"拷贝"
        # 而不是"引用"。
        merged.update(copy.deepcopy(config))
    return merged
