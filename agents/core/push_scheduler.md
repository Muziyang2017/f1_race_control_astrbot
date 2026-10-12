# core/push_scheduler.py

## 设计思路

`PushScheduler.run()`是一个`while not stop_event.is_set()`的无限循环，每轮调`_tick()`，然后用`asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval_sec)`代替`asyncio.sleep`——这样`stop()`调用后能立刻从等待中唤醒退出，而不是傻等到下一个轮询周期才发现要停。`main.py`在`__init__`里用`asyncio.create_task(self._scheduler.run())`启动，`terminate()`里调`stop()`。

## 设计目的

把"轮询赛程判断该干什么"和"具体怎么连SignalR/怎么发消息"分开。`_tick()`只做判断(该发开赛通知吗？该连/断race control吗？)，真正干活的分别委托给`context.send_message`和`LiveTimingClient`。这样`_tick()`本身逻辑很薄，容易看懂控制流。

## 注意事项/坑

- **`_handle_race_control`里的过滤和去重是两件独立的事，顺序不能搞反**: 对每条raw消息，先算`event_id`并计入`pushed_ids`去重集合(不管这条消息最后推不推送，都要标记成"处理过"，否则被过滤的消息会一直卡在"未去重"状态，每次tick重复判断)，然后才判断`race_control_formatter.is_filtered()`决定要不要格式化/加入待推送列表。换句话说："去重"管的是"这条消息我见过没见过"，"过滤"管的是"见过的消息里哪些值得发"，两个维度不要合并成一个判断，否则以后改配置(比如清空`race_control_excluded_flags`)会导致之前已经"见过但被过滤"的消息又重新被处理一遍的问题（不过目前实现不会有这个问题，因为`pushed_ids`已经提前标记了）。
- **`_maybe_force_refresh_schedule`：session开始前24h/1h/10min三个检查点强制刷新赛历**。背景：`JolpicaClient`的schedule缓存TTL是6小时(见[jolpica_api.md](jolpica_api.md))，如果赛历临时变更(天气延期等)而缓存还没过期，`_tick()`里用的`session.start`就是旧数据，开赛通知/race control连接窗口都会算错。这三个检查点就是在"决策点越来越近"的时候主动戳一下jolpica拿最新数据，而不是傻等6小时TTL自然过期。
  - 每个检查点(`"24h"`/`"1h"`/`"10m"`)按`session_id`存一个KV flag(`refreshed:{session_id}:{label}`)，一个session的同一个检查点只会触发一次强制刷新，不会每次tick都重复打请求。
  - **合批优化**：如果多个检查点同时到期(比如插件刚启动、session只剩几分钟了，三个门槛一次性全部满足)，只会真正发**一次**`force=True`请求，但会把所有到期的检查点flag一起标记掉——不是"错过的检查点要分别补刷新"，而是"反正数据已经刷新过了，补标记省得以后白跑判断"。已用模拟测试验证过这个合批行为。
  - 刷新之后`_tick()`会重新调一次`find_active_or_upcoming_session`(这次读的是刚刷新过的缓存，不是重复发网络请求)，意味着**如果赛历真的变了，这一次tick内`weekend`/`session`/`session_id`可能和刷新前不一样**——下面的开赛通知/连接管理用的是刷新后的新值，这是故意的（宁可用最新数据重新判断，也不要用刷新前已经可能过期的候选）。
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
- **提前断开依赖两个独立的结束信号，命中任一个就断**，分别来自`SessionStatus`流和`SessionInfo`流——这是两个不同的流、字段名也容易搞混，别改错：
  - `_handle_session_status`处理`SessionStatus`流，检查顶层`Status`字段是否在`_SESSION_STATUS_END_VALUES = {"Finalised", "Finalized", "Ends", "Inactive"}`里。
  - `_handle_session_info`处理`SessionInfo`流，检查`SessionStatus`字段(payload里的key叫`SessionStatus`，跟流名`SessionInfo`不是一回事，看代码时容易看串)是否在`_SESSION_INFO_END_VALUES = {"Finalised", "Finalized"}`里。
  - 两个都已经用真实历史数据验证过：`SessionStatus`流真实样本是`{"Status": "Ends", "Started": "Finished"}`；`SessionInfo`流真实样本是`{"SessionStatus": "Finalised", "ArchiveStatus": {"Status": "Complete"}, ...}`。样本抓的是2026新加坡站正赛结束后的历史快照，存在`debug/race_control_raw_sample.json`(未提交到git，纯本地调试用，`.gitignore`已排除`debug/`)。`SessionInfo`这个信号更直接(官方明确标注"Finalised"+存档完整)，加进来是为了给原本只验证过一种样本的`SessionStatus`信号加一道独立交叉验证，降低"猜的值不对导致断不开"的风险。
  - `_Finalized`(美式拼写)目前是猜测的兼容写法，没有真实样本验证过，实际F1接口暂时只见过`Finalised`(英式拼写)。
  - 没验证过的场景：真实比赛**进行中**正常结束时的实时推送(目前两个样本都是赛后查历史快照拿到的，不是比赛结束瞬间親眼看着它从"进行中"变成"Finalised"的)。第一次真实比赛要重点看日志确认连接在比赛结束后正常断开。
