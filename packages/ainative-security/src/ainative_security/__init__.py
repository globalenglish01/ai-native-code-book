"""ainative-security —— PII脱敏、输出安全检测、同形字折叠、密钥漂移巡检。"""

from __future__ import annotations

# 下面这些`from 模块 import 名字`是把本包内其他几个文件（confusables.py、
# output_safety.py、pii_redaction.py、secret_drift.py）里已经写好的函数/类，
# "搬"到`ainative_security`这个包的最外层——这样别人使用这个包时，可以
# 直接写`from ainative_security import redact_pii_text`，不需要知道
# 这个函数其实住在`ainative_security.pii_redaction`这个更深的子模块里。
# 这是Python包设计里很常见的做法：内部文件可以拆得很细，但对外暴露一个
# 简洁、稳定的"门面"。
from ainative_security.confusables import fold_confusables, has_confusables
from ainative_security.output_safety import OutputSafetyMiddleware, SafetyViolationError, strip_injection
from ainative_security.pii_redaction import redact_pii_text
from ainative_security.secret_drift import (
    detect_secret_drift,
    run_secret_drift_check_once,
    secret_drift_check_loop,
)

# `__version__`——见ainative-core的`__init__.py`同名变量的详细解释：
# 让别的代码能通过`ainative_security.__version__`读到版本号，不用去
# 解析pyproject.toml文件。
__version__ = "0.1.0"

# `__all__`是Python的一个特殊约定变量——它是一份"字符串列表"，列出这个
# 模块打算公开、允许被`from ainative_security import *`这种"星号导入"
# 一次性带走的所有名字。它还有一个更实际的作用：一些IDE/类型检查工具会
# 用它来判断"这个名字是不是这个包刻意导出的公开API的一部分"，帮助使用者
# 分清"哪些是这个包承诺会长期维护的公开接口"和"哪些只是内部实现细节"。
__all__ = [
    "OutputSafetyMiddleware",
    "SafetyViolationError",
    "detect_secret_drift",
    "fold_confusables",
    "has_confusables",
    "redact_pii_text",
    "run_secret_drift_check_once",
    "secret_drift_check_loop",
    "strip_injection",
]
