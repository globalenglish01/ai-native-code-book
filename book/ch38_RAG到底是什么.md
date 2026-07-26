# 第38章 —— RAG到底是什么

代码位置：`packages/ainative-rag/src/ainative_rag/pipeline.py`

## 一个真实会让用户失去信任的场景

假设你在做一个企业内部知识库问答机器人，接入了公司最新的报销政策文档。有一天，一位同事问机器人"出差住宿每晚补贴上限是多少"，而这份文档里其实**从来没有提到过住宿补贴**——它只写了交通和餐饮标准。

如果你的系统只是简单地把问题丢给大模型，模型很可能会"编"出一个听起来很合理的数字（比如"每晚300元"），因为大模型的本能是"尽力给出一个像样的回答"，而不是"诚实地说我不知道"。这个编造出来的数字一旦被同事当真去报销，后果可能是真金白银的损失，也会让所有人从此不再信任这个机器人——哪怕它在其他99次问答里都表现得完美无缺。

RAG（Retrieval-Augmented Generation，检索增强生成）要解决的核心问题，正是让大模型的回答"有据可查"：先从知识库里检索出真正相关的资料片段，把这些片段当作"参考资料"连同问题一起交给模型，让模型基于**真实存在的文字**来回答，而不是仅凭训练时记住的、可能过时或压根不存在的"印象"作答。这一章要看的`pipeline.py`，就是这套"检索→生成"完整流程的一个干净实现。

## 拆解成明确的阶段，而不是一股脑丢给AI

模块docstring里提出的第一条设计原则是：

> 拆解成明确阶段：检索资料 -> 组织上下文 -> 生成回答，而不是把用户原始问题和所有可能相关的资料一股脑丢给AI自己处理。

`run_rag_query`函数完整地体现了这个思路：

```python
async def run_rag_query(
    query: str,
    *,
    retriever: Retriever,
    generate_fn: Callable[[str, str], Awaitable[str]],
    max_context_chunks: int = 5,
) -> RagAnswer:
    chunks = await retriever.retrieve(query)
    if not chunks:
        return RagAnswer(text=NO_RELEVANT_CONTENT_MESSAGE, citations=[], grounded=False)

    selected = chunks[:max_context_chunks]
    context = "\n\n".join(f"[{c.doc_id}#{c.chunk_id}] {c.text}" for c in selected)
    answer_text = await generate_fn(query, context)
    citations = [Citation(doc_id=c.doc_id, chunk_id=c.chunk_id) for c in selected]
    return RagAnswer(text=answer_text, citations=citations, grounded=True)
```

五个阶段清清楚楚：检索（`retriever.retrieve`）→ 判断是否为空 → 组织上下文（拼接前N个片段）→ 生成回答（`generate_fn`）→ 附带引用（把片段转换成`Citation`）。这种"明确分阶段"的写法，好处在于**每一步都可以独立测试、独立替换**——你可以单独验证"检索阶段有没有正确返回结果"，不需要连带真的调用一次大模型；也可以把`retriever`换成向量检索、关键词检索或者混合检索的任意实现，`run_rag_query`本身完全不需要跟着改动。

## 检索无结果时，诚实地拒答

```python
if not chunks:
    return RagAnswer(text=NO_RELEVANT_CONTENT_MESSAGE, citations=[], grounded=False)
```

这是本章开头那个"编造住宿补贴数字"场景的直接解药。`NO_RELEVANT_CONTENT_MESSAGE`是一个写在模块顶层的固定常量文案（"I don't have enough information in the knowledge base to answer this question."），一旦检索阶段完全没有命中任何相关内容，流程会**立刻**在这里返回，绝不会让代码继续往下走到"组织上下文、调用生成模型"这一步——因为如果没有任何真实资料可以参考，生成模型唯一能做的就是凭空编造，这正是要不惜一切代价避免的结果。

模块docstring把这一点讲得很明确：

> 检索无结果时诚实拒答：完全没有检索到相关内容时，在早期就明确返回"没有足够信息"，而不是让AI基于空上下文强行生成一个可能编造的答案——这是本流水线的默认行为，不是可选项。

"默认行为，不是可选项"这几个字值得反复咀嚼——这不是一个调用方可以选择打开或关闭的开关，而是这套流水线从设计上就不允许绕过的行为。

## `grounded`字段：不要只看`text`是否非空来判断"这是不是一份真答案"

```python
@dataclass(frozen=True)
class RagAnswer:
    text: str
    citations: list[Citation]
    grounded: bool
```

这里有一个容易被忽略、但很重要的设计细节：**当检索无结果时，`text`字段依然是非空的**（它是`NO_RELEVANT_CONTENT_MESSAGE`这段有意义的提示文字），如果调用方（比如前端界面）只是简单地判断"`text`是否为空字符串"来决定要不要展示这条回答，会误把这条"拒答提示"当成一份正常的答案展示出去。

