"""跨模块共享的协议（Protocol）与数据结构定义。

设计原则：这个文件里的每一个类型，都只描述"需要具备什么行为"，不涉及
"具体用什么数据库/中间件去实现这个行为"——这是让 ainative-core 及其上层
模块（ainative-guardrail/ainative-prompt/ainative-security/ainative-eval）
能够脱离任何具体基础设施独立运行、又能在真实项目里被替换成真实存储的
关键设计。

真实项目接入时的典型用法：自己写一个类，继承/实现下面某个 Protocol，
构造函数里去连真实的 Postgres/Redis/MongoDB，然后把这个类的实例，作为
参数传给 ainative-guardrail/ainative-prompt 等模块里需要它的地方。
"""

from __future__ import annotations

# Callable 用来给"一个函数/方法"本身写类型注解——`Callable[[Any], bool]`
# 表示"一个接收一个任意类型参数、返回布尔值的函数"，`Callable[[], GateResult]`
# 表示"一个不接收任何参数、返回GateResult的函数"。
from collections.abc import Callable

# dataclass、field 见下面第一次出现dataclass时的详细解释。
from dataclasses import dataclass, field

# datetime/UTC 用来表示"某个具体的时间点"，UTC是"世界统一时间"这个时区，
# 服务器记录时间时通常统一用UTC，避免因为服务器部署在不同时区而混乱。
from datetime import UTC, datetime

# Any——任意类型；Literal——"字面量类型"，比如`Literal["fail", "warn"]`
# 表示"这个值只能是字符串'fail'或字符串'warn'这两者之一，不能是别的
# 任意字符串"，比普通的`str`类型注解更精确，IDE和类型检查工具能帮你
# 发现"传错了一个不在允许范围内的字符串"这种错误。
# Protocol/runtime_checkable——本文件的核心，见下面详细解释。
from typing import Any, Literal, Protocol, runtime_checkable

# ═══════════════════════════════════════════════════════════════════════
# 本文件反复出现两个Python语法概念，这里先集中解释一次，后面就不再重复：
#
# 【dataclass 数据类】
# 写在类前面的 `@dataclass` 是一个"装饰器"，作用是自动帮你生成这个类的
# 构造函数（__init__）、比较逻辑（==）、打印格式（repr）等样板代码——
# 你只需要按 `字段名: 类型` 的格式声明这个类有哪些字段，不用自己手写
# `def __init__(self, a, b): self.a = a; self.b = b` 这类重复代码。
# `@dataclass(frozen=True)` 里的 frozen=True 表示"冻结"——创建出实例
# 之后，字段就不能再被修改，任何"实例.字段 = 新值"的操作都会直接报错。
# 这份文件里的大多数dataclass都选择frozen=True，因为它们大多代表
# "一次性传递的数据快照"（比如一次调用的结果），创建之后本就不应该
# 再被改动。
#
# 【Protocol 协议】
# `class Xxx(Protocol):` 定义的不是一个"可以直接创建实例"的普通类，
# 而是一份"接口约定"——只规定"需要有哪些方法、方法的参数和返回值类型
# 是什么"，方法体里用`...`（表示"具体怎么实现，这里不关心"）。
# 任何别的类，只要真的实现了这些同名同参数的方法，Python就会认为它
# "满足了这个Protocol"，不需要显式写`class MyStore(PromptStore):`
# 去声明"我继承/实现了它"——这叫"结构化类型"（只要长得像鸭子、叫声像
# 鸭子，就当它是鸭子，不需要血缘关系）。
# `@runtime_checkable` 这个装饰器让这份协议额外支持
# `isinstance(某个对象, 这个Protocol)` 这种运行时检查——没加这个装饰器的
# Protocol，只能在写代码/类型检查阶段起作用，不能在程序真正运行时用来
# 判断一个对象符不符合约定。
# ═══════════════════════════════════════════════════════════════════════

# 下面这种 `# ── 标题 ──` 格式的注释，纯粹是给人看的"分节标题"，把这个
# 文件里数量不少的类型定义，按照"属于哪个功能领域"分组，方便阅读时
# 快速定位，本身不影响程序运行。
# ── 用量记录 ──────────────────────────────────────────────────────────────────


