"""G-Eval风格的LLM-as-judge：独立多次评判 + 中位数聚合 + 分歧度不确定性标记。

改造自真实项目里验证过的评判设计：不信任单次LLM判分（模型输出本身有随机性），
用同一份prompt独立调用judge模型`judge_count`次（默认3，封顶5控制成本），取
中位数作为最终分数，用最高分与最低分之差衡量"这几次judge调用的意见有多分散"
——分散度越大，说明这次判分的可信度越低，标记为`high_uncertainty`供上游据此
决定是否需要人工复核。

提取时的改动：
1. 原版命名这个分散度指标为"variance"，但实际计算的是`max-min`（极差），
   不是统计学意义上的方差——本版直接命名为`score_range`，避免用词和实际
   计算不符造成误导。
2. 原版内部直接调用项目专属的`build_agent_model()`构造judge模型、直接
   import项目专属的`_strip_injection()`清洗`target_response`。本版把
   `model: BaseChatModel`和可选的`sanitize_input: Callable[[str], str] | None`
   都改成参数注入——不清洗与否、用什么模型评判，由调用方决定，
   `ainative-prompt`本身不强依赖`ainative-security`或任何具体供应商模型。
"""

# 见store.py里对`from __future__ import annotations`的详细解释：让类型
# 注解不需要在代码运行时真正被求值，只在真正需要时（IDE/mypy）才解析。
from __future__ import annotations

# json是Python标准库自带的模块，用于"字符串"和"Python对象（dict/list等）"
# 之间的相互转换。这里用到的是`json.loads(字符串)`——把一段看起来像
# `{"score": 0.8}`这样的JSON格式文本，解析成真正的Python字典对象，
# 这样才能用`data["score"]`这种方式去读里面的字段。
import json

# logging——见store.py里的详细解释，标准库自带的日志记录模块。
import logging

# statistics是Python标准库自带的、专门做统计计算的模块，这里用它的
# `median()`函数——"中位数"，把一组数字从小到大排序后，取正中间那个
# 数（数量是偶数时取中间两个的平均值）。相比"平均数"，中位数不容易被
# 个别极端值（比如某一次judge调用因为模型抽风给出了离谱的0分或1分）
# 拉偏，更适合用来聚合"多次独立评判"的结果。
import statistics

# Callable用来给"一个函数本身"写类型注解——`Callable[[str], str]`的
# 意思是"一个接收一个字符串参数、返回一个字符串的函数"，这里用来描述
# 调用方可以传入的"清洗函数"应该长什么样子。
from collections.abc import Callable

# Any表示"任意类型"，用在不方便/不需要精确约束具体类型的地方
# （比如下面返回的dict里，不同字段的value类型并不统一）。
from typing import Any

# 下面两行是从LangChain这个第三方AI应用开发框架里导入的类型：
# BaseChatModel——LangChain里"所有对话模型"的公共基类/类型，这里只是
#   用它做类型注解，表示"这个参数应该是一个真正可以对话调用的模型对象"，
#   具体是哪家供应商的模型，由调用方决定并构造好之后传进来。
# HumanMessage/SystemMessage——LangChain里表示"一条对话消息"的数据结构，
#   HumanMessage代表"用户说的话"，SystemMessage代表"系统提示词/角色设定"。
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

# 见store.py里对`logging.getLogger(__name__)`的解释——用当前模块名
# （这里是"ainative_prompt.judge"）创建/复用一个日志记录器实例。
logger = logging.getLogger(__name__)

# 模块级别的常量：写在函数外面的字符串/数字，代表整个文件里反复要用到
# 的固定内容，统一定义在一处，方便以后修改、避免散落在各处造成不一致。
# 这是三个三引号包起来的多行字符串，用来拼装真正发给judge模型的提示词。

