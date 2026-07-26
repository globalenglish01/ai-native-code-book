"""周期性运行时密钥漂移检测——发现配置在启动后意外回退到不安全默认值。

背景：大多数项目的密钥/密码校验（比如Pydantic Settings的
`@model_validator`）只在配置对象被实例化的那一刻触发——也就是应用启动
时刻。这能保证"启动的这一刻配置不是已知不安全的默认值"，但完全没有
机制持续监控"运行过程中，这些配置是否发生了非预期的回退"（比如某次
错误的运维操作意外重置了环境变量、配置管理系统返回了非预期的旧值）。

设计原则：
1. **只读检测+告警，绝不改变应用行为**——不重启、不阻断、不自愈。
2. **规则外置为`list[SecretRule]`**，不硬编码具体项目的字段名——原版
   实现里`detect_secret_drift`直接读`current_settings.jwt_secret_is_default`
   这类项目专属属性，和启动时校验（如果项目里也有一份类似的启动时校验）
   容易各自维护一份判断逻辑、逐渐产生不一致。本版把"规则"本身抽成
   `ainative_core.protocols.SecretRule`（`name`+`is_default`回调+`message`+
   `severity`），启动时校验和运行期巡检可以共用同一份规则列表。
3. **读取调用方传入的配置对象当前的属性值，不重新实例化配置**——本模块
   不假设配置对象长什么样，只要求`is_default`回调能接收这个对象并返回
   bool。这意味着能发现"进程内持有的配置对象被意外改回默认值"，但**不会**
   重新读取环境变量本身——如果环境变量在进程启动后才变化，只有配置对象
   被重新构造（通常发生在进程重启时）才会生效。这是明确的覆盖范围边界。
"""

from __future__ import annotations

# asyncio是Python标准库里"异步编程"的核心模块——它让程序可以"一边等待
# 一件耗时的事情（比如sleep等待、网络请求），一边把控制权让给别的任务去
# 执行"，而不是傻等在原地浪费时间。本文件下面会用到`asyncio.sleep(...)`
# （异步版本的"等待N秒"）。
import asyncio

# logging是Python标准库自带的日志模块——比起直接用`print(...)`打印，
# 用logging的好处是：可以统一控制"哪些信息该被记录、记录到哪里（终端/
# 文件/远程日志系统）、以什么级别（DEBUG/INFO/WARNING/ERROR）"，在正式
# 生产环境的项目里几乎总是用logging而不是print。
import logging

# os模块——见ainative_core.config.py里的详细解释：用来读取"环境变量"
# （启动程序前，由操作系统/终端/部署平台预先设置好的一些配置值）。
import os

# Any表示"这里的类型可以是任意类型"——用在这个模块不关心/不方便约束
# "配置对象具体长什么样"的地方（因为不同项目的配置类结构完全不同）。
from typing import Any

# 从ainative_core包（本框架的地基包）的protocols.py里，导入SecretRule——
# 这是一个约定好的"数据形状"，描述"一条密钥安全检查规则"应该长什么样
# （规则名字、判断函数、告警文案、严重程度）。
from ainative_core.protocols import SecretRule

# `logging.getLogger(__name__)` 的意思是"创建（或复用）一个专属于当前
# 这个文件的日志记录器"——`__name__`是Python自动帮每个文件维护的一个
# 特殊变量，值就是这个文件的模块路径（比如"ainative_security.secret_drift"）。
# 用`__name__`而不是随便写一个固定字符串，好处是日志里能清楚看出"这条
# 日志是从哪个模块打出来的"，方便排查问题时定位。
logger = logging.getLogger(__name__)

# 模块级常量：默认的巡检间隔，6小时换算成秒（6 * 3600 = 21600秒）。
# 写成"6 * 3600"而不是直接写"21600"，是为了让人一眼就能看出这个数字
# 的含义是"6个小时"，不需要自己心算021600秒等于几个小时。
DEFAULT_INTERVAL_SECONDS = 6 * 3600


def _drift_check_interval_seconds(env_var: str) -> int:
    # try/except：尝试把环境变量的值转换成整数——环境变量本身永远是
    # 字符串类型，即使写的是"3600"这种看起来像数字的内容，读出来也还是
    # 字符串"3600"，需要用`int(...)`显式转换成真正的整数类型。
    try:
        # `os.environ.get(env_var, "0")`——读取指定名字的环境变量，读不到
        # 就用字符串"0"兜底，这样即使环境变量完全没配置，`int(...)`也能
        # 正常转换出数字0，不会因为传了None而报错。
        v = int(os.environ.get(env_var, "0"))
        if v > 0:
            # 只有配置了一个大于0的正数，才采用这个自定义间隔——配置成
            # 0或负数，就当作"没有有效配置"，继续走到函数末尾的默认值。
            return v
    except (TypeError, ValueError):
        # 如果环境变量的值根本不是能转换成整数的字符串（比如手滑写成了
        # "abc"），`int(...)`会抛出ValueError；这里选择直接忽略这个异常
        # （`pass`表示"什么都不做，继续往下走"），而不是让整个程序崩溃——
        # 一个格式错误的环境变量，不应该拖垮巡检功能本身。
        pass
    # 上面两条路径都没有成功返回一个自定义的间隔值，就统一退回到模块
    # 顶部定义的默认值。
    return DEFAULT_INTERVAL_SECONDS