@runtime_checkable
class UsageSink(Protocol):
    """"一次LLM调用产生的用量该被记到哪里"这件事的抽象接口。

    真实项目里，通常会实现一个把 event 写进 MongoDB/Postgres/日志系统的版本；
    本包默认提供 `InMemoryUsageSink`（见 `memory_backends.py`），只把事件存进
    一个内存列表，方便脱离数据库直接跑通demo和测试。
    """

    def record(self, event: dict[str, Any]) -> None:
        """记录一条用量事件。

        `event` 至少应该包含：`agent_name`、`provider`、`model`、
        `input_tokens`、`output_tokens`、`timestamp` 这几个字段，具体
        字段集合由调用方（`model_factory.py`）决定，本协议不强制schema，
        只要求"能接收一个dict、自己决定怎么持久化"。
        """
        # 这个`...`（英文叫Ellipsis）在Protocol里就是"方法体先空着"的
        # 占位符——Protocol本身不会被直接实例化调用，真正的实现逻辑
        # 由别的类（比如InMemoryUsageSink）提供。
        ...


# ── 密钥安全巡检 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SecretRule:
    """一条"某个配置项是否还停留在已知的不安全默认值上"的检查规则。

    这是从真实项目 `settings.py` 的 `_validate_secrets`（启动时强制校验）
    和 `secret_drift_monitor.py`（运行期周期巡检）里提炼出来的通用模式——
    这两处原本各自手写了一遍几乎相同的判断逻辑，本包把"规则"本身抽成
    数据，让两种触发时机（启动时/运行期）共用同一份规则定义，不再重复。
    """

    name: str
    """规则的简短标识，比如 "jwt_secret_is_default"。"""

    # 这个字段的类型是"一个函数"——调用时传入配置对象，返回True/False。
    # 把"怎么判断"这件事本身当作数据存起来（而不是写死成if/else代码），
    # 好处是不同项目可以传入自己的判断逻辑，SecretRule这个类本身完全
    # 不需要知道具体判断细节。
    is_default: Callable[[Any], bool]
    """接收调用方传入的配置对象，返回 True 表示"这一项目前还是不安全的默认值"。"""

    message: str
    """命中时展示给运维人员的具体说明文字。"""

    # `Literal["fail", "warn"] = "fail"` ——类型是"只能是这两个字符串
    # 之一"，默认值是"fail"。
    severity: Literal["fail", "warn"] = "fail"
    """`fail`：在生产/预发布环境应该阻断启动；`warn`：仅记录警告，不阻断。"""


# ── Prompt 版本管理 ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromptVariant:
    """一个Prompt在某个具体位置（agent_name + prompt_key）下的一个具体版本。"""

    variant: str
    """变体标识，比如 "default"、"v2"。"""

    content: str
    """这个变体真正的Prompt文本内容。"""

    traffic_pct: int
    """这个变体应该分到的流量百分比（0-100）。"""

    version: int
    """版本号，每次修改自增。"""

    # 布尔类型字段，默认值True——新创建的变体默认是"生效中"的状态。
    is_active: bool = True


@runtime_checkable
class PromptStore(Protocol):
    """Prompt版本管理+A/B分流所需要的最小存储接口。

    真实项目里通常用一张数据库表实现；`ainative_prompt.store.InMemoryPromptStore`
    提供了一份内存版实现，用普通dict存储，足以支撑demo和单元测试，但不适合
    当作生产环境的最终存储（进程重启数据即丢失）。
    """

    # 方法名前面的`async`表示这是一个"异步方法"——调用它时需要写成
    # `await store.get_active_variants(...)`，而不是直接调用。异步方法
    # 存在的意义是：真实实现如果要访问数据库/网络（比如查Postgres），
    # 这个过程需要等待，"异步"让程序在等待期间可以先去处理别的事情，
    # 而不是傻等在这里浪费时间。这份Protocol里几乎所有方法都是异步的，
    # 因为真实存储实现几乎必然涉及网络/磁盘IO。
    async def get_active_variants(
        self, agent_name: str, prompt_key: str
    ) -> list[PromptVariant]:
        """返回某个位置当前处于激活状态的全部变体（可能是0个、1个或多个）。"""
        ...

    async def get_sticky_decision(
        self, agent_name: str, prompt_key: str, thread_id: str
    ) -> str | None:
        """如果这个 thread_id 之前已经被分流到过某个具体变体，返回那个变体标识；
        否则返回 None（表示这是第一次，需要重新走一次分流决策）。"""
        ...

    async def record_decision(
        self, agent_name: str, prompt_key: str, thread_id: str, variant: str
    ) -> None:
        """记录"这个 thread_id 这一次被分流到了哪个变体"，供下次 get_sticky_decision 查询。"""
        ...

    async def save_variant(
        self, agent_name: str, prompt_key: str, variant: PromptVariant
    ) -> None:
        """新增或者更新一个具体的Prompt变体。"""
        ...


