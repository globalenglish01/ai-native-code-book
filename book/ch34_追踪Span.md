# 第34章 —— 追踪Span

代码位置：`packages/ainative-observability/src/ainative_observability/tracing.py`

## 一个"看起来在追踪，实际上数据早就丢了"的场景

假设你的系统接入了分布式追踪——每次处理请求、每次调用大模型，都会生成一条"span"（一段有开始和结束时间的操作记录），这些span最终会被导出到一个专门的追踪后端（比如Tempo、Jaeger），团队靠它排查"这次请求到底慢在哪一步"。

某天有人反馈"最近好几次超时的请求，追踪系统里完全查不到对应的记录"。你去翻代码，发现导出span到后端这一步，外面包了一层`try/except`，抓住异常之后只是`pass`掉——网络抖动、后端服务重启、序列化失败……任何一次导出失败，都会被这行`except: pass`默默吞掉，代码里的追踪逻辑"看起来一直在正常工作"（没有任何报错、没有任何告警），实际上这段时间的数据可能已经在悄悄流失，而且没有任何人知道流失了多少。

这一章要看的`tracing.py`要解决的正是这个问题：**追踪系统本身的可靠性，需要被追踪系统自己监控起来**。除此之外，模块还处理了另一个真实出现过的问题——`request_id`（HTTP层）、`trace_id`（追踪层）、`thread_id`（业务框架层）三套不同层级的ID体系，如果彼此之间没有代码真正把它们串起来，即使每一层各自都有自己的ID，你依然没办法从一条日志反查到对应的完整调用链路。

## `SpanRecord`：一份已经发生的历史事实

```python
@dataclass(frozen=True)
class SpanRecord:
    span_id: str
    parent_span_id: str | None
    correlation_id: str
    name: str
    start_time: float
    end_time: float
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error_message: str | None = None

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000
```

留意`frozen=True`——一个span一旦"完成"（有了确定的`end_time`和`status`），就代表一段已经发生过的历史事实，不应该在事后被谁悄悄改写。`correlation_id`是一个没有默认值的必填字段：这个模块的态度是"关联ID不是事后才想起来要补的可选项，而是构造一个span时就必须想清楚的东西"——这正是本章开头提到的"三套ID体系各自为政"问题的直接应对：真实项目里，这个字段应该填成HTTP层的`request_id`本身，或者一个能反查到`request_id`的值，而不是另起一套跟其他层完全无关的编号。

`duration_ms`用`@property`包装成一个"看起来像字段、实际是计算出来的"属性——耗时这个数字完全可以从`start_time`和`end_time`直接算出来，没必要让每个调用方自己写一遍减法乘法，也避免了"如果真存一个独立字段，忘记同步更新导致和`start_time`/`end_time`对不上"这类问题（这类"能推导出来就不要独立存储"的设计思路，比手动维护冗余字段更不容易出现数据不一致）。

## `SpanExporter`：一个只有一个方法的协议

```python
@runtime_checkable
class SpanExporter(Protocol):
    def export(self, record: SpanRecord) -> None: ...
```

这是本章第二次遇到`Protocol`（第7章讲MCP的`Retriever`接口时也是同样的写法）——只要一个类有一个同名同签名的`export`方法，就自动被认为"实现了这个协议"，不需要显式写`class Foo(SpanExporter)`。真实项目想接入OpenTelemetry、Jaeger或者任何自建的追踪后端，只需要实现这一个方法，`Tracer`本身完全不关心span最终具体被送到了哪里。

## `_ObservableSpanRecorder`：把"导出失败"从沉默变成可数的数字

这是这一章最核心的部分：

```python
class _ObservableSpanRecorder:
    def __init__(
        self, exporter: SpanExporter, *, on_export_failure: Callable[[Exception], None] | None = None
    ) -> None:
        self._exporter = exporter
        self._on_export_failure = on_export_failure
        self.export_failure_count = 0

    def export(self, record: SpanRecord) -> None:
        try:
            self._exporter.export(record)
        except Exception as exc:  # noqa: BLE001
            self.export_failure_count += 1
            if self._on_export_failure is not None:
                self._on_export_failure(exc)
```

`except Exception`这里故意写得很宽泛——导出失败的原因可能是网络断开、后端服务挂了、序列化出问题，各种意料之外的情况都有可能，而这个方法的职责就是"不管具体因为什么原因失败，都不能让追踪系统本身的问题反过来影响被追踪的业务代码"。但和本章开头那个反面场景（`except: pass`）的关键区别在于：**这里没有真的把异常"吞掉"就完事**——`self.export_failure_count += 1`把每一次失败都变成了一个持续累加、可以被外部代码随时读取的数字；如果调用方注册了`on_export_failure`回调，还会额外把这次具体的异常对象传出去，让调用方有机会立刻打一条告警日志或者上报监控系统。

**这就是本章想强调的核心原则**：一个"可能失败但不能让失败拖垮主流程"的操作，正确的处理方式不是"捕获异常然后什么都不做"，而是"捕获异常，同时让这次失败变得可统计、可观察"——两者的代码看起来都是`try/except`，但对外的行为天差地别：前者是真正的"優雅降级"，后者是给自己埋了一个没有任何提示的定时炸弹。

## `Tracer.span`：`contextmanager`+`try/finally`保证span一定会被导出

