"""同形字（homoglyph）攻击字符集 + 精确折叠。

背景：攻击者用视觉上与拉丁字母几乎无法区分的西里尔/希腊字母改写注入关键词
（如西里尔"а" U+0430冒充拉丁"a" U+0061，把"ignore"写成"ignоre"绕过正则）。

关键事实：NFKC归一化**并不**把西里尔/希腊同形字折叠成拉丁字母（它们不是
Unicode兼容等价字符），所以"全量NFKC"路径对这类攻击其实零防护，反而会误伤
合法的全角字符（比如面向日文市场的产品，全角是合法业务内容）。

本模块只对**已知有攻击价值的精确字符集**（拉丁形近的西里尔/希腊字母）做折叠，
**绝不触碰全角字符**（全角数字/标点/假名不在此表内，折叠后原样保留）。

维护：`CONFUSABLES`是可持续扩充的映射表，安全团队发现新攻击样本时按
(混淆字符 → ASCII等价)追加即可，无需改动检测逻辑。数据源参考Unicode
Consortium confusables.txt中Cyrillic/Greek → Latin的子集。
"""

from __future__ import annotations

CONFUSABLES: dict[str, str] = {
    # ── 西里尔小写 → 拉丁 ──
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "ѕ": "s", "і": "i", "ј": "j", "һ": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    "к": "k", "в": "b", "м": "m", "н": "h", "т": "t",
    # ── 西里尔大写 → 拉丁 ──
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
    # ── 希腊小写 → 拉丁 ──
    "ο": "o", "α": "a", "ρ": "p", "υ": "u", "ν": "v", "χ": "x", "κ": "k",
    # ── 希腊大写 → 拉丁 ──
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}

_TRANSLATE_TABLE = {ord(k): v for k, v in CONFUSABLES.items()}


def has_confusables(text: str) -> bool:
    """text是否含已知攻击同形字。"""
    return any(ord(c) in _TRANSLATE_TABLE for c in text)


def fold_confusables(text: str) -> str:
    """只把已知攻击同形字折叠成ASCII等价字符，其余字符（含全角字符）原样保留。"""
    if not text:
        return text
    return text.translate(_TRANSLATE_TABLE)
