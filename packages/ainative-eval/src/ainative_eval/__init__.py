"""ainative-eval —— FCARS风格治理Gate、独立多评判聚合。"""

from __future__ import annotations

# 下面这一大段`from xxx import (...)`都是"包的对外门面"——把散落在
# fairness.py/gate.py/gdpr.py/judge_aggregation.py几个文件里的类/函数，
# 统一重新导出到`ainative_eval`这个包的顶层，方便使用方直接写
# `from ainative_eval import Gate`，而不用记住"Gate具体是在gate.py里"
# 这种内部文件划分细节。
from ainative_eval.fairness import (
    FairnessDimensionScore,
    FairnessResult,
    detect_stereotype_skew,
    evaluate_fairness,
    has_dominant_skew,
)
from ainative_eval.gate import (
    GREEN,
    NEEDS_REVIEW,
    RED,
    SKIPPED,
    UNKNOWN,
    YELLOW,
    Gate,
    decide,
    maybe_recheck_boundary,
    status_from_score,
)
from ainative_eval.gdpr import AuditRecord, DataSubjectRightsService, InMemoryAuditSink, ResourceCleaner
from ainative_eval.judge_aggregation import AggregatedJudgment, aggregate_scores

# 和ainative-core的__init__.py不同，这个包在顶层导出了大量具体的类/函数
# （而不是只留一个__version__）——因为这几个模块（gate/judge_aggregation/
# fairness/gdpr）联系紧密，使用方大多数场景下会一次性用到好几个，统一
# 从包顶层导入更方便；ainative-core则是被拆分成互相独立的协议文件，
# 使用方通常只需要精确导入某一个具体协议，所以没有做这种统一再导出。
__version__ = "0.1.0"

# `__all__`是Python的一个模块级特殊变量——专门用来声明"当别人写
# `from ainative_eval import *`（星号导入，一次性导入这个模块里所有
# 公开的东西）时，具体应该导入哪些名字"。这里列出的每一个字符串，
# 都对应上面已经导入进来的一个类/函数/常量名。即使不使用`import *`，
# 显式维护这份列表也能让IDE/类型检查工具清楚知道"这个包真正对外承诺
# 导出的东西是这些，其余都算内部实现细节"。
__all__ = [
    "GREEN",
    "NEEDS_REVIEW",
    "RED",
    "SKIPPED",
    "UNKNOWN",
    "YELLOW",
    "AggregatedJudgment",
    "AuditRecord",
    "DataSubjectRightsService",
    "FairnessDimensionScore",
    "FairnessResult",
    "Gate",
    "InMemoryAuditSink",
    "ResourceCleaner",
    "aggregate_scores",
    "decide",
    "detect_stereotype_skew",
    "evaluate_fairness",
    "has_dominant_skew",
    "maybe_recheck_boundary",
    "status_from_score",
]
