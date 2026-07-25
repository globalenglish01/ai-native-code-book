"""对话内容持久化前的轻量PII检测/脱敏（规则匹配版）。

设计原则：
1. **绝不抛异常**：检测/脱敏本身出问题绝不能拖垮上层的持久化/对话流程。
2. **保留业务可读性**：脱敏时只涂黑中间部分，保留能辨认"这是身份证/手机号"
   的首尾片段，不是整段删除或替换成不透明的占位符。
3. **只提供纯函数，不关心调用时机**：本模块自身不假设自己会在什么时候被调用
   （比如checkpoint写入前、日志记录前、summarization溢出到历史文件前）——
   由使用方在自己的持久化/日志管道里决定何时调用`redact_pii_text`。

**当前覆盖范围**（第一阶段，规则匹配）：
- 中国大陆身份证号（18位，末位可为X/x校验位）
- 中国大陆手机号（11位，1[3-9]开头）

**明确未覆盖、留待后续评估**：人名、地址、银行卡号等无固定格式规律、需要
上下文理解才能识别的PII类型，通常需要专门的NER模型/服务，超出"规则匹配"
这个第一阶段的范围。
"""

from __future__ import annotations

import re

__all__ = ["COVERED_PII_TYPES", "redact_pii_text"]

COVERED_PII_TYPES: tuple[str, ...] = ("china_id_card", "china_mobile_phone")

# 中国大陆身份证号：18位，前17位数字，末位数字或X/x（校验位）。
# 用前后向零宽断言避免匹配到更长数字串的子串（而不是\b——Python re把CJK
# 表意文字也算作\w，紧邻中文字符时\b不产生边界，会导致"号是310101...1234"
# 这类前面紧跟中文字符的真实场景漏检）；不做行政区划码/出生日期的合法性
# 校验（规则匹配阶段的定位是"高召回、可接受一定误报"，不是精确身份证校验器）。
_CHINA_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?![\dXx])")

# 中国大陆手机号：11位，1[3-9]开头。同样用零宽断言而非\b（理由同上）。
_CHINA_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _mask_id_card(m: re.Match[str]) -> str:
    """保留前6位（地区码）+后4位，中间8位掩码——与国内常见的身份证脱敏惯例一致。"""
    s = m.group(0)
    return f"{s[:6]}********{s[-4:]}"


def _mask_mobile(m: re.Match[str]) -> str:
    """保留前3位（运营商号段）+后4位，中间4位掩码——如 138****1234。"""
    s = m.group(0)
    return f"{s[:3]}****{s[-4:]}"


def redact_pii_text(text: str) -> str:
    """对单段文本做PII脱敏。绝不抛异常——异常时原样返回，宁可漏检也不中断调用方流程。

    幂等：对已经脱敏过的文本再跑一次是安全的（掩码后的字符串不会再匹配身份证/手机号
    的格式规则），"已脱敏的旧内容 + 新内容"拼接后整体传入时不会造成重复脱敏或内容损坏。
    """
    if not text or not isinstance(text, str):
        return text
    try:
        text = _CHINA_ID_CARD_RE.sub(_mask_id_card, text)
        text = _CHINA_MOBILE_RE.sub(_mask_mobile, text)
    except Exception:  # noqa: BLE001
        return text
    return text
