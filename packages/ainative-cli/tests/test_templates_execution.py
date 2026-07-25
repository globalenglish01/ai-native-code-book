"""端到端验证：每个模板生成的main.py真的能被执行，不只是语法正确。

这是比`test_templates.py`里的AST语法检查更强的保证——真正import并运行
生成的代码，捕获"import的模块/函数名不存在"这类只有真正执行才会暴露的错误。
不需要任何真实API Key，因为每个模板用的都是内存版实现。
"""

from __future__ import annotations

import asyncio
import types

import pytest
from ainative_cli.templates import TEMPLATES


@pytest.mark.parametrize("template_name", sorted(TEMPLATES))
def test_every_template_main_py_executes_without_error(template_name, capsys):
    template = TEMPLATES[template_name]
    module = types.ModuleType(f"generated_{template_name.replace('-', '_')}")
    exec(compile(template.main_py, f"<{template_name}/main.py>", "exec"), module.__dict__)

    # Every template defines an async main() and calls asyncio.run(main()) under
    # `if __name__ == "__main__"` — since exec() sets __name__ to the module's
    # own name (not "__main__"), we invoke it explicitly here.
    asyncio.run(module.main())

    captured = capsys.readouterr()
    assert captured.out.strip() != ""
