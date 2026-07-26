"""内容新鲜度感知的缓存键——文档更新/删除后旧缓存必须自然失效。

改造自checklist A类"引用溯源与内容新鲜度子类"真实发现的反面案例（对
`anything-chat-rag`真实代码问答缓存设计提炼）：真实项目的问答缓存键
只由查询内容的哈希构成，不包含任何文档版本/ID关联信息，导致文档删除/
更新后，基于旧内容生成的缓存答案会持续被提供给未来完全不知情的用户，
且缓存键设计上就无法追溯到"这条缓存是从哪份文档衍生的"，删除时也就
无从清理。

正确设计原则：缓存键必须显式包含"参与生成这份答案的每份文档当前的
版本标识"，而不是只哈希查询文本本身——文档版本变化时，同样的查询文本
会自然产生不同的缓存键，旧缓存条目不会被错误复用，也天然具备"按
doc_id关联清理"的能力（不需要额外的删除时清理机制）。
"""

from __future__ import annotations

import hashlib
import json


def build_freshness_aware_cache_key(query: str, *, doc_versions: dict[str, str]) -> str:
    """构造缓存键——显式纳入`doc_versions`（`{doc_id: version_or_hash}`），
    而不是只哈希`query`本身。

    Args:
        query: 用户查询文本。
        doc_versions: 参与生成这次回答的每份文档，当前的版本标识（可以是
            版本号、内容哈希、或最后更新时间戳，只要文档内容变化时这个
            值也会变化）。按key排序后参与哈希计算，保证"同一组文档、
            不同顺序传入"仍然产生相同的缓存键。

    Returns:
        一个稳定的缓存键字符串——`query`不变但任意一份`doc_versions`
        变化，都会产生不同的键，天然让旧缓存失效，不需要额外的删除时
        清理机制。

    真实bug背景：早期实现用`"|".join(f"{k}:{v}")`这类手工拼接分隔符的
    方式构造哈希输入，如果`doc_id`或版本值本身含有`:`/`|`这类分隔符
    字符（真实场景下doc_id经常是路径/哈希/带命名空间的字符串，并不罕见），
    两组完全不同的`doc_versions`会拼接出一模一样的字符串，产生哈希碰撞——
    比如`{"a": "1", "b": "2"}`和`{"a": "1|b:2"}`会拼出相同的`"a:1|b:2"`。
    改用`json.dumps`结构化序列化：JSON会给每个字符串正确加引号/转义，
    `"a:b"`和`"a"`在JSON里永远是两个不同的token，不可能因为内容恰好
    包含分隔符字符就被混淆成同一个序列化结果。
    """
    payload = json.dumps({"query": query, "doc_versions": doc_versions}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
