"""ainative-cli —— 一条命令生成AI Native项目骨架（`ainative new <project> --type <type>`）。"""

# 这一行是Python的"未来特性"声明：让代码里的类型注解（比如函数参数、
# 返回值上写的类型）不需要在写代码的当下就真正存在，Python解释器只把它们
# 当作字符串暂存，等真正需要检查类型时（比如IDE/mypy）才去解析。这个包
# 本身这一个文件里暂时没有用到需要它的写法，但保持和包内其余文件风格
# 一致，统一在每个模块顶部加上这一行。
from __future__ import annotations

# 这是这个包（ainative-cli）的版本号——写成一个模块级的公开字符串变量，
# 方便别的代码（比如打包工具、`ainative --version`这类命令）直接
# `from ainative_cli import __version__`读取，而不用去解析pyproject.toml。
__version__ = "0.1.0"
