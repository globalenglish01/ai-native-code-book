# 第21章 —— Checkpoint持久化

代码位置：`packages/ainative-memory/src/ainative_memory/checkpoint.py`

## 半夜三点，进程被重启了

设想你负责维护一个跑得好好的Agent服务：它正在帮一个用户处理一个耗时很长的任务——读了十几个文件、调用了好几次工具、马上就要生成最终报告。这时候运维平台做了一次滚动发布，把这个进程杀掉重启了。用户会看到什么？

如果这个Agent没有做任何"断点续跑"设计，答案是：**用户之前的十几轮交互全部作废，一切从零开始**。这不只是体验差，在生产环境里几乎是不可接受的——真实的Agent任务经常要跑几分钟甚至几十分钟，进程重启（发布、扩缩容、偶发崩溃）几乎是必然会发生的事，不能指望"永远不重启"。

业界对这个问题的标准答案是"checkpoint持久化"：Agent执行到关键节点时，把当前状态写进一个持久化存储（比如Postgres），进程重启后，从最近一次checkpoint恢复，接着往下跑，而不是从头再来。这一章要看的，不是"怎么设计checkpoint的数据结构"（那是LangGraph这类框架自己的事），而是一个更容易被忽视、却同样关键的问题：**负责连接这个持久化存储的那段代码本身，要怎么写才够健壮？**

## 存储句柄从哪来：懒加载 + 单例

`checkpoint.py`模块的docstring交代了它的来历：

> 改造自真实项目里验证过的LangGraph checkpoint持久化设计模式（原版深度耦合`AsyncPostgresSaver`/`psycopg_pool`，这里把"单例懒加载 + 失败分类重试"这个与具体存储后端无关的策略骨架抽出来，具体连接哪种数据库由调用方传入的`build_saver`回调决定）。

也就是说，这个模块压根不知道你连的是Postgres还是Redis还是别的什么——它只关心一件跟具体数据库无关、但几乎所有需要持久化连接的系统都要面对的问题：**连接这个存储句柄的过程本身可能失败，失败之后该怎么办**。

先看构造函数怎么记录状态：

```python
def __init__(
    self,
    # Callable[[], Awaitable[Any]]——一个不接收参数、返回"可等待对象"
    # 的函数，也就是一个异步函数本身，调用它才能拿到真正的存储句柄。
    build_saver: Callable[[], Awaitable[Any]],
    *,
    # 元组里放的是"异常类型"本身（不是异常实例），默认只有ImportError。
    permanent_failure_types: tuple[type[BaseException], ...] = (ImportError,),
    retry_interval_seconds: float = 30.0,
) -> None:
    self._build_saver = build_saver
    self._permanent_failure_types = permanent_failure_types
    self._retry_interval_seconds = retry_interval_seconds

    self._saver: Any | None = None   # 缓存的存储句柄，一开始还没连接，是None
    self._permanently_failed = False   # 是否已经判定为"永久性失败，不再重试"
    self._last_attempt_at: float | None = None   # 上一次真正尝试连接是什么时候
    self._lock = asyncio.Lock()   # 异步锁，防止多个协程同时抢着连接
```

`build_saver`是调用方传进来的一个异步回调——真正去"连接数据库、返回一个能读写checkpoint的句柄"这件事，完全交给它去做。`CheckpointSaverFactory`本身不认识任何具体的数据库客户端，它只知道"调用这个函数，可能成功拿到一个东西，也可能失败"。

`self._saver`一开始是`None`，这就是"懒加载"：不在构造函数里立刻去连接数据库，而是等第一次真正调用`get()`的时候才尝试。这么设计的好处很直接——构造一个`CheckpointSaverFactory`实例本身不需要网络、不需要数据库已经就绪，你可以在应用启动的最早期就把这个对象建好，真正的连接动作推迟到第一次真正需要用到它的时候才发生。一旦连接成功，`self._saver`就把这个句柄缓存下来，之后反复调用`get()`直接复用同一个句柄，不用每次都重新连接——这是"单例"的意思。

## 永久性失败 vs 暂时性失败：不是所有失败都值得重试

