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

from __future__ import annotations

import hashlib
import logging

from ainative_core.protocols import PromptStore, PromptVariant

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
    if not variants:
        raise ValueError("ab_select_deterministic requires a non-empty variants list")
    total = sum(v.traffic_pct for v in variants)
    if total <= 0:
        return variants[0]
    seed = thread_id or "anonymous"
    h = int(hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest(), 16)
    threshold = (h % 10000) / 10000.0 * total
    cumulative = 0.0
    for v in variants:
        cumulative += v.traffic_pct
        if threshold < cumulative:
            return v
    return variants[-1]


class InMemoryPromptStore:
    """`PromptStore`的内存版实现——用普通dict存储变体和粘性路由决策。"""

    def __init__(self) -> None:
        self._variants: dict[tuple[str, str], dict[str, PromptVariant]] = {}
        self._sticky: dict[tuple[str, str, str], str] = {}

    async def get_active_variants(self, agent_name: str, prompt_key: str) -> list[PromptVariant]:
        bucket = self._variants.get((agent_name, prompt_key), {})
        return [v for v in bucket.values() if v.is_active]

    async def get_sticky_decision(self, agent_name: str, prompt_key: str, thread_id: str) -> str | None:
        return self._sticky.get((agent_name, prompt_key, thread_id))

    async def record_decision(self, agent_name: str, prompt_key: str, thread_id: str, variant: str) -> None:
        self._sticky[(agent_name, prompt_key, thread_id)] = variant

    async def save_variant(self, agent_name: str, prompt_key: str, variant: PromptVariant) -> None:
        bucket = self._variants.setdefault((agent_name, prompt_key), {})
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
    variants = await store.get_active_variants(agent_name, prompt_key)

    if not variants:
        logger.info("Using default prompt for %s:%s", agent_name, prompt_key)
        return default

    if len(variants) == 1:
        variant = variants[0]
        logger.info("Loaded prompt %s:%s variant=%s v%d", agent_name, prompt_key, variant.variant, variant.version)
        return variant.content

    active_names = {v.variant for v in variants}
    sticky_variant_name = (
        await store.get_sticky_decision(agent_name, prompt_key, thread_id) if thread_id else None
    )
    if sticky_variant_name is not None and sticky_variant_name in active_names:
        selected = next(v for v in variants if v.variant == sticky_variant_name)
        logger.info(
            "A/B sticky route %s:%s → variant=%s (reused persisted decision) thread=%s",
            agent_name, prompt_key, selected.variant, thread_id or "n/a",
        )
    else:
        selected = ab_select_deterministic(variants, thread_id)
        logger.info(
            "A/B route %s:%s → variant=%s (%.0f%% traffic) thread=%s",
            agent_name, prompt_key, selected.variant, selected.traffic_pct, thread_id or "n/a",
        )
        if thread_id:
            await store.record_decision(agent_name, prompt_key, thread_id, selected.variant)

    return selected.content
