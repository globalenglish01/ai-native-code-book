"""MCP server配置组装 + stdio子进程环境变量安全白名单。

改造自真实项目里验证过的设计：多个agent各自手写`MultiServerMCPClient`
需要的server配置字典（transport判断、env白名单过滤），任何调整都要人工
同步到每一处，容易遗漏。本模块统一"如何拼装配置字典/如何过滤白名单
环境变量"这一层，不判断/决定该用哪种transport（http/stdio/sse）——
由调用方决定，保持各agent现有的传输方式选择不变。
"""

from __future__ import annotations

import os

SAFE_ENV_KEYS: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "DISPLAY", "LANG", "LC_ALL",
    "PLAYWRIGHT_BROWSERS_PATH", "NODE_PATH",
    "TMPDIR", "TEMP", "TMP",
    "SystemRoot", "SYSTEMROOT", "ComSpec", "USERPROFILE",  # Windows
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",  # Windows
})
"""stdio模式下只把这些变量传给子进程的默认安全白名单，防止API Key等密钥
通过环境变量泄露给子进程。这是一份可覆盖的默认上限，不是唯一的安全边界——
需要更多变量（比如让Playwright能找到浏览器/Node路径）的调用方可以通过
`build_safe_env(extra=...)`按需扩展。"""


def build_safe_env(
    extra: dict[str, str] | None = None,
    *,
    safe_keys: frozenset[str] = SAFE_ENV_KEYS,
) -> dict[str, str]:
    """按白名单过滤当前进程环境变量，再叠加`extra`（自定义键优先，会覆盖白名单同名值）。

    只应在真正需要把宿主环境变量透传给stdio子进程时调用；不需要宿主环境
    透传的场景，直接传一个普通dict给`build_mcp_config(env=...)`即可。
    """
    safe_env = {k: v for k, v in os.environ.items() if k in safe_keys}
    if extra:
        safe_env.update(extra)
    return safe_env


def build_mcp_config(
    name: str,
    transport: str,
    *,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, dict]:
    """组装`MultiServerMCPClient({...})`需要的单个server配置字典（按`name`为key）。

    只负责把参数拼成字典，不判断该用哪种transport——由调用方决定。省略的
    可选参数不会出现在结果字典里（避免给http配置塞入stdio专属的
    `command`/`env`等无意义键）。
    """
    entry: dict = {"transport": transport}
    if url is not None:
        entry["url"] = url
    if headers is not None:
        entry["headers"] = headers
    if command is not None:
        entry["command"] = command
    if args is not None:
        entry["args"] = args
    if cwd is not None:
        entry["cwd"] = cwd
    if env is not None:
        entry["env"] = env
    return {name: entry}


def merge_mcp_configs(*configs: dict[str, dict]) -> dict[str, dict]:
    """把多个`build_mcp_config()`的结果合并成一份`MultiServerMCPClient`需要的完整配置字典。

    后面的配置在server名重复时会覆盖前面的（与普通dict合并语义一致），
    调用方应确保各server名唯一，不依赖这里的覆盖行为掩盖命名冲突。
    """
    merged: dict[str, dict] = {}
    for config in configs:
        merged.update(config)
    return merged
