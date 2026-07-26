"""`ainative_core.protocols.PromptStore`的内存版实现 + 加载入口函数。

改造自真实项目里验证过的Prompt A/B路由设计：多变体时按`traffic_pct`加权
确定性哈希路由，同一`thread_id`始终路由到同一变体（粘性路由，避免运营
调整流量占比后正在进行中的会话被重新路由，破坏实验粘性承诺）。

原版直接耦合SQLAlchemy ORM查询 + MongoDB持久化决策记录，这里把存储
职责收敛到`PromptStore` Protocol（`ainative_core.protocols`），
`load_prompt()`只依赖这个协议接口，不关心具体存储实现；本包提供的
`InMemoryPromptStore`默认实现可以直接用于demo/测试，真实项目接入时
实现同一协议接入Postgres/MongoDB等即可。
"""

# 这一行是Python的"未来特性"声明：让下面所有类型注解都不需要在写代码的
# 时候真正被求值——Python解释器只把它们当作字符串暂存，等真正需要检查
# 类型时（比如IDE/mypy）才去解析。好处是像`list[PromptVariant]`这种写法，
# 在更旧版本的Python里也能正常工作，不会因为提前求值而报错。
from __future__ import annotations

# hashlib是Python标准库自带的、专门用来做"哈希"计算的模块。哈希可以
# 理解成一台"绞肉机"：不管你喂给它多长、多复杂的输入（这里是一个字符串），
# 它都会吐出一段长度固定、看起来毫无规律的输出（一串十六进制数字）。
# 关键特性是：同样的输入永远得到同样的输出（确定性），但即使输入只改动
# 一个字符，输出也会变得面目全非（看起来像随机的）。这正好用来实现
# "同一个thread_id每次都应该落到同一个分组"，同时又希望不同thread_id
# 尽量均匀地分散到各个分组里，不扎堆。
import hashlib

# logging是Python标准库自带的日志模块——比起用`print()`直接打印，
# 用logging记录的信息可以统一控制"要不要显示""显示到哪里（终端/文件）"
# "标记这是普通信息还是警告"等，更适合正式项目长期使用。
import logging

# 从ainative-core包（本框架的地基包）里，导入两个已经定义好的类型：
# PromptStore——一份"协议"（Protocol，即接口约定），规定了"想要管理
#   Prompt版本和A/B路由的存储对象，必须具备哪些方法"，具体怎么存储
#   （内存/数据库）由实现者自己决定，本文件下面的InMemoryPromptStore
#   就是它的一种具体实现。
# PromptVariant——"一个Prompt的一个具体版本"这件事的数据结构，包含
#   变体标识、内容文本、流量占比、版本号、是否生效等字段。
from ainative_core.protocols import PromptStore, PromptVariant

# `logging.getLogger(__name__)` 创建（或复用）一个"记录器"对象，
# `__name__`是Python内置的一个特殊变量，自动等于当前这个文件的模块路径
# （比如"ainative_prompt.store"）——用模块名当作记录器名字，是Python
# 日志系统的标准做法，方便以后查日志时能一眼看出这条记录是哪个文件打的。
logger = logging.getLogger(__name__)