这个模块最值得记住的设计决策，是对失败做了分类。模块docstring里说得很清楚：

> **永久性失败 vs 暂时性失败**：`ImportError`（依赖包未安装）之类的错误，重试没有意义——锁定状态，不再重试，直到`reset()`被显式调用（通常在测试里，或者运维确认修复依赖后重启进程）。其余异常（数据库连接抖动等）视为暂时性——在可配置的重试窗口内、按节流间隔重新尝试，故障自愈后自动恢复，不需要重启进程。

想象两种截然不同的故障场景：

第一种，服务器上根本没装连接Postgres所需要的驱动包，`import psycopg`直接抛出`ImportError`。这种情况下，不管你重试多少次，结果都是一样的失败——问题出在"这台机器缺一个包"，不是网络抖了一下就能自愈的。如果程序傻乎乎地每隔几秒钟重试一次，只是在白白浪费CPU、把日志刷满同一条错误，没有任何意义。

第二种，数据库这几秒钟正好在做主从切换，或者网络短暂抖动了一下，`build_saver()`抛出一个`ConnectionError`。这种情况下，过几秒钟再试一次很可能就成功了——这是一个真正"暂时性"的问题，值得自动重试，而不应该被一次失败就彻底锁死。

`get()`方法正是围绕这个区分来写的：

```python
async def get(self) -> Any | None:
    # 第一层检查（不用抢锁）：已经缓存过句柄，直接返回，这是最常见的快速路径。
    if self._saver is not None:
        return self._saver
    if self._permanently_failed:
        return None   # 已经判定永久失败，不再浪费时间尝试

    async with self._lock:   # 需要真正尝试连接了——先拿锁，避免并发重复连接
        # 第二层检查（拿到锁之后）：防止排队等锁期间，别的协程已经连接成功了。
        if self._saver is not None:
            return self._saver
        if self._permanently_failed:
            return None

        now = time.monotonic()
        # 距离上一次尝试还没超过节流间隔——先别再试，直接返回None。
        if self._last_attempt_at is not None and (now - self._last_attempt_at) < self._retry_interval_seconds:
            return None

        self._last_attempt_at = now   # 记录这次尝试的时刻，供下次节流判断用
        try:
            self._saver = await self._build_saver()   # 真正调用调用方传入的连接函数
        except self._permanent_failure_types:
            # 命中了"永久性失败"类型的异常——锁定状态，之后不再自动重试。
            self._permanently_failed = True
            return None
        except Exception:  # noqa: BLE001  故意宽泛捕获,归为暂时性失败
            # 暂时性失败：不锁定，下一次达到 retry_interval_seconds 后会再次尝试。
            return None
        return self._saver   # 连接成功，返回真正的句柄
```

`except self._permanent_failure_types:`这一句是分类的关键——`permanent_failure_types`是一个异常类型组成的元组（默认只有`ImportError`），Python的`except`允许直接传一个元组，表示"这几种异常类型里任意一种发生，都走这个分支"。命中之后，`self._permanently_failed`被设成`True`，之后所有`get()`调用都会在最前面那两行快速检查里直接返回`None`，不会再浪费时间尝试。

而`except Exception:`这个更宽的兜底分支，捕获了除已经被前面分支处理掉的、几乎所有其他类型的异常，全部归类为"暂时性失败"——不设任何锁定标志，只是安静地返回`None`。代码里这一行末尾的`# noqa: BLE001`是留给静态检查工具ruff的一条指令，意思是"这里故意宽泛地捕获`Exception`，请不要因此报警"——因为这里的"宽泛捕获"是刻意设计出来的兜底策略，不是写代码时偷懒漏掉了具体异常类型。

## 节流：故障自愈，但不无限期疯狂重试

光是"分类"还不够——如果每一次暂时性失败之后，下一次`get()`调用立刻又去重试，遇到数据库真的挂了几分钟的场景，会在几分钟内发起成百上千次连接尝试，反而给本就出问题的数据库雪上加霜。`retry_interval_seconds`就是用来解决这个问题的节流阀：