# ── 记忆存储 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemoryEntry:
    """一条可检索/可裁剪的结构化记忆条目。

    设计取自真实项目里"跨章节书籍记忆"的滑动窗口裁剪模式（按`sequence`排序、
    取最近N条），泛化为不限定业务场景的通用记忆条目——`sequence`可以是章节号、
    对话轮次、任务步骤序号等任何单调递增的排序键。
    """

    owner_id: str
    """这条记忆归属的主体标识（用户ID/线程ID/会话ID），用于`delete_by_owner`按主体删除。"""

    sequence: int
    """单调递增的排序键，`load_recent`按此字段裁剪"最近N条"。"""

    content: str
    """记忆内容本身（通常是LLM提取出的结构化摘要文本）。"""

    # `field(default_factory=dict)` 的写法专门用在"默认值是可变对象
    # （比如列表/字典）"的场景——不能直接写 `metadata: dict = {}`，
    # 因为那样所有实例会共享同一个字典对象（这是Python的一个经典陷阱：
    # 可变默认值只会在类定义时被创建一次，之后所有实例复用同一份）。
    # `default_factory=dict` 表示"每次创建新实例时，调用一次dict()
    # 生成一个全新的空字典"，确保每个实例都有自己独立的metadata。
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    """长期记忆的最小存储接口——存储无关，可插拔Postgres/Redis/文件系统等。

    `delete_by_owner`在设计之初就是一等公民接口，而不是事后补丁：真实项目
    的GDPR"被遗忘权"删除场景暴露过"多个存储介质各自需要清理、任一遗漏都会
    导致删除不彻底"这个真实教训，本协议要求任何`MemoryStore`实现从一开始
    就必须能回答"删除某个owner的全部记忆"这个问题。
    """

    async def append(self, entry: MemoryEntry) -> None:
        """追加一条新记忆。"""
        ...

    # `*` 出现在参数列表中间，表示它后面的参数（before_sequence/max_items）
    # 必须用"参数名=值"的方式传入，不能只按位置传（见config.py里的
    # 详细解释）。`int | None = None` 表示"可以是整数，也可以不传
    # （默认None）"。
    async def load_recent(
        self, owner_id: str, *, before_sequence: int | None = None, max_items: int = 10
    ) -> list[MemoryEntry]:
        """加载某个owner最近的记忆条目（按sequence降序取前max_items条）。

        `before_sequence`不为None时，只返回`sequence < before_sequence`的条目——
        用于"只取某个时间点之前的记忆"这类场景（比如只想看某一步骤之前积累的记忆）。
        """
        ...

    async def delete_by_owner(self, owner_id: str) -> int:
        """删除某个owner的全部记忆，返回删除的条目数。"""
        ...


@runtime_checkable
class CheckpointSaverFactory(Protocol):
    """"如何拿到一个可用的checkpoint存储句柄"这件事的抽象接口。

    改造自真实项目里验证过的设计：单例懒加载 + 区分"永久性失败"（比如
    依赖包未安装，重试无意义）与"暂时性失败"（比如数据库连接抖动，一段
    时间后可能自愈）两类错误，避免把偶发的连接抖动当作致命错误处理，也
    避免对确定不可能恢复的错误做无意义的重试。
    """

    async def get(self) -> Any | None:
        """返回可用的checkpoint存储句柄；永久性失败时返回`None`（不抛异常）。"""
        ...

    def reset(self) -> None:
        """重置内部状态（清除已缓存的单例），供测试或强制重新初始化时调用。"""
        ...


# ── 治理门控（FCARS风格） ─────────────────────────────────────────────────────

# 这一行不是类，是一个"类型别名"——给`Literal[...]`这一长串取一个短名字
# `GateStatus`，之后别的地方写`GateStatus`就等同于写这一整串Literal，
# 避免到处重复这六个具体状态值。
GateStatus = Literal["GREEN", "YELLOW", "RED", "UNKNOWN", "SKIPPED", "NEEDS_REVIEW"]


# 注意：这里是`@dataclass`，没有加`frozen=True`——意味着GateResult创建
# 之后字段仍然可以被修改。这是刻意的：治理Gate的判定逻辑有时需要先
# 创建一个初步结果、再根据后续复核调整某个字段（比如`score`），不像
# 前面那些"一次性数据快照"那样天然适合冻结。
@dataclass
class GateResult:
    """单一维度的检查结果——统一schema，不管这个维度具体检查的是什么内容。"""

    dimension: str
    gating: bool
    """True 表示这个维度的结果，真的会影响最终"是否放行"的判定；
    False 表示这个维度只做记录，不参与最终判定（比如实验性的新检查项）。"""
    status: GateStatus
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    score: float | None = None