def ab_select_deterministic(variants: list[PromptVariant], thread_id: str | None) -> PromptVariant:
    """基于thread_id哈希的确定性A/B变体选择，同一thread_id始终选中同一变体。

    **已知设计特性（非bug，需明确告知使用方）**：`thread_id`为`None`时使用固定
    seed`"anonymous"`，所有匿名调用会被路由到*同一个*具体变体，而不是随机分散。
    如果匿名（无thread_id）调用在业务里占比不低，这部分流量不会参与真正的随机
    分流，统计A/B显著性时应该把匿名流量单独处理或排除，不要当作已经过公平
    随机分流的样本。

    Raises:
        ValueError: `variants`为空列表——本函数是公开API（不只是`load_prompt()`
            内部一个已经保证非空调用的辅助函数），单独调用时必须对空输入给出
            清晰的错误提示，而不是让`variants[0]`触发一个和"变体列表为空"这个
            真实原因毫无关联的`IndexError`。
    """
    # `not variants`——空列表在Python里等价于False，所以`not []`是True。
    # 这一行的意思是"如果传进来的variants是空列表，就直接报错，不再往下
    # 执行"，报错信息里写清楚原因，方便调用方一眼看出问题出在哪。
    if not variants:
        raise ValueError("ab_select_deterministic requires a non-empty variants list")
    # `sum(v.traffic_pct for v in variants)` 是一个"生成器表达式"：
    # 依次取出variants里的每一个变体v，取出它的traffic_pct（流量百分比）
    # 字段，最后把所有变体的这个数值加总起来。比如两个变体各占50%，
    # total就是100。
    total = sum(v.traffic_pct for v in variants)
    # 如果所有变体的流量占比加起来是0或者负数（不合理的配置数据），
    # 没办法按比例正常分流，就直接兜底返回第一个变体，避免后面的除法
    # 出现"除以0"这类错误。
    if total <= 0:
        return variants[0]
    # `thread_id or "anonymous"`——如果thread_id是None（或空字符串这种
    # "假值"），就用固定字符串"anonymous"代替；否则用真实传入的thread_id。
    # 这就是模块docstring里提到的"已知设计特性"：所有匿名调用共用同一个
    # seed，因此会被哈希到同一个变体，而不是各自随机分散。
    seed = thread_id or "anonymous"
    # 这一行是本函数的核心："把seed这个字符串，转换成一个可以用来做
    # 比例判断的数字"，分三步：
    # 1. `seed.encode()`——把字符串编码成字节（bytes），因为哈希函数
    #    只能处理字节数据，不能直接处理Python的字符串对象。
    # 2. `hashlib.md5(..., usedforsecurity=False)`——用MD5算法计算哈希。
    #    `usedforsecurity=False`是明确告诉Python："这里用MD5不是为了
    #    加密安全场景（比如密码存储），只是想要一个确定性的哈希分布，
    #    不需要MD5在安全性上的强保证"——一些更严格的Python构建版本
    #    如果不声明这一点，可能会因为MD5已知不适合安全场景而拒绝运行
    #    或发出警告。
    # 3. `.hexdigest()`——把哈希结果转换成一串十六进制字符（0-9,a-f
    #    组成的字符串）；`int(..., 16)`再把这串十六进制字符串解析成
    #    一个巨大的十进制整数——数字越大不重要，重要的是"同一个seed
    #    永远得到同一个数字"。
    h = int(hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest(), 16)
    # `h % 10000`——对h取"模"（求除以10000之后的余数），结果永远落在
    # 0到9999之间；再除以10000.0变成一个0到1之间的小数（浮点数除法，
    # 除数带小数点保证得到的是小数而不是整数除法）。这就把"任意巨大的
    # 哈希整数"转换成了"0~1之间均匀分布的一个小数"，可以理解成"这个
    # thread_id在0~1这条数轴上落在哪个位置"。再乘以total（所有变体
    # 流量占比总和），就是"这个thread_id对应的位置，落在0~total这个
    # 更大区间的哪个点"，用来跟下面每个变体累计占用的区间做比较。
    threshold = (h % 10000) / 10000.0 * total
    # 累加器，从0开始，逐个变体往上叠加它们各自的traffic_pct，用来
    # 判断threshold这个点具体落在哪个变体的"势力范围"内。
    cumulative = 0.0
    # 依次遍历每一个变体（保持传入列表的原始顺序）。
    for v in variants:
        # 把当前变体的流量占比加到累计值上——比如两个变体分别是30%和
        # 70%，遍历到第一个变体后cumulative变成30，遍历到第二个变体后
        # cumulative变成100。
        cumulative += v.traffic_pct
        # 只要threshold这个点还落在"目前累计到的范围之内"，就说明这个
        # 点应该归属于当前这个变体——类似于把0~100这条数轴按每个变体的
        # 占比切成几段，看threshold具体落在哪一段里。
        if threshold < cumulative:
            return v
    # 理论上，只要total算对了，上面的循环一定会在中途通过`return v`
    # 返回，走不到这里；但为了防止浮点数运算中可能出现的极其微小的
    # 精度误差（导致cumulative最终没有严格大于threshold），这里保留
    # 一个兜底：返回列表里最后一个变体，保证函数无论如何都会返回一个
    # 有效结果，而不是"什么都不返回"。
    return variants[-1]