```python
now = time.monotonic()   # 单调递增的计时器读数，不受系统时钟调整影响
# 既要"确实尝试过一次"，又要"离那次尝试还没过够节流间隔"，两个都满足才拒绝这次请求。
if self._last_attempt_at is not None and (now - self._last_attempt_at) < self._retry_interval_seconds:
    return None
```

这里选用`time.monotonic()`而不是`time.time()`的道理，和第11章讲滑动窗口限流时是同一个原因——`monotonic()`返回的计时器只会单调递增，不受系统时钟被人为调整（手动改时间、夏令时切换）影响，专门适合"测量两个时间点之间经过了多久"这类场景，不会因为系统时钟被意外调整而算错重试间隔。

效果是：一次暂时性失败之后，接下来`retry_interval_seconds`秒之内的所有`get()`调用都会直接返回`None`，不会真的去尝试连接；一旦超过这个冷却时间，下一次调用才会真正再试一次。如果那时候数据库已经恢复，这次尝试会成功，`self._saver`被填上值，后续调用直接复用——**整个过程不需要人工干预、不需要重启进程，故障自愈之后系统自动恢复正常**。这和永久性失败的处理方式形成了鲜明对比：一个是"给你机会自愈，但别太频繁"，一个是"没有自愈的可能，别再试了"。

## `asyncio.Lock`：防止好几个协程同时抢着连数据库

再看一遍`get()`方法开头的写法，会发现有两层检查——第一层在拿锁之前，第二层在拿到锁之后。这是"双重检查锁定"（double-checked locking）这个经典并发编程套路，要理解它，得先搞清楚这里为什么需要一把锁。

Python的`asyncio`是标准库里用来写"异步/并发"程序的模块。你可以把它想象成：一个程序里可以同时"挂起"好几段代码（术语叫"协程"），每一段代码在遇到需要等待的操作（比如等网络请求返回）时会主动让出控制权，让其他协程有机会先执行，等真正的结果回来了再继续——这样CPU不会在"傻等"上被浪费，一个进程可以同时高效地处理很多个"正在等待"的任务。

回到这里的场景：假设一个Agent服务刚启动，几乎同时有好几个协程都在处理各自的请求，都需要拿到checkpoint存储句柄，于是几乎同时调用了`factory.get()`。如果没有任何保护机制，这几个协程可能都会看到"`self._saver`还是`None`"，于是都各自去执行`await self._build_saver()`——结果是同一个数据库被连接了好几遍，浪费资源不说，某些数据库客户端在这种"重复初始化"场景下还可能出现更诡异的问题。

`asyncio.Lock`就是用来防止这种情况的一把"只能容纳一个人的房间"式的锁：

```python
self._lock = asyncio.Lock()
```

```python
async with self._lock:
    ...
```

`async with self._lock:`的意思是："进入这个代码块之前，先检查这把锁有没有被别的协程占用；如果占用了，就在这里排队等待，直到锁被释放；拿到锁之后才继续往下执行代码块内部的逻辑；代码块结束时（不管是正常结束还是中途抛出异常），自动释放锁，让排在后面的协程能拿到它"。

现在回头看"双重检查"为什么必要：假设协程A和协程B几乎同时调用`get()`，都在最外层看到`self._saver`是`None`、都决定要去抢锁。协程A先抢到，进入代码块，真正执行`await self._build_saver()`并成功缓存了`self._saver`；协程A执行`async with`代码块结束，锁被释放。这时候协程B才轮到抢锁——**如果协程B进锁之后不再检查一次`self._saver`是否已经有值，它会重复执行一遍完整的构建逻辑**，白白多连接一次数据库。所以拿到锁之后必须"再检查一遍"：

```python
async with self._lock:
    if self._saver is not None:
        return self._saver
    if self._permanently_failed:
        return None
    ...
```

只有这一遍检查也确认"确实还没人构建过"，协程B才会真正去执行构建逻辑。这就是"双重检查锁定"：第一次检查是为了在绝大多数场景下（句柄已经缓存好）走一条完全不用排队等锁的快速路径；第二次检查是为了防止"抢锁排队期间，别人已经把事情做完了"这种竞态情况下的重复劳动。

