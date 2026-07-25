from __future__ import annotations

from ainative_security import pii_redaction
from ainative_security.pii_redaction import redact_pii_text


def test_redacts_china_id_card():
    text = "我的身份证号是310101199001011234，请核实。"
    result = redact_pii_text(text)
    assert "310101199001011234" not in result
    assert "310101" in result
    assert "1234" in result


def test_redacts_china_mobile_phone():
    text = "联系电话13812345678谢谢"
    result = redact_pii_text(text)
    assert "13812345678" not in result
    assert "138****5678" in result


def test_does_not_touch_normal_text():
    text = "这是一段完全没有敏感信息的普通对话内容。"
    assert redact_pii_text(text) == text


def test_never_raises_on_non_string_input():
    assert redact_pii_text(None) is None  # type: ignore[arg-type]
    assert redact_pii_text("") == ""


def test_idempotent_on_already_redacted_text():
    text = "手机号13812345678"
    once = redact_pii_text(text)
    twice = redact_pii_text(once)
    assert once == twice


def test_id_card_adjacent_to_cjk_characters_is_detected():
    """验证零宽断言而非\\b边界——紧邻中文字符时也能正确检测（CJK被Python re视为\\w）。"""
    text = "号是310101199001011234号"
    result = redact_pii_text(text)
    assert "310101199001011234" not in result


def test_returns_original_text_if_regex_substitution_unexpectedly_raises(monkeypatch):
    """redact_pii_text必须绝不抛异常——即使底层正则替换本身出问题，也要原样
    返回输入文本，而不是让调用方的持久化/日志流程被中断。"""

    class _BrokenPattern:
        def sub(self, *args, **kwargs):
            raise RuntimeError("simulated regex engine failure")

    monkeypatch.setattr(pii_redaction, "_CHINA_ID_CARD_RE", _BrokenPattern())
    text = "phone 13812345678"
    assert redact_pii_text(text) == text
