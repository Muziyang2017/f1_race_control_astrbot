# astrbot_plugin_f1_race_control — agent知识库入口

## 项目是什么

AstrBot插件，QQ群用。两类功能：

1. 手动查询：`/赛历` `/车手积分` `/车队积分`
2. 主动推送：比赛开始通知 + race control实时消息，按5类赛段(练习赛/冲刺排位赛/排位赛/冲刺赛/正赛)分别开关，支持全局默认(WebUI配置)+群内指令覆盖两层控制。

## 整体数据流(文字版)

```
查询类:
  群内指令(main.py) --> JolpicaClient(core/jolpica_api.py) --> jolpica-f1 API --> 格式化文本回复

推送类:
  PushScheduler(core/push_scheduler.py) 每60s tick一次:
    1. 刷新赛历缓存(JolpicaClient)
    2. 算出当前该关注的session(core/session_types.py: find_active_or_upcoming_session)
    3. 开赛通知: 到点且未通知过 -> 查SubscriptionStore算目标群 -> context.send_message
    4. race control窗口判断: 在窗口内且有目标群 -> 按需启动LiveTimingClient(core/live_timing.py)
       连SignalR -> 收到RaceControlMessages -> race_control_formatter格式化+去重 -> send_message
       窗口结束/目标群清空/收到SessionStatus结束状态 -> 断开连接
```

## 模块索引

- [main.md](main.md) — 指令层(main.py)
- [core/jolpica_api.md](core/jolpica_api.md) — 赛历/积分数据源
- [core/session_types.md](core/session_types.md) — 赛段key体系、下一场赛事/当前赛段查找
- [core/live_timing.md](core/live_timing.md) — F1官方SignalR实时流客户端
- [core/race_control_formatter.md](core/race_control_formatter.md) — race control消息格式化+去重
- [core/subscriptions.md](core/subscriptions.md) — 群级开关覆盖存取
- [core/push_scheduler.md](core/push_scheduler.md) — 后台调度主循环

## 全局约定

- **5类赛段key**: `FP`(练习赛，含P1/P2/P3)/`SQ`(冲刺排位赛，jolpica里叫SprintQualifying或SprintShootout)/`Q`(排位赛)/`SPRINT`(冲刺赛)/`RACE`(正赛)。定义在 `core/session_types.py`。
- **KV key命名**: `known_groups`(登记过的群umo列表) / `group_cfg:{umo}`(群覆盖配置) / `notified:{session_id}`(开赛通知去重) / `pushed:{session_id}`(race control消息去重，session_id格式见push_scheduler)。
- **覆盖值语义**: 群配置里某赛段的值为`None`/缺失 = 跟随全局默认(WebUI配置的 `enable_start_notice`/`enable_race_control`)，不是"关闭"。
- **指令风格**: 查询类指令用中文纯中文名(`/赛历`等)；管理类指令用`f1`前缀(`/f1订阅本群` `/f1通知` `/f1控制` `/f1状态`)，避免和查询指令混淆。

## 已知限制速查(详见各模块文档"坑"部分)

- jolpica-f1 若遇到全新赛季初期数据未录入，`/赛历`等指令会直接报错，没做静态API fallback。（2026赛季已验证可用，见 jolpica_api.md）
- AstrBot没有"列出所有群"的官方API，目标群只能靠WebUI配置的`default_group_ids`或群内`/f1订阅本群`登记，机器人没加的群不会出现在候选里。
- race control连接按需建立/断开，不是常驻连接——比赛中途才订阅的群会错过之前的消息。
- 正赛若红旗中断导致大幅超时，可能超出预估时长+buffer窗口，调度器会提前断开(即使SessionStatus没发结束信号)，这种情况下可能漏掉比赛后段的race control消息。