`grounded`字段就是为了避免这种误判存在的——`True`表示"这是一份真正有资料依据的回答"，`False`表示"这是一条诚实的拒答提示，不是模型生成的实质内容"。前端/调用方应该优先检查这个布尔字段，而不是简单判断字符串是否为空。这是一种很值得记住的接口设计思路：**当一个字段可能同时承载"正常结果"和"某种特殊状态提示"时，应该有一个独立的、语义明确的字段来区分这两种情况，而不是指望调用方靠"猜文本内容"去判断**。

## 引用溯源：`selected`而不是`chunks`

```python
citations = [Citation(doc_id=c.doc_id, chunk_id=c.chunk_id) for c in selected]
```

注意这里用来生成引用列表的是`selected`（真正被截取、组织进上下文、参与了这次回答生成的那几个片段），而不是`chunks`（检索阶段召回的全部候选结果，可能比`max_context_chunks`还多）。这是模块docstring第三条设计原则的具体体现：

> 引用溯源贯穿全程：从"检索到具体片段"到"回答携带引用信息"要有完整链路，而不是检索时打了标签、最终回答却没有把这份信息带出来。

如果引用列表用的是`chunks`而不是`selected`，会出现一个微妙但真实的问题：回答文案里标注"这个回答参考了10篇文档"，但实际生成回答时，大模型能看到的上下文里只有前5篇——用户如果去核实第6到第10篇的内容，会发现这些文档跟回答其实毫无关系，这份"引用列表"本身就是不准确的。用`selected`保证了引用列表和实际参与生成的内容精确对应，不多不少。

## `Retriever`：一个只有一个方法的Protocol

```python
@runtime_checkable
class Retriever(Protocol):
    async def retrieve(self, query: str) -> list[RetrievedChunk]: ...
```

这是本书里第三次遇到类似的`Protocol`写法（第7章的MCP工具接口、第34章的`SpanExporter`）——只要一个类有一个同名同签名的`async def retrieve`方法，就自动被认为实现了这个接口，不需要显式继承。`run_rag_query`完全不关心这个`retriever`背后到底是关键词匹配、向量检索还是混合检索（下一章要讲的内容），只要它能"把一个查询变成一批候选片段"就行——这种"面向接口而不是面向具体实现"的设计，让检索算法可以随时被替换、升级，而不影响整套流水线的其他部分。

`InMemoryRetriever`是这个协议一个极简的demo实现，用简单的关键词子串匹配打分，不代表真正的向量/混合检索效果，仅用于测试和演示这套流水线本身的行为。

## 本章小结

- RAG（检索增强生成）的核心价值，是让大模型基于真实检索到的资料回答问题，而不是仅凭训练记忆凭空作答——这能大幅降低"编造看似合理但实际不存在的信息"的风险。
- 把流程拆解成"检索→组织上下文→生成→附带引用"明确的几个阶段，每一步都能独立测试、独立替换实现，比"一股脑丢给AI自己处理"更可控。
- 检索完全没有命中内容时，必须在早期就诚实拒答，这是流水线的默认行为而不是可选项——`grounded=False`加上固定的拒答文案，杜绝了生成阶段基于空上下文编造答案的可能。
- `grounded`这个独立的布尔字段，是为了让调用方能区分"有依据的真答案"和"诚实的拒答提示"，不能只靠判断`text`是否为空字符串。
- 引用列表必须用"真正参与生成的片段"（`selected`）而不是"全部检索结果"（`chunks`）来构造，保证引用信息和实际回答依据精确对应。

## 动手做

```python
import asyncio
from ainative_rag.pipeline import run_rag_query, InMemoryRetriever

async def fake_generate(query: str, context: str) -> str:
    return f"根据参考资料回答「{query}」：{context[:50]}..."

async def main():
    retriever = InMemoryRetriever(documents={
        "policy-2026": "差旅报销标准：经济舱机票、连锁快捷酒店、市内交通按实报销。",
        "onboarding": "新员工入职需要在第一周完成安全培训和账号开通。",
    })

    # 场景一：能检索到相关内容
    answer = await run_rag_query("差旅报销标准是什么", retriever=retriever, generate_fn=fake_generate)
    print("grounded:", answer.grounded)
    print("citations:", answer.citations)
    print("text:", answer.text)

    # 场景二：查询和知识库完全不相关，应该诚实拒答
    answer2 = await run_rag_query("今天天气怎么样", retriever=retriever, generate_fn=fake_generate)
    print("\ngrounded:", answer2.grounded)
    print("text:", answer2.text)

asyncio.run(main())
```

## 面试可能会问

**问：如果让你设计一个RAG系统的核心问答流程，你会重点关注哪些环节？**

答题思路：先说明RAG的核心价值是让生成阶段"有据可查"，避免模型凭训练记忆编造信息。然后讲清楚流程应该拆成检索、组织上下文、生成、附带引用几个明确阶段，方便独立测试和替换实现。重点强调"检索完全无结果时必须诚实拒答"这一条——用一个独立的布尔字段（而不是简单判断文本是否为空）来区分"真答案"和"拒答提示"，避免调用方误判。最后可以提到引用列表必须精确对应"实际参与生成的内容"而不是"全部检索候选"，这样引用信息才经得起用户核实。这几点合在一起，体现的是"一个负责任的RAG系统，宁可少答，也不能编答案"这个更深层的设计取向。
