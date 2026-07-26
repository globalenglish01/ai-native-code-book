"""任务队列积压监控 + 匀速消费——背压不是被动发现，而是主动预警。

改造自checklist D类真实设计要点："队列/后台任务积压时是否有监控和背压
机制（提前告警、临时扩容、或在源头限制新请求），而不是让积压无限增长、
最终才被动发现"；以及"消息队列/后台任务是否按下游服务允许的速率匀速
消费，而不是一股脑并发调用撞上限速墙"。

设计原则（沿用`GuardHealthMonitorMiddleware`同一套"纯旁路预警，不改变
终止判断"的定位）：
1. **提前告警而不是事后发现**：`QueueBacklogMonitor`在队列深度超过阈值
   的那一刻就发出结构化WARNING，而不是等到下游超时/内存耗尽才暴露问题；
   每次跨越阈值只告警一次（去抖），避免刷屏。
2. **入队速率限制是"源头限制"而不是"消费端限流"**：`RateLimitedConsumer`
   保证真正被处理的任务不超过下游允许的速率，如果需要处理的任务比
   速率允许的更快到达，调用方必须显式等待（`await acquire()`），而不是
   把所有任务都塞给下游、指望下游自己扛住。
"""

from __future__ import annotations

# logging是Python标准库自带的"日志"模块——见model_router.py里的详细解释。
import logging

# time模块用来获取"现在的时间"——见ainative_core.usage_tracking模块里
# 对time.time()的详细解释；这里额外用到的time.monotonic()见下面
# RateLimitedConsumer类里的详细解释。
import time

# deque（"double-ended queue"，双端队列的缩写，发音同"deck"）是Python
# 标准库collections模块提供的一种数据结构——和普通列表list很像，都能
# "按顺序存一串数据"，但deque在"从最前面/最后面添加或删除元素"这类
# 操作上，速度比list快得多（list从最前面删除元素需要把后面所有元素
# 都往前挪一位，deque不需要）。这里用来维护"最近一段时间窗口内，
# 分别在什么时刻发生过调用"的时间戳列表。
from collections import deque

logger = logging.getLogger(__name__)


class QueueBacklogMonitor:
    """跟踪队列深度，超过阈值时主动告警——不依赖被动发现积压。

    用法::

        monitor = QueueBacklogMonitor(warn_threshold=100)
        monitor.record_depth(queue.qsize())  # 每次入队/出队后调用
    """

    def __init__(self, *, warn_threshold: int) -> None:
        self._warn_threshold = warn_threshold
        # 记录"当前是否已经处于告警状态"——用来实现"深度超过阈值时只
        # 告警一次，不会每次调用record_depth都重复打印警告"这个去抖
        # 效果。
        self._warned = False
        # `peak_depth`没有下划线前缀，是一个公开属性——记录"从这个
        # 监控器创建以来，观测到过的最大队列深度"，供外部代码直接读取
        # 查看历史峰值，不需要通过额外的方法。
        self.peak_depth = 0

    def record_depth(self, current_depth: int) -> bool:
        """记录一次队列深度快照；深度超过阈值且此前未告警过时打一条
        结构化WARNING并返回`True`（供调用方决定要不要额外触发扩容/限流
        动作），深度回落到阈值以下后允许再次告警（避免"一次告警后哪怕
        真的持续积压也再也不会提醒"）。"""
        # `max(self.peak_depth, current_depth)` ——把这次观测到的深度
        # 和历史最高值比较，取较大的那个，更新峰值记录。
        self.peak_depth = max(self.peak_depth, current_depth)
        if current_depth >= self._warn_threshold:
            if self._warned:
                # 已经处于告警状态（上一次调用就已经超过阈值并告警
                # 过了），这次虽然仍然超过阈值，但不重复打印日志，
                # 返回False表示"这次没有新触发一次告警"。
                return False
            # 第一次跨越阈值——真正打印警告日志，并把_warned标记为True，
            # 之后只要深度继续保持在阈值以上，都不会再重复告警，直到
            # 深度回落（见下面的else分支）。
            self._warned = True
            logger.warning(
                "[Backpressure] queue depth %d has reached the warn threshold (%d)",
                current_depth, self._warn_threshold,
                extra={"guard_health_event": "queue_backlog", "depth": current_depth, "threshold": self._warn_threshold},
            )
            return True
        # 走到这里，说明这次的深度已经回落到阈值以下——把_warned重置成
        # False，这样"下一次如果深度又重新涨回阈值以上"，会被当作一次
        # 全新的告警重新打印出来，而不是因为"之前告警过"就被永久静音。
        self._warned = False
        return False


