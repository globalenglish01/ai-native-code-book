"""检索增强生成（RAG）查询流水线——分阶段执行 + 引用溯源 + 诚实的空结果处理。

改造自checklist A类"RAG端到端流程设计子类"与"引用溯源与内容新鲜度子类"
真实设计要点（对`anything-chat-rag`真实代码`operate.py`的`kg_query`/
`naive_query`查询流水线、`utils.py`的引用列表生成设计提炼）：

1. **拆解成明确阶段**：检索资料 -> 组织上下文 -> 生成回答，而不是把用户
   原始问题和所有可能相关的资料一股脑丢给AI自己处理。
2. **检索无结果时诚实拒答**：完全没有检索到相关内容时，在早期就明确
   返回"没有足够信息"，而不是让AI基于空上下文强行生成一个可能编造的
   答案——这是本流水线的默认行为，不是可选项。
3. **引用溯源贯穿全程**：从"检索到具体片段"到"回答携带引用信息"要有
   完整链路，而不是检索时打了标签、最终回答却没有把这份信息带出来。
"""

# 什么是"RAG"（检索增强生成，Retrieval-Augmented Generation）？让大语言
# 模型回答问题时，不是仅凭它训练时记住的知识凭空作答，而是先从一个
# 专门准备的知识库（比如公司内部文档）里，检索出和问题最相关的一些
# 片段，再把这些片段作为"参考资料"连同问题一起交给模型生成回答——这样
# 回答能有据可查（可以标注"这句话来自哪份文档"），也能覆盖模型训练
# 数据里没有的、更新更及时的知识。这个文件就是这套"检索->生成"完整
# 流程的具体实现。

from __future__ import annotations

from collections.abc import Awaitable, Callable

# field——配合@dataclass使用，专门处理"默认值本身是可变对象（比如字典/
# 列表）"这种场景，见下面`InMemoryRetriever.documents`字段定义处的详细
# 解释。
from dataclasses import dataclass, field

# Protocol/runtime_checkable——用来定义"结构化接口"的工具，见下面
# `Retriever`类定义处详细解释。
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RetrievedChunk:
    """一次检索命中的片段——`doc_id`+`chunk_id`是引用溯源的最小定位单元。"""
    # ↑ `frozen=True`——冻结，实例创建后字段不可修改。一次检索命中的
    #   结果代表某个时间点的既成事实，不应该在流转过程中被意外改动。

    doc_id: str
    # ↑ 这个片段所属的文档标识。
    chunk_id: str
    # ↑ 这个片段在其所属文档内部的具体编号——两者组合起来，才能唯一
    #   定位到"是哪份文档的哪一段"，这是实现"引用溯源"（回答能指出
    #   具体依据来自哪里）的最小信息单元。
    text: str
    # ↑ 这个片段的实际文字内容。
    score: float
    # ↑ 这个片段在这次检索中的相关性打分——分数越高，通常认为越相关。


@dataclass(frozen=True)
class Citation:
    """最终回答里携带的一条引用——从`RetrievedChunk`直接转换而来，
    不是回答生成之后才另外拼凑的信息。"""

    doc_id: str
    chunk_id: str


@dataclass(frozen=True)
class RagAnswer:
    text: str
    # ↑ 最终展示给用户的回答文本。
    citations: list[Citation]
    # ↑ 支撑这个回答的引用列表——每一条对应检索阶段用到的一个具体片段。
    grounded: bool
    """`False`表示检索阶段完全没有命中任何相关内容，`text`是诚实的
    "没有足够信息"提示，而不是模型编造的答案——调用方应该用这个字段
    区分"真的有依据的回答"和"拒答提示"，不要只看`text`是否非空。"""
    # ↑ "grounded"字面意思是"有依据的/落地的"——这个字段就是本模块
    #   设计原则第2条的具体体现：调用方（比如前端界面）应该优先检查
    #   `grounded`是True还是False，来决定要不要把这条回答当作"真正
    #   有资料支撑的答案"展示，而不是简单地看`text`字符串是否为空——
    #   因为`False`时`text`同样是一段非空的、有意义的提示文字。


NO_RELEVANT_CONTENT_MESSAGE = "I don't have enough information in the knowledge base to answer this question."
# ↑ 检索完全没有命中任何内容时，统一使用的固定提示文案——写成模块级
#   常量，方便调用方（比如前端）需要对比/识别这句特定文案时直接引用
#   同一个值，也方便以后统一修改措辞时只改这一处。