class InMemoryPromptStore:
    """`PromptStore`的内存版实现——用普通dict存储变体和粘性路由决策。"""

    # __init__ 是Python类的"构造函数"——每次用`InMemoryPromptStore()`
    # 创建一个新实例时，Python会自动调用这个方法，进行一些初始化设置。
    def __init__(self) -> None:
        # `self._variants` 这个属性用来存"所有已经保存过的Prompt变体"。
        # 类型注解`dict[tuple[str, str], dict[str, PromptVariant]]`拆开看：
        # - 最外层是一个dict（字典），
        # - key是`tuple[str, str]`——一个由两个字符串组成的元组，也就是
        #   `(agent_name, prompt_key)`这一对，用来定位"哪个agent的哪个
        #   prompt位置"；
        # - value又是一个dict，key是变体标识字符串（比如"a"/"default"），
        #   value是对应的PromptVariant对象本身。
        # 这样两层嵌套字典，就能表达"每个(agent, prompt_key)组合下面，
        # 可以有好几个不同variant标识的变体，各自存一份"这种结构。
        self._variants: dict[tuple[str, str], dict[str, PromptVariant]] = {}
        # `self._sticky` 用来记录"粘性路由决策"——也就是"某个具体的
        # thread_id之前被分流到了哪个变体"。key是三元组
        # `(agent_name, prompt_key, thread_id)`，value是变体标识字符串。
        self._sticky: dict[tuple[str, str, str], str] = {}

    # 方法名前面的`async`表示这是一个"异步方法"——调用时要写成
    # `await store.get_active_variants(...)`。虽然这个内存版实现内部
    # 其实没有任何真正需要"等待"的操作（不像访问数据库那样需要网络
    # IO），但因为它要满足`PromptStore`这份协议的方法签名（协议里
    # 声明的就是async方法，为了兼容真实的数据库实现，这些方法几乎必然
    # 要等待网络/磁盘），所以这里也统一写成async，保持和协议签名一致，
    # 调用方不需要区分"这次拿到的是内存版还是数据库版"，统一用`await`
    # 调用即可。
    async def get_active_variants(self, agent_name: str, prompt_key: str) -> list[PromptVariant]:
        # `self._variants.get((agent_name, prompt_key), {})`——尝试按
        # 这一对key去查字典，查不到（这个agent+prompt_key组合从来没
        # 保存过任何变体）就用一个空字典`{}`兜底，避免直接报KeyError。
        bucket = self._variants.get((agent_name, prompt_key), {})
        # `[v for v in bucket.values() if v.is_active]`是一个"列表推导式"：
        # 依次取出bucket这个字典里的每一个value（也就是每一个
        # PromptVariant对象，命名为v），只保留满足`v.is_active`为True
        # 条件的那些，最终组装成一个新列表返回。效果就是"只返回当前
        # 处于生效状态的变体，跳过已经被停用的"。
        return [v for v in bucket.values() if v.is_active]

    async def get_sticky_decision(self, agent_name: str, prompt_key: str, thread_id: str) -> str | None:
        # 同样用`.get(key, 默认值)`的安全读取方式——查不到这个thread_id
        # 之前的分流记录，就返回None，表示"还没有过历史决策，需要重新
        # 走一次分流逻辑"。
        return self._sticky.get((agent_name, prompt_key, thread_id))

    async def record_decision(self, agent_name: str, prompt_key: str, thread_id: str, variant: str) -> None:
        # 直接往字典里写入/覆盖这一条"这个thread_id这次被分到了哪个
        # 变体"的记录——如果这个thread_id之前已经记录过，这里会覆盖
        # 成新值（正常业务流程里不应该发生同一个thread_id被反复记录成
        # 不同变体的情况，但字典赋值本身不会阻止你这么做）。
        self._sticky[(agent_name, prompt_key, thread_id)] = variant

    async def save_variant(self, agent_name: str, prompt_key: str, variant: PromptVariant) -> None:
        # `dict.setdefault(key, 默认值)`的行为是："如果key已经存在，
        # 就返回它当前对应的value（什么都不改动）；如果key不存在，就
        # 先把key设置成默认值，再返回这个刚设置好的默认值"。这里的效果
        # 是："如果这个(agent_name, prompt_key)组合是第一次出现，先给它
        # 建一个空字典{}，否则直接复用已有的那个字典"——避免每次保存
        # 变体前都要手写一遍"不存在就先初始化"的if判断。
        bucket = self._variants.setdefault((agent_name, prompt_key), {})
        # 把这个具体的variant对象，按它自己的variant标识（比如"a"）
        # 存进bucket字典里——如果这个标识之前已经存过一次，这里会直接
        # 覆盖成新传入的variant（也就是实现了"更新"这个变体的效果）。
        bucket[variant.variant] = variant


