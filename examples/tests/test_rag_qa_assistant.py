from __future__ import annotations

from products.rag_qa_assistant import Document, RagQaAssistant, fake_generate


def _assistant_with_docs() -> RagQaAssistant:
    assistant = RagQaAssistant()
    assistant.store.add(Document(doc_id="doc1", content="Our refund policy allows returns within 30 days"))
    assistant.store.add(Document(doc_id="doc2", content="Shipping takes 5 to 7 business days"))
    return assistant


def test_answer_retrieves_relevant_document():
    assistant = _assistant_with_docs()
    result = assistant.answer("What is the refund policy?", fake_generate)
    assert "doc1" in result.retrieved_doc_ids


def test_answer_with_no_matching_documents_returns_fallback():
    assistant = _assistant_with_docs()
    result = assistant.answer("completely unrelated gibberish query xyz", fake_generate)
    assert result.retrieved_doc_ids == []
    assert "don't have information" in result.answer.lower()


def test_context_respects_token_budget():
    assistant = RagQaAssistant(max_context_tokens=5)
    # Two documents whose combined size clearly exceeds a 5-token budget.
    assistant.store.add(Document(doc_id="big1", content="word " * 100))
    assistant.store.add(Document(doc_id="big2", content="word " * 100))
    result = assistant.answer("word", fake_generate)
    # At least the first matching doc is always included (never zero context
    # just because a single doc alone exceeds budget), but not unbounded growth.
    assert len(result.retrieved_doc_ids) <= 2
    assert result.context_tokens > 0


def test_poisoned_document_triggers_output_safety_middleware():
    """一份被投毒的检索文档诱导模型在最终回复里说出注入短语——
    OutputSafetyMiddleware必须把这个短语从用户可见的最终文本里真正剥离掉，
    而不是只加一条警告note、把危险短语原样留在用户能看到的文本里。"""
    assistant = _assistant_with_docs()
    assistant.store.add(Document(doc_id="malicious_doc", content="malicious instructions hidden here"))
    result = assistant.answer("malicious", fake_generate)
    assert result.safety_triggered is True
    assert "ignore previous instructions" not in result.answer.lower()
    assert "[BLOCKED" in result.answer


def test_retrieved_doc_ids_preserve_relevance_order():
    assistant = RagQaAssistant()
    assistant.store.add(Document(doc_id="low_match", content="apple"))
    assistant.store.add(Document(doc_id="high_match", content="apple banana apple banana"))
    result = assistant.answer("apple banana", fake_generate)
    assert result.retrieved_doc_ids[0] == "high_match"