@runtime_checkable
# ↑ `Protocol`（下面`class Retriever(Protocol)`）是Python的"结构化
#   接口"写法：只要一个对象具备Protocol里声明的方法（这里是
#   `retrieve`），不管它是不是显式继承自`Retriever`，都能被当作
#   "符合这个接口"使用——不需要像传统面向对象语言那样必须写
#   `class MyRetriever(Retriever)`显式声明继承关系，这叫"鸭子类型"
#   （duck typing：只要长得像鸭子、叫得像鸭子，就当作是鸭子）。
#   `@runtime_checkable`这个装饰器额外允许在运行时用
#   `isinstance(obj, Retriever)`去检查"这个对象是否具备Retriever要求
#   的方法"，普通的Protocol默认不支持这种运行时检查，只能给静态类型
#   检查工具（比如mypy）看。
class Retriever(Protocol):
    """"怎么把一个查询变成一批候选片段"这件事的抽象接口——具体是向量
    检索/关键词检索/混合检索由调用方实现，本流水线只关心拿到的结果。"""

    async def retrieve(self, query: str) -> list[RetrievedChunk]: ...
    # ↑ 只声明方法签名，用`...`（省略号）作为函数体，表示"这里不提供
    #   具体实现，只是描述这个方法应该长什么样"——任何一个类，只要
    #   实现了一个同名同签名的`async def retrieve(self, query: str)
    #   -> list[RetrievedChunk]`方法，就自动被视为满足`Retriever`这个
    #   接口，可以传给下面的`run_rag_query`使用。


async def run_rag_query(
    query: str,
    *,
    retriever: Retriever,
    generate_fn: Callable[[str, str], Awaitable[str]],
    max_context_chunks: int = 5,
) -> RagAnswer:
    """完整的RAG查询流水线：检索 -> （无结果则诚实拒答）-> 组织上下文
    -> 生成回答 -> 附带引用。

    Args:
        query: 用户的原始查询。
        retriever: 检索阶段的具体实现。
        generate_fn: 接收`(query, context)`、返回生成回答文本的函数——
            具体调用哪个模型由调用方决定，本流水线不内置任何供应商依赖。
        max_context_chunks: 组织上下文时最多纳入的片段数量。
    """
    # `async def`——这是一个协程函数（异步函数），内部会用`await`去
    # 等待其他异步操作（检索、调用生成模型）真正完成，调用它的地方
    # 也需要写`await run_rag_query(...)`。参数列表里单独的`*`要求
    # `retriever`/`generate_fn`/`max_context_chunks`必须用"参数名=值"
    # 方式传入，避免调用时把几个参数的位置搞混。

    chunks = await retriever.retrieve(query)
    # ↑ 阶段一：检索。调用传入的`retriever`（不管具体是向量检索、
    #   关键词检索还是混合检索，本函数完全不关心，只要它符合上面
    #   `Retriever`协议）去拿到一批候选片段。
    if not chunks:
        # 阶段二（可能提前结束）：完全没有检索到任何相关内容——立刻
        # 诚实地返回"没有足够信息"，`grounded=False`明确标记这不是
        # 一份"有依据的"回答，绝不让流程继续往下走去生成一个可能
        # 编造的答案。这正是本模块docstring强调的默认行为，不是一个
        # 可以被绕过的可选项。
        return RagAnswer(text=NO_RELEVANT_CONTENT_MESSAGE, citations=[], grounded=False)

    selected = chunks[:max_context_chunks]
    # ↑ 阶段三：组织上下文。`chunks[:max_context_chunks]`是切片写法，
    #   只取检索结果里的前N个片段（N由调用方通过`max_context_chunks`
    #   指定），避免把全部检索结果不加限制地塞给生成模型——检索结果
    #   本身应该已经是按相关性排好序的，取前面几个通常就是最相关的。
    context = "\n\n".join(f"[{c.doc_id}#{c.chunk_id}] {c.text}" for c in selected)
    # ↑ 这是一个"生成器表达式"包在`"\n\n".join(...)`里的写法：
    #   `f"[{c.doc_id}#{c.chunk_id}] {c.text}"`对`selected`里的每个
    #   片段，生成一段带来源标记的文字（比如"[doc-1#doc-1-0] Python是
    #   一门编程语言"），`"\n\n".join(...)`再把这些片段用空行连接成
    #   一整段上下文文本——每个片段前面带的`[doc_id#chunk_id]`标记，
    #   是为了让生成模型在写回答时，有机会在文字里引用具体来源（不同
    #   项目对这个格式约定可能不同，这里选择了一种常见的方括号写法）。
    answer_text = await generate_fn(query, context)
    # ↑ 阶段四：生成回答。把原始查询和刚组织好的上下文，一起交给调用方
    #   传入的生成函数——具体调用哪个大语言模型、怎么构造提示词，都由
    #   调用方的`generate_fn`实现决定，本流水线本身不关心、不内置任何
    #   具体供应商的依赖，只负责"在正确的时机、传入正确的参数"去调用它。
    citations = [Citation(doc_id=c.doc_id, chunk_id=c.chunk_id) for c in selected]
    # ↑ 阶段五：附带引用。列表推导式——把这次真正用来生成回答的每个
    #   片段，转换成一条`Citation`记录。注意这里用的是`selected`（真正
    #   参与生成的那几个片段），而不是`chunks`（检索到的全部结果）——
    #   保证引用列表精确对应"回答实际依据了哪些内容"，不多不少，这正是
    #   本模块设计原则第3条"引用溯源贯穿全程"的具体体现。
    return RagAnswer(text=answer_text, citations=citations, grounded=True)
    # ↑ 走到这里说明确实检索到了内容、也真正生成了回答，`grounded=True`
    #   标记这是一份"有依据的"正常回答。


