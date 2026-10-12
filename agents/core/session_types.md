# core/session_types.py

## 设计思路

把jolpica返回的赛历数据(`RaceWeekend`/`SessionInfo`)和"5类赛段"这个业务概念做一层转换，是整个插件里"赛段"相关逻辑的唯一真相来源(Single Source of Truth)。main.py、push_scheduler.py、subscriptions.py都依赖这里定义的`SESSION_KEYS`/`SESSION_LABELS`/`LABEL_TO_KEY`。

## 设计目的

用户需求明确要求5类赛段(练习赛/冲刺排位赛/排位赛/冲刺赛/正赛)分别控制开关，但jolpica原始数据里练习赛拆成了FirstPractice/SecondPractice/ThirdPractice三个字段。这里做合并：三个字段统一映射成`FP`这一个key，和需求的开关粒度对齐，不会出现"用户只能关FP1不能关FP2"这种不符合需求的情况。

## 注意事项/坑

- **字段名兼容**: `SprintQualifying`和`SprintShootout`两个jolpica字段名都映射到`SQ`，原因见 [jolpica_api.md](jolpica_api.md) 的字段名坑。
- **时区处理**: jolpica返回的时间是UTC(`date`+`time`字段，`time`形如`"05:00:00Z"`)，`_parse_dt`统一转成tz-aware的`datetime`(UTC)。后续`push_scheduler`里所有时间比较都基于UTC，`/赛历`指令展示给用户时才用`.astimezone()`转本地时区，这个转换点不能搞反，否则比较逻辑会出错。
- **预估时长表是经验值，不是官方数据**: `ESTIMATED_DURATION_MIN`(FP≈70min, Q≈70min, SQ≈50min, SPRINT≈40min, RACE≈180min)+30min buffer，纯粹是按正常比赛节奏估的。**最大的坑**：正赛如果出红旗中断(赛会叫停重新发车)，实际时长可能远超预估+buffer，这种情况下`find_active_or_upcoming_session`会在窗口结束后把"当前关注的session"切到下一场，导致`push_scheduler`提前断开race control连接，漏掉比赛后段的消息。目前没有更精确的时长来源，只能指望`SessionStatus`里的结束状态字段做兜底(见 [live_timing.md](live_timing.md))，但这个字段的实际行为还没在真实超时比赛中验证过。
- **`find_active_or_upcoming_session`的返回语义**: 优先返回"正处于窗口内"的session；如果没有任何session在窗口内，返回"时间上最近的未来session"（不代表它已经开始）。调用方(`push_scheduler._maybe_send_start_notice`)自己要判断`now >= session.start`才发通知，不要误以为这个函数返回的就是"已开始"的session。

## 待办

- 如果jolpica后续给出官方的session实际结束时间（目前API不提供），`ESTIMATED_DURATION_MIN`这套经验值机制可以整个替换掉。
- 没有处理"赛程临时变更"(例如天气导致延期)的情况，依赖jolpica自己更新数据，而且赛历缓存有6小时TTL，极端情况下变更后6小时内还会用旧数据判断，影响开赛通知的准确时间点。