class RateLimitedConsumer:
    """限制真正到达下游的调用速率——用滑动窗口计数，而不是固定间隔的
    简单sleep（固定间隔在窗口边界附近容易允许短时间内的突发超速）。
    """

    def __init__(self, *, max_calls: int, window_seconds: float) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        # `deque[float]` ——一个只存浮点数的双端队列，用来记录"最近
        # 发生过的每一次调用，各自是在什么时刻发生的"（时间戳列表）。
        # 空的deque表示"目前还没有任何调用记录"。
        self._call_times: deque[float] = deque()

    def _prune_expired(self, now: float) -> None:
        # `cutoff` 是"滑动窗口"的起始边界——早于这个时刻的调用记录，
        # 已经不算在"最近window_seconds秒内"了，需要被清理掉。
        cutoff = now - self._window_seconds
        # `while self._call_times and self._call_times[0] <= cutoff:`
        # ——只要队列不为空（`self._call_times`非空时布尔值为True），
        # 且队列最前面的那个时间戳（最早发生的一次调用）已经早于/等于
        # cutoff，就把它从队列最前端弹出丢弃；循环往复，直到队列为空
        # 或者剩下的都是"仍在窗口期内"的记录为止。因为deque里的记录
        # 天然是按时间先后顺序追加的（越早的排在越前面），只需要不断
        # 检查/移除最前面的元素，就能高效清理掉所有过期记录，不需要
        # 遍历整个队列。
        while self._call_times and self._call_times[0] <= cutoff:
            # `.popleft()` 是deque特有的方法，作用是"从队列最前端取出
            # 并移除一个元素"——这正是"双端队列"比普通list更适合这个
            # 场景的地方：list也能做"删除第一个元素"，但deque做这件事
            # 的效率高得多。
            self._call_times.popleft()

    def time_until_next_slot(self) -> float:
        """距离下一次可以真正发起调用还需要等待多久（秒）；0表示现在
        就可以调用。调用方决定怎么等待（同步sleep/异步sleep/放回队列
        稍后重试），本方法只负责计算，不强制睡眠方式。"""
        # `time.monotonic()` 和`time.time()`不同：`time.time()`返回的
        # 是"墙上时钟"时间，可能因为系统时间被人工调整（比如手动改了
        # 系统时间、或者网络对时校准）而突然跳跃；`time.monotonic()`
        # 返回的是一个"只会单调递增、不受系统时间调整影响"的时间值，
        # 只适合用来"计算两个时刻之间经过了多少时间"，不能当作真实的
        # 日历时间使用。这里用monotonic是因为"限流窗口"关心的是"经过了
        # 多长时间"，用它比用真实墙上时间更可靠、不会因为系统时间被
        # 调整而算错。
        now = time.monotonic()
        # 先清理掉窗口之外的过期记录，保证接下来判断"当前窗口内有多少
        # 次调用"时，数字是准确的。
        self._prune_expired(now)
        if len(self._call_times) < self._max_calls:
            # 当前窗口内的调用次数还没达到上限，现在就可以发起调用，
            # 不需要等待。
            return 0.0
        # 窗口已经满了——要等到"最早的那一次调用"完全滑出窗口之后，
        # 才能腾出一个新的调用名额。`oldest`是窗口内最早的调用时刻，
        # `oldest + self._window_seconds`就是"这条记录滑出窗口的具体
        # 时刻"，减去现在的时刻就是"还需要再等多久"。
        oldest = self._call_times[0]
        # `max(0.0, ...)` 防止因为极小的计算误差算出一个微小的负数，
        # 保证返回值至少是0（不会出现"需要等待-0.001秒"这种没有意义
        # 的结果）。
        return max(0.0, (oldest + self._window_seconds) - now)

    def record_call(self) -> None:
        """真正发起了一次下游调用之后调用——把这次调用计入速率窗口。"""
        # 把"现在"这个时刻追加到队列末尾，记录"刚刚真的发生了一次调用"。
        self._call_times.append(time.monotonic())

    @property
    def current_call_count(self) -> int:
        """当前速率窗口内已经记录的调用次数——供监控/测试查看瞬时占用。"""
        # 先清理过期记录，再返回队列剩余的长度，保证这个数字反映的是
        # "此刻真正落在窗口期内"的调用次数，而不包含早已过期的旧记录。
        self._prune_expired(time.monotonic())
        return len(self._call_times)
