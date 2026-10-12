# core/push_scheduler.py

## 设计思路

`PushScheduler.run()`是一个`while not stop_event.is_set()`的无限循环，每轮调`_tick()`，然后用`asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval_sec)`代替`asyncio.sleep`——这样`stop()`调用后能立刻从等待中唤醒退出，而不是傻等到下一个轮询周期才发现要停。`main.py`在`__init__`里用`asyncio.create_task(self._scheduler.run())`启动，`terminate()`里调`stop()`。

## 设计目的

把"轮询赛程判断该干什么"和"具体怎么连SignalR/怎么发消息"分开。`_tick()`只做判断(该发开赛通知吗？该连/断race control吗？)，真正干活的分别委托给`context.send_message`和`LiveTimingClient`。这样`_tick()`本身逻辑很薄，容易看懂控制流。

## 注意事项/坑

- **race control连接是"按需建立/断开"而非常驻连接**，这是刻意的设计权衡：
  - 好处：不比赛的时候完全不占用SignalR连接资源，不需要长期维持一个WebSocket。
  - 代价（**已知限制，写在这里提醒**）：如果一个群是在比赛进行到一半才执行`/f1控制 正赛 开`开启订阅的，它只能从"下一条新消息"开始收到推送，**收不到比赛开始到订阅这段时间已经发生的race control消息**。因为`_handle_race_control`只处理SignalR实时推送的增量/当次连接拿到的快照，不会主动去补历史。
  - 如果以后想做成"随时能看到当前session已发生的全部消息"，需要改成连接常驻(session活跃期间不管有没有目标群都保持连接)，或者在用户新开启订阅时主动查一次`RaceControlMessages`的`KeyFramePath`静态快照（F1 static timing API）作为补偿，目前都没做。
- **`_maybe_manage_live_timing`的连接/断开判断逻辑**(重点，改的时候容易引入bug):
  ```
  should_connect = in_window and bool(targets)
  if should_connect:
      如果没连接，或连接的不是当前session_id -> 先断旧的(如果有)，再连新的
  else:
      如果当前有连接 -> 断开
  ```
  **这里有个容易踩的坑**：早期版本只在`self._live_session_id != session_id`时才断开，导致"同一个session但目标群后来变空了"这种情况不会触发断开（因为session_id没变，走不到判断分支）。现在的写法是`should_connect`为`False`时无条件断开，不管是因为窗口过了还是因为目标群空了，统一处理，已经修复这个问题——以后改这块逻辑要保持"else分支无条件断开"这个性质。
- **`_handle_session_status`提前断开未经真实比赛验证**: `_SESSION_END_STATUSES = {"Finalised", "Finalized", "Ends", "Inactive"}`这几个值是猜测/参考已知资料拼的，实测只验证过收到过`{"Status": "Ends", "Started": "Finished"}`这一种真实样本(来自上一场已结束正赛的历史快照)。真实比赛中正常结束时是否一定会推送这几个值之一、会不会有别的值，还没有实测验证，第一次真实比赛时要重点观察日志确认连接有没有在比赛结束后正常断开，没断开的话要检查实际收到的`Status`值是不是不在这个集合里。
- **`pushed:{session_id}`去重集合有长度上限`_PUSHED_IDS_LIMIT=500`**，每次写入时只保留最后500个id。正常一场比赛的race control消息量远低于500条，这个上限只是防止极端情况下KV数据无限增长，不是性能瓶颈点。
- **`session_id`的构造**: `f"{weekend.season}-{weekend.round_no}-{key}-{session.start.isoformat()}"`，用于区分"现在该连哪个session"和"KV里该用哪个key存通知/去重状态"。注意`session.start.isoformat()`里本身含有`-`和`:`，如果以后要从`session_id`反解析出某个字段，不能简单按`-`分割（之前的实现版本有这个bug，已改成`_start_live_timing`时把`key`作为独立参数传入闭包，不再从`session_id`字符串里解析）。
- **异常隔离**: `run()`里每次`_tick()`都包了一层try/except，单次tick异常只log不中断循环；`_maybe_send_start_notice`/`_handle_race_control`里对每个群的`send_message`单独try/except，一个群发送失败不影响其他群。

## 待办

- `_handle_session_status`、`_SESSION_END_STATUSES`的真实有效性依赖第一次真实比赛观察（见上面"坑"部分），如果发现断开时机不对需要回来调整这个集合。
- 没有做"连接异常后的退避重连"——`_start_live_timing`里的`_runner`如果`live_client.run()`抛异常，只是log，不会在同一个tick内重试；要等到下一次`_tick()`(最多等`poll_interval_sec`秒)才会重新判断`currently_connected`（这时`self._live_task.done()`为`True`）然后重连。正常情况下60秒的重连延迟可以接受，但如果希望更快恢复，可以考虑给`_runner`加一个内部重试循环。
- 开赛通知目前是"到点即发"(`_maybe_send_start_notice`里直接比较`now < session.start`)，没有"提前N分钟通知"的能力。如果以后要加，需要在这个时间比较上减去一个提前量，并在`_conf_schema.json`里加一个对应配置项。
