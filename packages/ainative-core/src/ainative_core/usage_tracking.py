"""把一次LLM调用的用量记录到`UsageSink`——通过LangChain回调机制自动采集。

改造自真实项目里验证过的"usage tracker"设计：不需要调用方在每次
`model.invoke(...)`之后手动读取结果、拼装事件、调用sink——只需要在
构造模型时传入`usage_sink`，`build_model`/`build_agent_model`会自动
挂上一个回调处理器，每次调用结束后自动提取token用量并记录。

**关键实现约束**：通过`init_chat_model(model_id, callbacks=[handler], ...)`
在构造时把回调传给底层模型，而不是用`model.with_config(callbacks=[...])`——
后者返回的是`RunnableBinding`，不是`BaseChatModel`的子类，会破坏
`ModelFallbackMiddleware`依赖的"primary必须是货真价实的BaseChatModel"这个
类型不变量（ch53-01教训的同一类问题）。
"""

from __future__ import annotations

# time模块是Python标准库自带的，`time.time()`返回"现在是什么时候"，
# 用一个浮点数表示——具体含义是"从1970年1月1日0点到现在，一共过了多少
# 秒"（这是计算机世界里表示时间的一种通用惯例，叫Unix时间戳）。
import time

# Any表示"任意类型"，UUID是"通用唯一标识符"——一种保证几乎不可能和
# 别人重复的长字符串/数字组合，常用来给"这一次具体的调用"分配一个
# 独一无二的编号。
from typing import Any
from uuid import UUID

# 下面两行是从LangChain这个第三方库里导入的类型：
# BaseCallbackHandler——LangChain定义的"回调处理器"基类，任何想在
#   "模型调用开始/结束"这些时间点插入自己逻辑的类，都需要继承它。
# LLMResult——LangChain定义的"一次模型调用的完整结果"的数据结构，
#   包含了生成的文本、用量信息等。
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

# 从本包（ainative_core）自己的protocols.py文件里，导入UsageSink这个
# 协议（Protocol）类型——它只是一份"接口约定"，规定了"想接收用量记录的
# 对象，必须有一个record(event)方法"，具体怎么存（内存/数据库）由
# 调用方自己决定。
from ainative_core.protocols import UsageSink


# 函数名前面的下划线`_extract_usage`表示这是"内部辅助函数"，只在本文件
# 内部使用，不打算被外部代码直接调用（这只是命名约定，不是强制限制）。
def _extract_usage(response: LLMResult) -> tuple[int, int, bool]:
    """从`LLMResult`里提取(input_tokens, output_tokens, usage_available)。

    checklist D类"自建推理服务兼容性子类"真实要点：自建的vLLM/SGLang端点
    经常不在响应里返回`usage_metadata`——这种情况必须被明确记录成"没有
    拿到用量信息"，而不是伪造成`(0, 0)`当作"这次调用真的消耗了0
    token"。两者对下游成本看板/巡检脚本而言含义完全不同：前者应该被
    单独统计、提醒运维"这个供应商的用量不可见"；后者会被误判为免费
    调用，成本核算出现无法察觉的系统性低估。第三个返回值`usage_available`
    就是这条界线——`(0, 0, True)`表示"确实拿到了用量数据、且刚好是
    0"（理论上罕见但不是不可能），`(0, 0, False)`表示"根本没有用量
    数据可用"，调用方必须能区分对待这两种情况。
    """
    # 先把三个要返回的值都初始化成"还没找到任何用量信息"的状态。
    input_tokens = 0
    output_tokens = 0
    usage_available = False

    # LangChain的一次调用结果`response.generations`是"两层嵌套列表"：
    # 外层对应"你这次调用请求了几条独立的prompt"（通常是1条），
    # 内层对应"这一条prompt模型生成了几个候选回复"（通常也是1个，
    # 除非你要求模型一次生成多个候选答案）。所以要用两层for循环
    # 才能真正遍历到每一个具体的"生成结果"。
    for generation_list in response.generations:
        for generation in generation_list:
            # `getattr(对象, "属性名", 默认值)` 的作用是"尝试读取对象
            # 的这个属性，读不到（属性不存在）就用默认值代替"——比直接
            # 写`generation.message`更安全，因为不同版本/不同供应商的
            # 返回结果，字段可能有细微差异。
            message = getattr(generation, "message", None)
            # 只有在message真的存在时，才继续往下读它的usage_metadata
            # 属性；如果message本身是None，就直接把usage也设为None，
            # 避免对None取属性引发报错（"NoneType has no attribute..."）。
            usage = getattr(message, "usage_metadata", None) if message is not None else None
            # `isinstance(x, dict)` 检查x是不是一个字典（dict）类型——
            # 只有真的拿到了字典形状的用量数据，才认为"这次真的有用量
            # 信息可用"。
            if isinstance(usage, dict):
                usage_available = True
                # `usage.get("input_tokens") or 0` 的意思是：如果字典里
                # 有"input_tokens"这个key且值不是None/0/空，就用那个值，
                # 否则当作0处理，再用`+=`累加到目前为止的总数上
                # （因为有可能生成了不止一条回复，需要把每一条都加起来）。
                input_tokens += usage.get("input_tokens") or 0
                output_tokens += usage.get("output_tokens") or 0
    # Python可以用逗号一次返回多个值，调用方接收时会自动打包成一个
    # "元组"（tuple），比如 `a, b, c = _extract_usage(response)`。
    return input_tokens, output_tokens, usage_available