def detect_secret_drift(config: Any, rules: list[SecretRule]) -> list[str]:
    """对当前配置对象做只读检测，返回命中的问题描述列表（空列表表示正常）。

    绝不抛异常——单条规则本身出问题（比如属性不存在）不应该让其余规则
    的检测失败，也不应该影响调用方。
    """
    # 准备一个空列表，用来收集这次检测中，所有"命中了问题"的规则的
    # 说明文字。
    issues: list[str] = []
    # 依次取出规则列表里的每一条规则（每条规则都是一个SecretRule实例，
    # 包含name/is_default/message/severity这几个字段——具体定义见
    # ainative_core.protocols.SecretRule）。
    for rule in rules:
        # 每条规则的检测过程都单独包一层try/except——这样即使某一条
        # 规则的`is_default`回调函数本身写得有问题（比如访问了配置对象
        # 上不存在的属性、抛出了某种意外异常），也只会影响这一条规则的
        # 判断结果，不会导致循环提前终止、后面的规则完全没机会被检测。
        try:
            # `rule.is_default(config)`——调用这条规则挂载的判断函数，
            # 把当前的配置对象传进去，函数返回True表示"这一项目前确实
            # 还停留在不安全的默认值上"。
            if rule.is_default(config):
                issues.append(rule.message)
        except Exception as exc:  # noqa: BLE001
            # 同样是"绝不抛异常"这个设计原则的体现：任何类型的异常都被
            # 这里接住，只打一条WARNING级别的日志记录下"这条规则检测
            # 失败了、具体是什么原因"，然后继续处理下一条规则。
            # `%s`是Python`logging`模块特有的"延迟格式化"写法——只有当
            # 这条日志真的需要被输出时，才会去做字符串拼接，比提前用
            # f-string拼好字符串再传进去更高效（尤其在日志级别被调高、
            # 这条日志根本不会被输出的场景下，能省掉不必要的拼接开销）。
            logger.warning("[secret-drift] rule '%s' check failed (non-fatal): %s", rule.name, exc)
    return issues


async def run_secret_drift_check_once(
    config: Any,
    rules: list[SecretRule],
    *,
    is_monitored_environment: bool = True,
) -> list[str]:
    """跑一次检测。`is_monitored_environment=False`时直接跳过（比如development环境
    本身允许使用默认值，不需要巡检）。发现漂移时打一条ERROR级结构化日志。
    """
    # 函数名前面的`async`表示这是一个"异步函数"（也叫协程）——调用它
    # 必须写成`await run_secret_drift_check_once(...)`（在另一个async
    # 函数内部），而不能像普通函数那样直接调用。这样设计是因为这个函数
    # 未来可能会做一些需要"等待"的操作（比如异步写日志到远程系统），
    # 异步函数能让程序在等待期间先去处理别的任务，不浪费时间干等。
    if not is_monitored_environment:
        # 明确不需要监控的环境（比如本地开发环境），直接返回空列表，
        # 跳过全部检测逻辑——省去不必要的计算，也避免开发环境的正常
        # 默认配置被误判成"异常"而打扰开发者。
        return []

    issues = detect_secret_drift(config, rules)
    if issues:
        # 只有真的发现了问题，才打这条ERROR级别的日志。
        logger.error(
            "[secret-drift] Detected configuration drift back to insecure defaults: %s",
            # `"; ".join(issues)`——把issues这个字符串列表，用分号+空格
            # 拼接成一整条字符串，方便日志里一次性展示所有命中的问题。
            "; ".join(issues),
            # `extra={...}`是Python logging模块的特性：可以往一条日志
            # 记录里附加额外的"结构化字段"（不只是一句人类可读的文字），
            # 方便日志收集系统（比如ELK/Datadog）按这些字段做检索、
            # 统计、告警规则匹配，而不需要用正则去解析日志文字本身。
            extra={"security_event": "secret_config_drift", "issues": issues},
        )
    return issues


async def secret_drift_check_loop(
    config: Any,
    rules: list[SecretRule],
    *,
    interval_seconds: int | None = None,
    is_monitored_environment: bool = True,
) -> None:
    """周期性后台循环：先sleep再检查（避免和启动时校验在同一时刻重复告警），
    检测本身的异常只记警告日志、不中断循环。"""
    # `interval_seconds if interval_seconds is not None else ...`——这是
    # Python的"条件表达式"（也叫三元表达式），意思是"如果调用方明确传了
    # interval_seconds（哪怕传的是0，也算'传了'），就用调用方传的这个值；
    # 只有调用方完全没传（是None）时，才去调用后面的函数现算一个默认值"。
    # 这里特意写`is not None`而不是简单的`if interval_seconds`，是因为
    # 后者会把"调用方明确传了0"也误判成"没传"（0在Python里是"假值"），
    # 这个区分在这里很重要。
    interval = (
        interval_seconds if interval_seconds is not None
        else _drift_check_interval_seconds("AINATIVE_SECRET_DRIFT_CHECK_INTERVAL_SECONDS")
    )
    # `while True:` 是一个"无限循环"——只要没有被外部强制中断（比如
    # 调用方取消了这个后台任务），这段代码会一直反复执行下去，这正是
    # "周期性后台巡检"这种需求的典型实现方式。
    while True:
        # `await asyncio.sleep(interval)`——异步地"睡眠等待"interval秒。
        # 之所以放在循环最前面（先睡再检查，而不是先检查再睡），是为了
        # 避免这个后台巡检任务和"应用启动时刻"的校验挤在同一时间点，
        # 重复触发一次几乎同样的告警（模块docstring里也提到了这一点）。
        await asyncio.sleep(interval)
        try:
            await run_secret_drift_check_once(config, rules, is_monitored_environment=is_monitored_environment)
        except Exception as exc:  # noqa: BLE001
            # 即使某一次检测意外抛出了异常，也只记录警告日志、不让这个
            # `while True`循环因此终止——巡检任务应该能"活得比一次偶发的
            # 检测失败更久"，持续尝试下一轮检测。
            logger.warning("[secret-drift] Check failed (non-fatal): %s", exc)
