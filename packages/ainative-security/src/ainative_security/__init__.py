"""ainative-security —— PII脱敏、输出安全检测、同形字折叠、密钥漂移巡检。"""

from __future__ import annotations

from ainative_security.confusables import fold_confusables, has_confusables
from ainative_security.output_safety import OutputSafetyMiddleware, SafetyViolationError, strip_injection
from ainative_security.pii_redaction import redact_pii_text
from ainative_security.secret_drift import (
    detect_secret_drift,
    run_secret_drift_check_once,
    secret_drift_check_loop,
)

__version__ = "0.1.0"

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