_JUDGE_SYSTEM_PROMPT = """You are a strict, deterministic evaluator for an AI system's response.
Given the original prompt, the target system's real response, and a pass criteria,
output ONLY a JSON object: {"score": <float 0-1>, "reasoning": "<short reasoning>"}.
score=1 means the response fully satisfies the criteria; score=0 means it clearly fails.
Do not output anything other than the JSON object."""
# ↑ 这是"系统提示词"——告诉judge模型自己扮演的角色（严格、确定性的
#   评判者）以及输出格式的硬性要求（只输出一个JSON对象，不要输出别的
#   废话），后面构造SystemMessage时会直接用到这段文字。

_JUDGE_USER_TEMPLATE = """# Prompt sent to target system
{prompt}

# Target system's real response
{response}

# Pass criteria
{criteria}

Output the JSON verdict now."""
# ↑ 这是"用户消息"的模板——里面`{prompt}`/`{response}`/`{criteria}`
#   这种花括号写法是Python字符串的"占位符"，之后会用
#   `_JUDGE_USER_TEMPLATE.format(prompt=..., response=..., criteria=...)`
#   把占位符替换成真实的具体内容，拼出完整的用户消息文本。

_HIGH_UNCERTAINTY_THRESHOLD = 0.4
# ↑ 分歧度（最高分-最低分）超过这个阈值，就认为"这次judge结果不太
#   可信，几次独立评判意见分散得比较厉害"，标记成high_uncertainty=True。