## `reset()`：给测试和运维留一个逃生舱

```python
def reset(self) -> None:
    self._saver = None   # 清空已缓存的句柄，下次get()会重新尝试连接
    self._permanently_failed = False   # 解除"永久性失败"锁定
    self._last_attempt_at = None   # 清空上次尝试时刻，节流计时重新开始
```

这个方法不是`async`的——它只是把几个状态变量清零，没有任何需要"等待"的操作，因此没必要写成异步方法。它的价值在于给两类场景留了一个显式的"重新开始"入口：一类是自动化测试之间需要相互隔离，不能让上一个测试用例留下的缓存句柄或"永久性失败"标志影响到下一个测试；另一类是真实运维场景——如果运维人员发现是因为忘了给容器镜像装某个依赖包才触发了永久性失败，装好依赖之后，理论上不需要重启整个进程，调用一次`reset()`就能让下一次`get()`重新尝试。当然，多数生产环境里"重启进程"往往比"调用一个内部reset方法"更简单可靠，但这个方法的存在，至少说明设计者认真考虑过"永久性失败不代表永远无法恢复，只是代表'不应该自动重试'"这件事。

## 本章小结

- Checkpoint持久化解决的是"进程重启后能不能从中断点继续"的问题；这一章关注的是更底层的一环——负责连接持久化存储的代码本身要如何健壮。
- 失败要分类处理：`ImportError`这类"重试也没用"的错误应该归为永久性失败，锁定状态直到显式`reset()`；其余异常归为暂时性失败，按节流间隔自动重试，故障自愈后不需要重启进程。
- `time.monotonic()`用来做重试节流计时，不受系统时钟调整影响。
- `asyncio.Lock`配合"双重检查锁定"，防止多个协程并发调用时重复构建存储句柄——第一次检查是快速路径，锁内的第二次检查是为了防止排队期间别人已经做完了。
- 懒加载 + 单例缓存意味着构造`CheckpointSaverFactory`本身不需要网络/数据库就绪，真正的连接推迟到第一次真正需要时才发生。

## 动手做

```python
import asyncio
from ainative_memory.checkpoint import CheckpointSaverFactory

attempt_count = 0   # 模块级变量，用来记录这个演示函数被调用了几次

async def flaky_build_saver():
    # global——声明要修改的是外层的attempt_count这个模块级变量，
    # 而不是在函数内部创建一个同名的新局部变量。
    global attempt_count
    attempt_count += 1
    if attempt_count < 3:
        raise ConnectionError("数据库暂时连不上")   # 前两次故意失败，模拟暂时性故障
    return {"handle": "fake-saver"}   # 第三次开始"恢复正常"，模拟故障自愈

async def main():
    # retry_interval_seconds=0——把节流时间设为0，这样演示时不需要真的等待。
    factory = CheckpointSaverFactory(flaky_build_saver, retry_interval_seconds=0)
    for i in range(4):
        saver = await factory.get()
        print(f"第{i + 1}次get():", saver)

asyncio.run(main())
```

把`retry_interval_seconds`设成0是为了在这个小实验里跳过节流等待，方便连续观察"暂时性失败→重试→最终成功"的完整过程。试着把`flaky_build_saver`改成直接抛`ImportError`，观察后面几次`get()`是不是都直接返回`None`、不再真的调用`flaky_build_saver`。

## 面试可能会问

**问：如果你的服务依赖一个外部存储（数据库/缓存），连接这个存储的代码应该怎么设计才算健壮？**

答题思路：先提出"不是所有失败都应该被同样对待"这个核心观点——依赖包缺失这类"重试没有意义"的错误应该被识别出来并停止重试，避免无意义地刷错误日志；网络抖动这类"过一会儿可能自愈"的错误则应该在节流间隔控制下自动重试。再补充并发安全的角度：多个请求同时触发"首次连接"时，要用锁（比如`asyncio.Lock`）加双重检查，防止重复初始化。如果能提到"用`time.monotonic()`而不是`time.time()`做重试间隔计算，避免系统时钟调整带来的误差"，会体现出对细节的把控。