- **`pushed:{session_id}`去重集合有长度上限`_PUSHED_IDS_LIMIT=500`**，每次写入时只保留最后500个id。正常一场比赛的race control消息量远低于500条，这个上限只是防止极端情况下KV数据无限增长，不是性能瓶颈点。
- **`session_id`的构造**: `f"{weekend.season}-{weekend.round_no}-{key}-{session.start.isoformat()}"`，用于区分"现在该连哪个session"和"KV里该用哪个key存通知/去重状态"。注意`session.start.isoformat()`里本身含有`-`和`:`，如果以后要从`session_id`反解析出某个字段，不能简单按`-`分割（之前的实现版本有这个bug，已改成`_start_live_timing`时把`key`作为独立参数传入闭包，不再从`session_id`字符串里解析）。
- **异常隔离**: `run()`里每次`_tick()`都包了一层try/except，单次tick异常只log不中断循环；`_maybe_send_start_notice`/`_handle_race_control`里对每个群的`send_message`单独try/except，一个群发送失败不影响其他群。

## 待办

- 三个强制刷新检查点降低了"用过期赛历数据做决策"的风险，但不是100%消除：检查点之间(比如1h到10min之间)如果赛历突然变了，还是要等到下一个检查点才会发现，最坏情况下残留的"数据过期窗口"缩小到了"10min检查点之后"这段，而不是原来的最多6小时。如果想做到更实时，可以把10min这个检查点再加密(比如再加一个"开始前2min"的检查点)，但收益递减，目前没有继续加。
- `_handle_session_status`/`_handle_session_info`两个结束信号的真实有效性依赖第一次真实比赛观察（见上面"坑"部分），如果发现断开时机不对需要回来调整`_SESSION_STATUS_END_VALUES`/`_SESSION_INFO_END_VALUES`这两个集合。
- 没有做"连接异常后的退避重连"——`_start_live_timing`里的`_runner`如果`live_client.run()`抛异常，只是log，不会在同一个tick内重试；要等到下一次`_tick()`(最多等`poll_interval_sec`秒)才会重新判断`currently_connected`（这时`self._live_task.done()`为`True`）然后重连。正常情况下60秒的重连延迟可以接受，但如果希望更快恢复，可以考虑给`_runner`加一个内部重试循环。
- 开赛通知目前是"到点即发"(`_maybe_send_start_notice`里直接比较`now < session.start`)，没有"提前N分钟通知"的能力。如果以后要加，需要在这个时间比较上减去一个提前量，并在`_conf_schema.json`里加一个对应配置项。