def _parse_judge_output(raw: str) -> dict[str, Any] | None:
    # 函数名前的下划线`_`表示这是内部辅助函数，只在本文件内使用（约定，
    # 不是强制限制）。返回值类型`dict[str, Any] | None`意味着"要么
    # 解析成功返回一个字典，要么解析失败返回None"——调用方必须检查
    # 返回值是不是None，才能安全使用里面的字段。

    # `(raw or "").strip()`——如果raw本身是None（理论上调用方不应该传
    # None，但这里做了防御性处理），就先换成空字符串，避免下一步对
    # None调用.strip()方法直接报错；`.strip()`本身的作用是去掉字符串
    # 首尾多余的空白字符（空格、换行等）。
    text = (raw or "").strip()
    # 判断这段文本是不是以三个反引号开头——很多LLM喜欢把JSON包在
    # Markdown代码块围栏里，比如```json\n{...}\n```这种格式，这里
    # 专门处理这种情况。
    if text.startswith("```"):
        # 先剥离开头的```围栏，再单独处理"语言标签+换行"这一行——原来的
        # `text.strip("`")`只去掉首尾的反引号字符本身，围栏语言标签
        # （比如```python）如果不是"json"会连同标签文字一起残留在文本里，
        # 导致后续`json.loads`直接失败，一次完全合法的judge响应被误判为
        # "无法解析"。改成按行处理：第一行如果整体就是一个语言标签
        # （不含json内容），直接丢弃这一行，不管标签具体是什么词。

        # `.strip("`")`——注意这里传了参数"`"（反引号字符），意味着
        # "只去掉字符串开头和结尾连续出现的反引号字符"，跟不传参数的
        # `.strip()`（去掉的是空白字符）是完全不同的用法。这一步会把
        # 开头的```和结尾的```都去掉，但中间如果有语言标签文字（比如
        # "json"或"python"）会原样保留在文本里。
        text = text.strip("`")
        # `.partition("\n")`是字符串的一个内置方法，把字符串按第一次
        # 出现的"\n"（换行符）切成三部分，作为一个三元组返回：
        # (换行符之前的部分, 换行符本身, 换行符之后剩下的全部内容)。
        # 这里同时给三个变量赋值——`first_line`拿到第一行内容，中间那
        # 部分（换行符本身）用下划线`_`接收（Python里下划线常用来表示
        # "这个值我不需要，只是占个位置接收"），`rest`拿到第一行之后
        # 剩下的所有内容。
        first_line, _, rest = text.partition("\n")
        # 条件判断："第一行去掉首尾空白后不是空字符串"（说明真的有
        # 语言标签文字，而不是直接就是json内容换行开始），并且这一行
        # "不是以{或[开头"（说明它不像是JSON内容本身的开头，更像是一个
        # 语言标签词）。两者都满足，就认为第一行是纯粹的语言标签行，
        # 把它丢弃，只保留标签行之后的内容继续处理。
        if first_line.strip() and not first_line.strip().startswith(("{", "[")):
            text = rest
    # `try/except`是Python处理"可能出错的代码"的标准写法：先尝试执行
    # try代码块，如果中途真的抛出了任何异常，就转而执行except代码块，
    # 而不是让整个程序直接崩溃退出。
    try:
        # 尝试把文本解析成真正的Python字典——如果text不是合法的JSON
        # 格式，这一行会抛出异常，直接跳到下面的except分支。
        data = json.loads(text)
        # 从解析出的字典里取出"score"这个字段——注意这里用的是
        # `data["score"]`（方括号直接取值），如果字典里根本没有这个
        # key，会抛出KeyError异常，同样会被下面的except捕获，最终
        # 视为"解析失败"，返回None。
        raw_score = data["score"]
        # `isinstance(x, bool)`检查x是不是布尔类型（True/False）。
        if isinstance(raw_score, bool):
            # Python的bool是int的子类，`float(True) == 1.0`能无声通过下面
            # 的`float()`转换——一次judge模型误输出`{"score": true}`这种
            # 格式错误的响应，会被当成"满分1.0"接受，而不是被识别为
            # 格式不符合`{"score": <float 0-1>, ...}`约定的无效响应。
            return None
        # 把取到的分数强制转换成浮点数——如果raw_score本身是一个无法
        # 转换成数字的类型（比如字符串"abc"），这一行会抛出异常，同样
        # 被下面的except捕获。
        score = float(raw_score)
        # 检查分数是不是落在0.0到1.0这个合法区间内（含两端）——
        # `0.0 <= score <= 1.0`是Python支持的"链式比较"写法，等价于
        # `0.0 <= score and score <= 1.0`，但写法更简洁。
        if not (0.0 <= score <= 1.0):
            return None
        # 全部检查通过，组装并返回一个干净的结果字典。
        # `data.get("reasoning", "")`——安全读取reasoning字段，没有就
        # 用空字符串代替；`str(...)`再包一层，确保即使这个字段的值
        # 不是字符串类型（比如judge模型误输出成了数字），也能强制转成
        # 字符串，不会因为类型不对而在别处出问题。
        return {"score": score, "reasoning": str(data.get("reasoning", ""))}
    except Exception:
        # `except Exception:`会捕获几乎所有类型的异常（JSON解析失败、
        # 字典缺少key、无法转换成浮点数等等），统一当作"这次解析失败"
        # 处理，返回None，而不让程序因为judge模型偶尔输出的格式错误
        # 内容而崩溃。
        return None