@dataclass(frozen=True)
class GateCheck:
    """一条注册进 Gate 的具体检查项。"""

    name: str
    gating: bool
    check_fn: Callable[[], GateResult]
    """执行这项检查、返回 GateResult 的函数——不接收参数，所有需要的上下文
    应该在构造 GateCheck 时通过闭包/partial提前绑定好。"""


@dataclass
class GateDecision:
    """一次完整Gate运行的最终判定结果。"""

    passed: bool
    dimensions: list[GateResult]
    blockers: list[str]
    """未通过、且是gating维度的检查项名称列表——这些是导致 passed=False 的具体原因。"""

    # `default_factory=lambda: datetime.now(UTC)` ——lambda是Python里
    # "写一个不用取名字的匿名小函数"的语法，这里的意思是"每次创建新的
    # GateDecision实例时，调用一次'获取当前UTC时间'，作为默认值"。
    # 用lambda包一层是因为default_factory要求传入一个"不带参数就能
    # 调用"的函数，而`datetime.now`本身需要传时区参数，所以用lambda
    # 包成"不需要额外传参就能调用"的形式。
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Agent间通信（A2A） ────────────────────────────────────────────────────────
#
# 两个被审计的真实生产项目都没有采用"多agent互相委派任务"这条架构路径——
# 都刻意选择了单agent+分阶段提示词的更轻量方案，且有真实案例证明过早建设
# "跨agent通信基础设施"容易变成从未被使用的负资产（一个自建的MCP网关因
# 零真实流量被正式下线）。以下协议是全新设计（不是从真实项目提炼），
# 面向确实需要"一个agent把子任务委派给另一个能力不同的agent、等待结果"
# 这类场景，设计时刻意保持最小化：不预设消息队列/服务发现等重基础设施，
# 默认实现是进程内直接函数调用。


@dataclass(frozen=True)
class AgentCapability:
    """一个agent对外声明的能力描述——用于发现"谁能处理这类任务"。"""

    name: str
    """能力标识，比如 "generate_test_plan"、"summarize_document"。"""

    description: str
    """人类可读的能力说明，供其他agent/编排层判断是否应该委派给它。"""

    input_schema: dict[str, Any] = field(default_factory=dict)
    """输入参数的schema描述（不强制具体格式，JSON Schema风格的dict是常见选择）。"""

    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class A2ATask:
    """一次agent间任务委派请求。"""

    task_id: str
    capability: str
    """目标能力名称，对应`AgentCapability.name`。"""

    payload: dict[str, Any]
    sender_agent: str
    """发起委派的agent标识——用于审计和委派深度追踪。"""

    # `tuple[str, ...]` 表示"一个元组，里面每一项都是字符串，长度不限"
    # （`...`在这里的含义和Protocol里的`...`不同，是类型注解语法，表示
    # "前面这个类型可以重复任意次"）。用元组而不是列表，是因为元组本身
    # 不可变，天然适合"委派链条"这种一旦记录下来就不该再被中途篡改的
    # 数据。默认值`()`是一个空元组，表示"最初发起的这一次委派，链条
    # 还是空的"。
    delegation_chain: tuple[str, ...] = ()
    """从最初发起方到当前委派链路上依次经过的agent标识，用于检测"A委派给B、
    B又委派给A"这类循环委派，以及限制委派链条的最大深度。"""


@dataclass(frozen=True)
class A2AResult:
    """一次agent间任务委派的结果。"""

    task_id: str
    status: Literal["success", "error"]
    output: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


@runtime_checkable
class AgentRegistry(Protocol):
    """"哪个agent能处理哪种能力"这件事的抽象接口。"""

    def register(self, agent_name: str, capability: AgentCapability) -> None:
        """登记某个agent具备某项能力。"""
        ...

    def find_agents_for(self, capability_name: str) -> list[str]:
        """返回所有登记了指定能力的agent名称列表。"""
        ...

    def get_capability(self, agent_name: str, capability_name: str) -> AgentCapability | None:
        """查询某个agent的某项能力的具体描述；未登记则返回`None`。"""
        ...


@runtime_checkable
class AgentTransport(Protocol):
    """"如何真正把一个任务发给目标agent并拿到结果"这件事的抽象接口。

    默认提供进程内直接函数调用的实现（`ainative_a2a.transport.InProcessTransport`）；
    真实项目如果需要跨进程/跨服务委派，实现本协议接入HTTP/消息队列等。
    """

    async def send(self, agent_name: str, task: A2ATask) -> A2AResult:
        """把`task`发给`agent_name`，返回其执行结果。"""
        ...