async def load_prompt(
    store: PromptStore,
    agent_name: str,
    prompt_key: str = "system_prompt",
    *,
    default: str = "",
    thread_id: str | None = None,
) -> str:
    """加载Prompt内容，支持多变体A/B流量路由，Store无记录时回退到`default`。

    Args:
        store: 具体的`PromptStore`实现（内存版/真实数据库版均可）。
        agent_name: Agent标识。
        prompt_key: 提示词键名，默认"system_prompt"。
        default: Store无记录时的代码硬编码默认值。
        thread_id: 可选，用于A/B粘性路由；为`None`时所有调用被路由到同一固定变体
            （见`ab_select_deterministic`文档字符串里的说明）。
    """
    # 先去问store："这个agent的这个prompt_key，目前有哪些处于生效状态
    # 的变体？"——不管store底层是内存字典还是真实数据库，这里统一用
    # `await`等待结果返回（真实数据库实现这一步很可能涉及网络请求）。
    variants = await store.get_active_variants(agent_name, prompt_key)

    # 一个变体都没有（这个位置从来没配置过Prompt，或者配置过的全部
    # 被停用了），就没办法做任何路由，直接返回调用方传入的硬编码默认值。
    if not variants:
        logger.info("Using default prompt for %s:%s", agent_name, prompt_key)
        return default

    # 只有一个生效变体时，不需要走任何A/B分流逻辑（反正只有一个选择），
    # 直接返回它的内容即可，省去后续的哈希计算和粘性记录读写。
    if len(variants) == 1:
        variant = variants[0]
        logger.info("Loaded prompt %s:%s variant=%s v%d", agent_name, prompt_key, variant.variant, variant.version)
        return variant.content

    # `{v.variant for v in variants}`是一个"集合推导式"（跟列表推导式
    # 很像，只是外层用花括号而不是方括号）——把所有生效变体的variant
    # 标识收集成一个"集合"（set，元素不重复、判断"是否在里面"的速度
    # 比列表更快）。这里用来快速检查"某个历史粘性决策记录的变体标识，
    # 是不是仍然属于当前生效变体之一"。
    active_names = {v.variant for v in variants}
    # 这一行是一个"条件表达式"（也叫三元表达式），格式是
    # `值A if 条件 else 值B`：如果thread_id不是None（说明调用方明确
    # 提供了thread_id，可以查粘性路由记录），就真的去问store"这个
    # thread_id之前有没有被分流过"；如果thread_id是None（匿名调用），
    # 就直接跳过查询，用None代替——因为匿名场景是所有调用共享固定
    # seed的确定性哈希（见ab_select_deterministic的说明），本来就不需要
    # 依赖粘性记录。
    sticky_variant_name = (
        await store.get_sticky_decision(agent_name, prompt_key, thread_id) if thread_id else None
    )
    # 只有同时满足两个条件才信任这条历史粘性记录：
    # 1. `sticky_variant_name is not None`——store里确实查到了历史记录；
    # 2. `sticky_variant_name in active_names`——这个记录里的变体标识，
    #    现在仍然是生效变体之一（防止运营下线了某个变体之后，历史记录
    #    还指向一个已经不存在/被停用的变体，那种情况应该重新走一次
    #    正常分流，而不是继续沿用一个已经失效的选择）。
    if sticky_variant_name is not None and sticky_variant_name in active_names:
        # `next(表达式 for v in variants if 条件)`是"生成器表达式+next()"
        # 的组合写法：生成器表达式本身会依次产出满足条件的v，`next(...)`
        # 只取第一个满足条件的结果就立刻停止（不会继续遍历剩下的元素），
        # 效果类似"在列表里查找第一个variant字段等于sticky_variant_name
        # 的对象"。
        selected = next(v for v in variants if v.variant == sticky_variant_name)
        logger.info(
            "A/B sticky route %s:%s → variant=%s (reused persisted decision) thread=%s",
            agent_name, prompt_key, selected.variant, thread_id or "n/a",
        )
    else:
        # 没有可信的历史粘性记录（要么是第一次遇到这个thread_id，要么
        # 是匿名调用，要么是历史记录指向的变体已经失效），就调用上面
        # 定义的确定性哈希函数，重新走一次分流决策。
        selected = ab_select_deterministic(variants, thread_id)
        logger.info(
            "A/B route %s:%s → variant=%s (%.0f%% traffic) thread=%s",
            agent_name, prompt_key, selected.variant, selected.traffic_pct, thread_id or "n/a",
        )
        # 只有真实提供了thread_id（不是匿名调用）时，才把这次的分流
        # 结果记录下来，供以后同一个thread_id的调用复用（实现"粘性"）。
        # 匿名调用不需要记录，因为它每次都会重新哈希到同一个固定结果，
        # 记不记录粘性决策不影响最终选中哪个变体。
        if thread_id:
            await store.record_decision(agent_name, prompt_key, thread_id, selected.variant)

    # 不管是走了"复用历史粘性决策"分支，还是"重新分流"分支，最终都要
    # 返回选中变体的实际Prompt文本内容。
    return selected.content