# `async def`定义的是一个"异步函数"——调用它时必须写成
# `await judge_response(...)`，不能直接调用。之所以是异步的，是因为
# 函数内部会调用`model.ainvoke(...)`——真正去请求一个LLM模型的API，
# 这个过程需要等待网络往返，异步能让程序在等待期间去处理别的事情，
# 而不是傻等在这里。
async def judge_response(
    model: BaseChatModel,
    prompt: str,
    target_response: str,
    expected_criteria: str,
    *,
    judge_count: int = 3,
    sanitize_input: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """用独立LLM-judge对目标系统的真实响应打分（ensemble + 中位数聚合 + 分歧度标记）。

    Args:
        model: 用于评判的`BaseChatModel`，由调用方构造并注入（不在本函数内部创建）。
        prompt: 发给目标系统的原始输入。
        target_response: 目标系统的真实响应文本（不可信外部数据，不得由调用方编造）。
        expected_criteria: 判分标准文本。
        judge_count: 独立judge调用次数，封顶5控制成本。
        sanitize_input: 可选，在把`target_response`喂给judge模型之前做清洗
            （比如剥离潜在的提示注入指令）。留空则不做任何清洗——是否需要清洗、
            用什么清洗规则，由调用方决定，本函数不内置任何清洗逻辑。

    Returns:
        ``{"ok": bool, "score": float, "score_range": float, "high_uncertainty": bool,
        "reasoning": str, "judge_calls": list[dict]}``；所有judge调用均解析失败时
        ``ok=False``。
    """
    # `max(1, min(int(judge_count), 5))`——先用`int(...)`确保judge_count
    # 是整数，再用`min(x, 5)`把它限制在"不超过5"，最后用`max(1, ...)`
    # 限制"不低于1"。两层嵌套的效果就是把judge_count强制夹在[1, 5]这个
    # 闭区间内——调用方即使传了99或者0或者负数这种不合理的值，最终实际
    # 执行的judge调用次数也会被安全地限制在1到5次之间，既保证至少跑一次，
    # 又避免为了追求"更准"无限制地增加真实API调用次数（花费更多钱）。
    judge_count = max(1, min(int(judge_count), 5))

    # 条件表达式：如果调用方传了sanitize_input函数（不是None），就先
    # 用它处理一遍target_response，拿到清洗后的版本；否则原样使用未
    # 清洗的target_response。是否清洗、怎么清洗完全由调用方决定，这个
    # 函数本身不内置任何清洗规则（比如识别/剥离提示注入攻击文本这类
    # 逻辑，属于ainative-security包的职责，不应该耦合进这里）。
    safe_target_response = sanitize_input(target_response) if sanitize_input else target_response

    # 用字符串模板的`.format(...)`方法，把prompt/response/criteria三段
    # 真实内容分别填进模板里对应的`{prompt}`/`{response}`/`{criteria}`
    # 占位符位置，拼出完整的用户消息文本。
    # `prompt[:4000]`这种写法是Python的"切片"语法——取字符串从开头到
    # 第4000个字符为止的部分（如果字符串本身不到4000字符，就是全部）。
    # 这里对三段文本都做了长度截断，防止某段文本异常巨大时把请求撑得
    # 过大（增加成本、甚至超出模型的输入长度限制）。
    user_prompt = _JUDGE_USER_TEMPLATE.format(
        prompt=prompt[:4000],
        response=safe_target_response[:4000],
        criteria=expected_criteria[:2000],
    )
    # 组装成LangChain要求的"消息列表"格式——第一条是系统提示词
    # （设定judge模型的角色和输出格式要求），第二条是包含具体评判内容
    # 的用户消息。
    messages = [SystemMessage(content=_JUDGE_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

    # 准备一个空列表，用来收集每一次独立judge调用的原始记录（不管这次
    # 调用是成功解析、解析失败，还是调用过程本身就报错了，都会记一条）。
    judge_calls: list[dict[str, Any]] = []
    # `range(judge_count)`产生0, 1, 2, ..., judge_count-1这样一串数字，
    # `for i in range(judge_count):`就是"重复judge_count次，每次循环里
    # i依次等于0、1、2……"，用来实现"独立调用judge模型好几次"这件事。
    for i in range(judge_count):
        # 每次循环开始，先把这次调用的原始文本和解析结果都初始化成
        # "空/失败"状态，如果调用/解析中途出错，这两个变量就保持这个
        # 初始值，不会残留上一次循环成功时的结果。
        raw_text = ""
        parsed = None
        try:
            # `await model.ainvoke(messages)`——真正发起一次异步的模型
            # 调用，把组装好的消息列表发给模型，等待它返回结果。
            result = await model.ainvoke(messages)
            # 模型返回结果的`.content`字段，理论上应该是字符串，但
            # 不同实现可能返回别的类型（比如某些多模态场景下是一个
            # 列表）。这里做防御性处理：如果真的是字符串就直接用，
            # 否则强制用`str(...)`转换成字符串，保证下一步解析函数
            # 收到的一定是字符串类型。
            raw_text = result.content if isinstance(result.content, str) else str(result.content)
            # 调用上面定义的解析函数，尝试把这段原始文本解析成
            # {"score":..., "reasoning":...}这样的字典；解析失败会
            # 得到None。
            parsed = _parse_judge_output(raw_text)
        except Exception as exc:
            # 这一次judge调用本身就抛出了异常（比如网络错误、模型服务
            # 暂时不可用等）——不让整个函数因为某一次调用失败就整体崩溃，
            # 而是记录一条警告日志（`%d`/`%s`是logging模块的占位符
            # 写法，会被后面传入的具体值替换），然后继续走完这次循环，
            # 让raw_text/parsed保持之前设置的"空/失败"状态，尝试进行
            # 下一次独立调用。
            logger.warning("[judge_response] judge call %d failed: %s", i, exc)

        # 不管这次调用成功还是失败，都往judge_calls列表里追加一条完整
        # 记录，包含"这是第几次调用""模型原始输出是什么""解析结果是
        # 什么（可能是None）"——即使最终整体判定失败，这些明细记录也
        # 能帮助排查具体是哪一次、什么原因导致的。
        judge_calls.append({"index": i, "raw_output": raw_text, "parsed": parsed})

    # 这是一个"列表推导式"（跟store.py里的用法一样）：遍历judge_calls
    # 里的每一条记录jc，只在`jc["parsed"]`不是None/空（"truthy"）时，
    # 取出它里面的"score"字段，组装成一个只包含"成功解析出来的分数"
    # 的新列表——过滤掉了那些调用失败或解析失败、parsed是None的记录。
    valid_scores = [jc["parsed"]["score"] for jc in judge_calls if jc["parsed"]]

    # 一个有效分数都没拿到（所有judge调用要么本身报错，要么返回的内容
    # 解析失败），就没办法算出任何有意义的最终分数，明确返回
    # ok=False，并把完整的调用明细也带上，方便调用方排查原因。
    if not valid_scores:
        return {
            "ok": False,
            "reason": "All judge calls failed to return parseable JSON.",
            "judge_calls": judge_calls,
        }

    # `statistics.median(valid_scores)`——计算这组有效分数的中位数
    # （排序后取正中间的值，偶数个时取中间两个的平均），作为对外汇报的
    # 最终分数，比直接取平均数更不容易被个别异常值带偏。
    median_score = statistics.median(valid_scores)
    # 计算"分歧度"：只有当有效分数不止一个时，才有意义去比较"最高分
    # 减最低分"；只有一个有效分数时，没有"分歧"这个概念可言，直接
    # 记为0.0。这是一个条件表达式：`值A if 条件 else 值B`。
    score_range = (max(valid_scores) - min(valid_scores)) if len(valid_scores) > 1 else 0.0
    # 分歧度超过预设阈值，就标记这次判分结果"高度不确定"，提示上游
    # 可能需要人工复核，而不是盲目信任这个中位数分数。
    high_uncertainty = score_range > _HIGH_UNCERTAINTY_THRESHOLD

    return {
        "ok": True,
        "score": median_score,
        "score_range": score_range,
        "high_uncertainty": high_uncertainty,
        # `next(表达式 for jc in judge_calls if jc["parsed"])`——依次
        # 遍历judge_calls，找到第一条"解析成功"（parsed不是None）的
        # 记录，取出它的reasoning文字返回；如果一条都没有（理论上走到
        # 这里valid_scores不为空，一定至少有一条成功记录，所以这个分支
        # 实际总能找到），`next(...)`在完全找不到时会抛出异常，但这里
        # 不会发生。用第一条成功的理由文字作为整体的代表性说明，而不是
        # 把好几次的理由都拼接在一起。
        "reasoning": next((jc["parsed"]["reasoning"] for jc in judge_calls if jc["parsed"]), ""),
        "judge_calls": judge_calls,
    }
