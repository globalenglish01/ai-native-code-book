# 第12章 —— Prompt是需要版本管理的资产

代码位置：`packages/ainative-prompt/src/ainative_prompt/store.py`

## 一个常见的坏习惯

大部分人写Agent的第一版代码，Prompt都是直接写死在代码里的字符串：

```python
# 直接把Prompt文本写死成一个模块级常量——每次想改一个字，
# 都要改代码、走一遍完整的代码发布流程。
SYSTEM_PROMPT = "你是一个专业的客服助手，请礼貌地回答用户问题……"
```

这样做的问题是：**Prompt的每一次调整，都要跟代码一起走完整的发布流程**——想微调一句话，都得走代码审查、测试、部署。而且没有办法知道"上周把这句话从A改成B之后，用户满意度是不是真的提升了"，因为没有版本、没有对照。

`ainative-prompt`把Prompt当成一种**独立于代码、需要被版本管理和灰度发布的资产**来对待，这一章讲它的核心数据结构。

## `PromptVariant`：一个版本长什么样

```python
@dataclass(frozen=True)
class PromptVariant:
    variant: str        # 变体标识，比如"default"、"v2"
    content: str         # Prompt文本内容
    traffic_pct: int     # 应该分到的流量百分比（0-100）
    version: int          # 版本号，每次修改自增
    is_active: bool = True   # 这个变体目前是否生效，可以被下线而不删除记录
```

一个Prompt的"位置"由`(agent_name, prompt_key)`这一对确定（比如"客服Agent的system_prompt"），这个位置下可以同时存在多个`PromptVariant`——这正是做A/B测试的基础：同一个位置，一部分流量走`variant="default"`，另一部分流量走`variant="v2"`，观察哪个效果更好。

## 粘性路由：为什么"同一个用户"不能被反复重新分流

假设你把流量比例设成default 70%、v2 30%，一个用户开始一段多轮对话，前几轮被分到了v2，如果每一轮对话都重新做一次随机分流，这个用户可能在对话进行到一半时，突然被换到了default——这会让整个对话体验变得不一致（前后风格、行为可能有差异），也会破坏A/B测试本身的有效性（同一个用户的多轮反馈，被混进了两个不同的实验组里）。

`ainative-prompt`的解法是"粘性路由"（sticky routing）：**用`thread_id`（对话/会话标识）作为哈希输入，同一个`thread_id`永远得到同一个分流结果**：

```python
def ab_select_deterministic(variants: list[PromptVariant], thread_id: str | None) -> PromptVariant:
    ...
    # or——thread_id如果是None（没有会话标识），就用固定字符串
    # "anonymous"顶替；这一行本身就是下面要讲的"匿名陷阱"的根源。
    seed = thread_id or "anonymous"
    # seed.encode()——把字符串转成字节序列（哈希函数处理的是字节）；
    # hashlib.md5(...).hexdigest()算出一段固定长度的十六进制字符串；
    # int(..., 16)把这串十六进制文本转换成一个巨大的整数。
    h = int(hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest(), 16)
    # h % 10000——取模运算，把这个巨大整数"压缩"到0-9999这个范围；
    # 再除以10000.0变成0到1之间的小数，乘以total（流量总数）得到
    # 一个"这次请求落在哪个刻度"的具体数值。
    threshold = (h % 10000) / 10000.0 * total
    cumulative = 0.0   # 累计流量占比，从0开始逐步往上叠加
    for v in variants:
        cumulative += v.traffic_pct
        # 只要threshold落在"目前为止累计到的区间"之内，就选中这个变体。
        if threshold < cumulative:
            return v
    return variants[-1]   # 兜底：理论上不会走到这里，除非累计比例算错了
```

核心机制是"哈希+累计区间判断"：把`thread_id`这个字符串，通过MD5哈希算法转换成一个巨大的数字，再对10000取模、除以10000，得到一个**0到1之间、看起来随机但完全确定性**的小数（同样的`thread_id`永远得到同样的小数）。再把每个变体的流量占比依次累加成一个"势力范围"区间（比如变体A占30%，对应区间`[0, 30)`；变体B占70%，对应区间`[30, 100)`），看这个小数（乘以总流量后）落在哪个区间里，就选中对应的变体。

这个设计巧妙在哪？**它不需要"记住"任何历史分流决策就能保证一致性**——同一个`thread_id`，无论你调用多少次这个函数，因为哈希算法是确定性的，永远会算出同一个结果。这是"粘性"最底层的保证；`InMemoryPromptStore`额外维护的`_sticky`字典（记录粘性决策）是一层锦上添花的优化和显式记录，不是粘性本身的必要条件（详见`load_prompt`函数）。