```python
@contextmanager
def span(self, name: str, *, correlation_id: str, parent: SpanRecord | None = None, **attributes: Any):
    span_id = str(uuid.uuid4())
    start_time = time.time()
    status = "ok"
    error_message: str | None = None
    try:
        placeholder = SpanRecord(
            span_id=span_id, parent_span_id=parent.span_id if parent else None,
            correlation_id=correlation_id, name=name, start_time=start_time, end_time=start_time,
            attributes=dict(attributes),
        )
        yield placeholder
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        raise
    finally:
        end_time = time.time()
        record = SpanRecord(
            span_id=span_id, parent_span_id=parent.span_id if parent else None,
            correlation_id=correlation_id, name=name, start_time=start_time, end_time=end_time,
            attributes=dict(attributes), status=status, error_message=error_message,
        )
        self._recorder.export(record)
```

用法是这样的：

```python
with tracer.span("handle_request", correlation_id=request_id) as s:
    ...
    with tracer.span("call_llm", correlation_id=request_id, parent=s):
        ...
```

`@contextmanager`把一个普通的生成器函数改造成能配合`with`语句使用的上下文管理器——`yield`之前的代码在进入`with`块时执行一次，`yield`把`placeholder`交给`as s`，然后暂停在这里，等`with`块里调用方写的代码执行完（或者中途抛出异常），才会回到`yield`这一行往下继续走`except`/`finally`部分。

这里最值得记住的设计是`finally`块——不管`try`块里的业务代码是正常跑完，还是中途抛了异常被`except`捕获，`finally`都一定会执行到，所以"span被导出"这件事，**不会因为它包裹的业务代码出错就被跳过**。`except`分支里`raise`（不带参数，原样重新抛出刚捕获的异常）也是精心设计的一环：这个方法只是"顺便"记录一下这次失败对应哪个span，绝不会替调用方悄悄吞掉异常本身——调用方依然能像没有这层span包装时一样，正常感知到原本会发生的异常。

留意`parent`参数——`parent: SpanRecord | None = None`要求调用方**显式**传入父span，而不是提供某种"自动从一个全局变量里猜测当前span是谁"的隐式机制。这和第35章讲的租户身份传播形成一个有意思的对比：租户身份用`contextvars`做隐式的按调用链传播是合理的（因为"当前请求属于哪个租户"这件事在一次请求的生命周期内基本不会变化），但"当前span的父子关系"如果也做成隐式全局状态，后台任务/并发场景下很容易把父子关系挂错——显式传参虽然多写几个字，但杜绝了这类容易在事后才发现的错位问题。

## 本章小结

- `SpanRecord.correlation_id`是构造时就必填的字段，而不是事后才想起来补的可选项——避免"HTTP层、追踪层、业务框架层各有一套ID、互相之间没有代码真正串联"的问题重演。
- `_ObservableSpanRecorder`捕获导出异常时，不是简单"抓住然后什么都不做"，而是把每次失败变成一个持续累加、可被外部读取的计数器，外加一个可选的失败回调——让"追踪数据本身有没有正常送达"这件事变得可观察，而不是无声无息地流失。
- `Tracer.span`用`@contextmanager`+`try/finally`保证span无论业务代码是正常结束还是中途抛出异常，都一定会被构造完整并导出；`except`分支里原样`raise`，不会替调用方吞掉原本该抛出的异常。
- `span()`要求父span必须显式传入，不提供"从某个隐式全局状态猜测当前span"的魔法，避免并发/后台任务场景下span的父子关系被挂错。

## 动手做

```python
from ainative_observability.tracing import Tracer, SpanExporter, SpanRecord

class ListExporter:
    def __init__(self):
        self.records: list[SpanRecord] = []
    def export(self, record: SpanRecord) -> None:
        self.records.append(record)

class FlakyExporter:
    def export(self, record: SpanRecord) -> None:
        raise ConnectionError("追踪后端暂时不可达")

exporter = ListExporter()
tracer = Tracer(exporter)

with tracer.span("handle_request", correlation_id="req-1") as parent:
    with tracer.span("call_llm", correlation_id="req-1", parent=parent, model="claude"):
        pass

for r in exporter.records:
    print(r.name, r.correlation_id, r.parent_span_id, f"{r.duration_ms:.2f}ms", r.status)

# 观察导出失败也不会让业务代码崩溃，同时失败计数会真实累加
flaky_tracer = Tracer(FlakyExporter())
try:
    with flaky_tracer.span("will_still_run", correlation_id="req-2"):
        print("即使导出会失败，这段业务代码依然正常执行")
except Exception:
    print("不应该走到这里——导出失败不该冒泡到业务代码")

print("导出失败次数：", flaky_tracer.export_failure_count)
```

## 面试可能会问

**问：如果追踪/监控这类基础设施代码本身出了故障（比如导出到后端失败），你觉得应该怎么处理？**

答题思路：先指出最容易犯的错误——用`try/except`把异常直接吞掉、什么都不做，这样表面上"业务代码没受影响"，但追踪数据可能已经在持续丢失，而且没有任何人能感知到。正确的做法是区分"不能让失败影响主流程"和"不能让失败变得不可见"这两件事——前者靠`try/except`兜住异常，后者靠一个持续累加的失败计数器+可选的失败回调，把"基础设施是否正常工作"变成一个可以被监控、可以触发告警的具体数字，而不是一个只能靠"用户反馈追踪数据缺失"才能发现的隐藏问题。可以进一步提到`correlation_id`应该是构造时的必填项、父子span关系应该显式传递而不是依赖隐式全局状态，展示对分布式追踪设计更完整的理解。
