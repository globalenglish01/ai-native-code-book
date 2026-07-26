"""`SpanExporter`协议的内存版默认实现——供demo/测试使用。"""

# 让类型注解可以延迟解析，详见`tracing.py`里的详细解释，这里不再重复。
from __future__ import annotations

# 从`tracing.py`导入`SpanRecord`——这个模块里的两个类都是围绕"怎么处理
# 一份已经构造好的`SpanRecord`"来设计的，所以需要先拿到它的类型定义，
# 才能在类型注解（比如`list[SpanRecord]`）和方法参数里正确引用它。
from ainative_observability.tracing import SpanRecord


class InMemorySpanExporter:
    """把span原样存进内存列表——真实项目应实现`SpanExporter`协议接入真实后端。"""

    # 注意这个类没有显式写`class InMemorySpanExporter(SpanExporter):`去
    # 继承`tracing.py`里定义的`SpanExporter`协议——因为`SpanExporter`是
    # 一个`Protocol`（结构化类型/"鸭子类型"接口）：只要一个类具备同名同
    # 签名的`export`方法，就自动被认为"实现了这个协议"，不需要显式继承
    # 声明。这个类下面确实定义了`export(self, record: SpanRecord) -> None`，
    # 所以它天然就满足`SpanExporter`协议的要求。

    def __init__(self) -> None:
        # 用一个普通的Python列表，在内存里保存所有导出过的span记录。
        # 变量名前面的下划线`_`是命名惯例：表示这是这个类内部维护的
        # 实现细节，不建议外部代码直接读写这个列表本身（应该通过下面
        # 的`all()`/`for_correlation_id()`方法来读取）。
        self._records: list[SpanRecord] = []

    def export(self, record: SpanRecord) -> None:
        # 把传入的这一份span记录追加到内部列表末尾——这就是"导出"这个
        # 动作在"内存版"实现里具体做的事情：既不发网络请求，也不写文件，
        # 只是简单地"记住它"，方便demo/测试代码事后检查。
        self._records.append(record)

    def all(self) -> list[SpanRecord]:
        # `list(self._records)`——用内部列表的内容，构造一份新的、独立
        # 的列表返回，而不是直接把`self._records`本身返回出去。这样调用
        # 方拿到返回值后即使自己修改这份列表（比如`.append(...)`或
        # `.clear()`），也不会意外影响到这个exporter内部真正保存的数据。
        return list(self._records)

    def for_correlation_id(self, correlation_id: str) -> list[SpanRecord]:
        # 这是一个"列表推导式"（list comprehension）——Python里用一行
        # 代码快速从一个已有序列筛选/转换出一个新列表的写法，等价于写一个
        # for循环、符合条件的元素才`append`进新列表，但更简洁。这里的
        # 意思是：只保留`self._records`里`correlation_id`字段等于调用方
        # 传入的`correlation_id`参数值的那些span记录，其余的丢弃不要。
        # 用来回答"这一次请求/操作，一共产生了哪些span"这个问题。
        return [r for r in self._records if r.correlation_id == correlation_id]


class AlwaysFailingSpanExporter:
    """故意每次都抛异常的exporter——用于测试`Tracer`的导出失败自我监控是否生效。"""

    # 这个类同样没有显式继承`SpanExporter`——理由和上面`InMemorySpanExporter`
    # 一致：`SpanExporter`是结构化类型的`Protocol`，只要具备匹配签名的
    # `export`方法就自动算作实现了它。

    def export(self, record: SpanRecord) -> None:
        # 不管传进来的是什么span记录，都直接抛出一个`ConnectionError`
        # （Python标准库内置的异常类型，通常表示"网络连接相关的错误"）。
        # 这个类存在的唯一目的，就是模拟"真实的追踪后端不可用/连不上"
        # 这种场景，配合`tracing.py`里的`Tracer`/`_ObservableSpanRecorder`
        # 一起测试：即使导出这一步彻底失败，被追踪的业务代码本身也不能
        # 因此崩溃，而失败次数应该被正确统计下来。
        raise ConnectionError("simulated span export backend unavailable")