## 一个"已知设计特性"：匿名调用的哈希陷阱

代码的docstring里坦诚地写明了一个容易被误用的细节：

```python
# 所有thread_id为None的调用，都会用完全相同的字符串"anonymous"
# 去算哈希——意味着它们全都会被分到同一个变体，不是均匀随机分散的。
seed = thread_id or "anonymous"
```

**当`thread_id`是`None`（匿名调用）时，所有匿名请求都会用同一个固定字符串`"anonymous"`作为哈希种子**——这意味着所有匿名流量会被哈希到**同一个**具体变体，而不是像有`thread_id`的流量那样均匀分散。这不是bug，而是这个函数明确的设计边界：docstring特意标注了"如果匿名调用在业务里占比不低，统计A/B显著性时应该把匿名流量单独处理或排除，不要当作已经过公平随机分流的样本"。

这一节想强调的道理是：**一份好的技术文档，不只要写"这个函数做什么"，还要写"这个函数在什么边界情况下的行为，可能和你的直觉不一致"**。如果没有这句说明，一个不知情的工程师可能会拿匿名流量的分布数据去做统计显著性检验，得出一个看起来"正常"、实际上完全没有意义的结论（因为匿名流量压根没有真正被随机分流过）。

## `load_prompt`：把"粘性"和"哈希"组合起来

真正对外提供服务的入口函数是`load_prompt`，它按顺序做了几件事：

1. 查询这个位置当前有哪些**生效**的变体（`get_active_variants`）——没有就用代码里的硬编码默认值兜底。
2. 只有一个生效变体时，直接返回它，不做任何分流计算。
3. 多个变体时，先查有没有历史粘性决策，**并且检查这个历史决策指向的变体是否仍然生效**（`sticky_variant_name in active_names`）——防止运营下线了某个变体之后，历史记录还顽固地指向一个已经不存在的选项。
4. 没有可信的历史决策，就调用`ab_select_deterministic`重新分流，并且（非匿名时）把这次决策记录下来供未来复用。

第3步这个"检查历史决策是否仍然有效"的细节，是很容易被忽略、但真实场景里一定会发生的情况——运营团队随时可能调整流量配置、下线某个效果不好的变体，系统必须能优雅地处理"历史记录已经过时"这种情况，而不是死板地沿用一个已经不存在的选择。

## 本章小结

- Prompt应该被当作独立于代码的、需要版本管理和灰度发布的资产，而不是硬编码在代码里的字符串。
- 粘性路由通过对`thread_id`做确定性哈希实现，同一个标识永远得到同一个分流结果，不需要额外的状态记录就能保证一致性。
- 匿名调用（`thread_id=None`）会退化成所有请求共享同一个哈希种子，导致它们全部落在同一个变体上——这是一个需要被文档明确标注、否则容易被误用的边界行为。
- 复用历史粘性决策前，必须验证该决策指向的变体是否仍然生效，避免沿用一个已经被下线的选项。

## 动手做

```python
from ainative_prompt.store import ab_select_deterministic
from ainative_core.protocols import PromptVariant

# 两个变体，a占30%流量，b占70%流量。
variants = [
    PromptVariant(variant="a", content="版本A", traffic_pct=30, version=1),
    PromptVariant(variant="b", content="版本B", traffic_pct=70, version=1),
]

# 依次用几个不同的会话标识去测试分流结果，最后两个None模拟匿名调用。
for tid in ["user-1", "user-2", "user-3", None, None]:
    print(tid, "→", ab_select_deterministic(variants, tid).variant)
```

多跑几次这段代码，验证"同一个thread_id每次结果都一样"，以及"两次`None`是不是也总是选中同一个变体"。

## 面试可能会问

**问：如何设计一个多轮对话场景下的A/B测试系统，保证同一个用户的体验前后一致？**

答题思路：核心概念是"粘性路由"——用一个稳定的用户/会话标识做确定性哈希，而不是每次请求都重新随机；解释确定性哈希"同样输入永远得到同样输出"这个性质如何天然满足一致性要求。加分项：提到"匿名流量没有稳定标识可用，需要单独设计降级策略（比如共享固定种子），并且在统计分析时把这部分流量排除或单独处理"，这体现了对边界情况的完整考虑，而不只是讲了"主流程"。
