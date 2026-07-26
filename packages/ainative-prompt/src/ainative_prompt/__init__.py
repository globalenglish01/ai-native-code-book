"""ainative-prompt —— Prompt版本管理、A/B粘性路由、LLM-as-judge评判。"""

# 见store.py/judge.py里的详细解释：让类型注解不需要在运行时真正被求值。
from __future__ import annotations

# 从本包内部的judge.py和store.py两个文件里，把外部使用方最常用的
# 函数/类直接导入到包的顶层——这样真实项目使用时可以写更短的
# `from ainative_prompt import load_prompt`，而不必写完整路径
# `from ainative_prompt.store import load_prompt`（两种写法都能用，
# 这里只是提供一个更方便的"快捷入口"）。
from ainative_prompt.judge import judge_response
from ainative_prompt.store import InMemoryPromptStore, ab_select_deterministic, load_prompt

# `__version__` 是Python社区的通用约定——定义了这个变量之后，别的代码
# 可以通过`ainative_prompt.__version__`读到"这是第几个版本"，不需要
# 单独解析pyproject.toml文件。
__version__ = "0.1.0"

# `__all__` 是Python的一个特殊约定变量：当别的代码执行
# `from ainative_prompt import *`（导入这个包里所有"公开"的东西）时，
# Python只会导入这个列表里列出的名字，而不是"包里定义的所有东西"——
# 用来明确声明"这几个才是这个包对外正式支持使用的公开接口"，其余的
# 都算作内部实现细节。
__all__ = [
    "InMemoryPromptStore",
    "ab_select_deterministic",
    "judge_response",
    "load_prompt",
]