def _extract_model_name(response: LLMResult) -> str | None:
    """尽力从`LLMResult.llm_output`里取出具体模型名（不同供应商字段名不一致，
    找不到就返回`None`，由调用方决定fallback成什么）。"""
    # `response.llm_output` 可能是None（不同供应商实现不一致），
    # `x or {}` 是Python里常见的"给None兜底成一个空字典"的写法——
    # 这样后面就能放心调用`.get(...)`而不用先判断是不是None。
    llm_output = response.llm_output or {}
    # 不同供应商的SDK，把"模型名"这个信息放在不同的字段名下——有的叫
    # model_name，有的直接叫model。这里按优先顺序依次尝试这两个key。
    for key in ("model_name", "model"):
        value = llm_output.get(key)
        # 双重检查：这个值必须真的是字符串类型，而且不能是空字符串
        # （空字符串在Python里`bool("")`是False，所以`and value`这个
        # 条件会自动过滤掉它）。
        if isinstance(value, str) and value:
            # 只要找到一个满足条件的，立刻返回，不再继续尝试下一个key。
            return value
    # 两个key都没找到符合条件的值，明确返回None，让调用方知道
    # "没找到"，而不是返回一个可能引起误解的空字符串。
    return None


# class UsageTrackingCallbackHandler(BaseCallbackHandler): 表示这个类
# "继承"自BaseCallbackHandler——意味着它自动拥有BaseCallbackHandler
# 已经写好的所有能力，同时可以在需要的地方"重写"（覆盖）某些方法，
# 加上自己的行为（下面的on_llm_end就是重写的方法之一）。
class UsageTrackingCallbackHandler(BaseCallbackHandler):
    """每次LLM调用结束后，把用量事件写进注入的`UsageSink`。

    Args:
        sink: 用量记录的目标。
        agent_name: 记入事件的agent标识（哪个agent发起的这次调用）。
        provider: 记入事件的供应商标识（比如"anthropic"）。
        model_id: 记入事件的model_id（比如"anthropic:claude-sonnet-4-5"）——
            优先于从`LLMResult`里尝试提取到的模型名，因为调用方在构造时
            已经明确知道自己传的是哪个model_id，不需要依赖不同供应商
            response里字段名不一致的猜测。
    """

    # `*` 出现在参数列表中间，是Python的一个特殊语法：它后面的所有参数
    # 都必须"用参数名=值"的方式传入（叫"关键字参数"），不能只按位置
    # 顺序传值。这样调用时必须写成
    # `UsageTrackingCallbackHandler(sink, agent_name="a", provider="p", model_id="m")`，
    # 强制调用方明确写出每个参数的名字，避免因为参数顺序记错而传错值。
    def __init__(self, sink: UsageSink, *, agent_name: str, provider: str, model_id: str) -> None:
        # `super().__init__()` 的意思是"先执行父类（BaseCallbackHandler）
        # 自己的构造逻辑"——父类可能需要初始化一些自己内部用的状态，
        # 子类重写`__init__`时，通常都应该先调用一次父类的，除非明确
        # 知道不需要。
        super().__init__()
        # 把传进来的几个参数，分别存到`self`（这个实例自己）的属性上，
        # 方便这个类的其他方法（比如下面的on_llm_end）之后随时读取。
        self._sink = sink
        self._agent_name = agent_name
        self._provider = provider
        self._model_id = model_id

    # on_llm_end 是BaseCallbackHandler规定好的"钩子方法"名字——
    # LangChain在每次模型调用真正结束的那一刻，会自动调用所有挂载的
    # 回调处理器的这个方法，并把这次调用的完整结果(response)传进来。
    # 我们不需要自己去调用它，只需要重写好这个方法里想做的事。
    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        # 调用上面定义的辅助函数，从这次调用结果里提取出token用量信息。
        input_tokens, output_tokens, usage_available = _extract_usage(response)
        # 调用注入进来的sink（用量记录目标）的record方法，把这次调用
        # 拼装成一个字典（dict）记录下来。字典的每个key都是有明确
        # 含义的字段名，value是对应的实际数据。
        self._sink.record({
            "agent_name": self._agent_name,
            "provider": self._provider,
            # `_extract_model_name(response) or self._model_id` 表示：
            # 优先用从真实响应里提取到的模型名，提取失败（返回None，
            # Python里None相当于False）就退回用构造时传入的model_id。
            "model": _extract_model_name(response) or self._model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # 自建推理服务（vLLM/SGLang等）经常不返回用量信息——`False`时
            # `input_tokens`/`output_tokens`只是占位的0，不代表"这次调用
            # 真的免费"，下游成本核算/看板必须能读到这个字段、单独处理
            # "用量不可见"这类事件，而不是把它们和真实的零用量调用混在一起。
            "usage_available": usage_available,
            # 记录下这条事件产生的具体时刻。
            "timestamp": time.time(),
        })