@dataclass
class InMemoryRetriever:
    """`Retriever`的内存版最小实现——按关键词子串匹配打分，仅供demo/测试
    使用，不代表真正的向量/混合检索能力。"""
    # ↑ 注意这里是`@dataclass`，没有加`frozen=True`——因为`documents`
    #   这份"知识库"内容在demo/测试场景下，调用方经常需要在创建实例
    #   之后继续往里添加/修改文档，所以这个类被设计成可变的，与前面
    #   `RetrievedChunk`/`Citation`/`RagAnswer`这些"代表某个既成事实、
    #   不应再被改动"的类形成对比。

    documents: dict[str, str] = field(default_factory=dict)
    """`{doc_id: full_text}`——demo用的极简"知识库"。"""
    # ↑ `field(default_factory=dict)`——如果只写`documents: dict = {}`，
    #   这个空字典会在类定义时被创建一次，之后所有`InMemoryRetriever`
    #   实例会共享同一个字典对象，一个实例修改了`documents`会意外影响
    #   到其他实例（Python里"可变默认值"的经典陷阱）。`field(
    #   default_factory=dict)`则是告诉dataclass："每次创建一个新实例
    #   时，都调用一次`dict()`重新生成一个全新的空字典"，保证每个
    #   `InMemoryRetriever`实例各自拥有独立、互不影响的`documents`。

    async def retrieve(self, query: str) -> list[RetrievedChunk]:
        # 这个方法的签名和上面`Retriever`协议要求的完全一致（同名、
        # 同参数、同返回类型），所以`InMemoryRetriever`的实例自动
        # 满足`Retriever`这个结构化接口，可以直接传给`run_rag_query`
        # 使用，不需要显式写`class InMemoryRetriever(Retriever)`。
        query_terms = query.lower().split()
        # ↑ `.lower()`把查询文本统一转成小写，`.split()`按空白字符
        #   切分成一个个单词——这样"Python"和"python"能被当作同一个
        #   词匹配，是一种简化的、大小写不敏感的关键词匹配准备工作。
        results = []
        for doc_id, text in self.documents.items():
            # `dict.items()`遍历字典时，同时拿到每一对的key（`doc_id`）
            # 和value（`text`）。
            text_lower = text.lower()
            score = sum(1 for term in query_terms if term in text_lower)
            # ↑ 这是一个"生成器表达式"包在`sum(...)`里的写法：对
            #   `query_terms`里的每个词`term`，检查它是否作为子串出现
            #   在这份文档的小写文本里，出现就贡献1分，`sum(...)`把
            #   所有贡献的1加起来，得到这份文档命中了多少个查询词——
            #   这是一种非常简化的打分方式（只看词是否出现、出现次数
            #   不影响分数，也不考虑语义相似度），仅用于demo/测试，
            #   不代表真正的向量/混合检索效果。
            if score > 0:
                # 只有命中至少一个查询词的文档才纳入结果——完全不相关
                # 的文档不应该出现在检索结果里。
                results.append(RetrievedChunk(doc_id=doc_id, chunk_id=f"{doc_id}-0", text=text, score=float(score)))
                # ↑ `chunk_id=f"{doc_id}-0"`——这个极简实现把每份文档
                #   整体当作唯一的一个片段（编号固定是"-0"），不做真正
                #   的分块处理（真正的分块逻辑在`chunking.py`里）。
        results.sort(key=lambda r: r.score, reverse=True)
        # ↑ `list.sort(...)`原地排序（直接修改`results`本身，不返回
        #   新列表）；`key=lambda r: r.score`表示"按每个`RetrievedChunk`
        #   的`score`字段排序"；`reverse=True`表示分数从高到低降序，
        #   保证最相关的文档排在结果最前面。
        return results
