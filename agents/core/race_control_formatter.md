# core/race_control_formatter.py

## 设计思路

纯函数模块，不依赖网络/状态，只做"RaceControlMessages原始payload -> 中文播报文本 + 去重ID"的转换。拆出来单独一个文件是为了可以脱离SignalR连接单独测试格式化逻辑(已用真实抓到的历史消息验证过，见下方"注意事项")。

## 设计目的

F1官方race control消息本身是英文、结构化字段(Flag/Category/Sector/Lap等)，直接转发原文可读性差。这里按`Flag`/`Category`加中文标签+emoji前缀，让QQ群消息更直观(例如看到🔴红旗能立刻反应过来，不用读英文Flag字段)。

`is_filtered()`则是另一半职责：决定一条消息该不该被推送。放在同一个文件是因为过滤规则(按`Flag`值判断)和格式化规则共享同一份"认识Flag/Category字段"的知识，拆开反而要重复定义一遍字段语义。

## 注意事项/坑

- **`is_filtered`按`Flag`值过滤，不是按`Category`**: 用真实历史数据(一场正赛153条消息)验证过，`Flag:"BLUE"`(赛车被套圈需要让道)占了71条，接近一半，默认配置`race_control_excluded_flags=["BLUE"]`就是冲着这个来的。大小写不敏感(内部统一转大写比较)，所以WebUI配置里填`"blue"`也生效。只按`Flag`过滤是刻意选择——用户只提了蓝旗这一个诉求，没有做按`Category`过滤(比如想把`Other`类的刷车限时间删除消息也过滤掉)，如果以后要加，照抄`is_filtered`的模式加一个新函数/新配置项就行，不用改这个函数本身。
- **过滤判断在调用方(`push_scheduler._handle_race_control`)做，不在`format_message`里短路**: 这样`format_message`永远是"给什么payload就老实格式化"的纯函数，不用关心"这条是不是应该被过滤"这种业务决策，过滤是调度层的事。

- **字段不保证齐全，必须容错**: 实测抓到的真实消息里，同一个`Category:"Other"`的消息可能完全没有`Flag`/`Sector`字段，只有`Message`文本；`Category:"SafetyCar"`的消息有`Status`/`Mode`字段但也没有`Flag`。`format_message`的逻辑是：有`Flag`就优先用`_FLAG_LABELS`映射，没有则退回`_CATEGORY_LABELS`按`Category`映射，再兜底成"📢赛事通告"。`Utc`/`Message`都可能为空，为空直接返回`None`(调用方要检查这个返回值，不发空消息)。
- **emoji/中文标签映射表是人工拟定，非官方标准**。已验证覆盖到的真实Flag值：`GREEN`/`YELLOW`/`DOUBLE YELLOW`/`RED`(理论值，未实测遇到过)/`CHEQUERED`/`BLUE`/`CLEAR`/`BLACK AND WHITE`。已验证覆盖到的真实Category值：`SafetyCar`/`Flag`/`Other`（`Drs`/`CarEvent`是预留的，实测数据里没遇到，如果以后发现实际用的Category名字不是这两个，需要回来改`_CATEGORY_LABELS`）。
- **`event_id`去重公式`utc|flag|sector|text`是照搬F1-RaceControl-Desktop安卓版的**(`F1Feed.java`里的`toMessage()`逻辑)，不是本项目自己设计的。好处是同一条消息即使被F1服务端重复推送多次也能去重；坏处是如果两条**不同**消息恰好utc/flag/sector/text完全一样会被误判成重复（实测没遇到过，理论上可能发生在极短时间内重复触发同一个事件的场景）。
- **`extract_messages`兼容`Messages`字段的两种shape**: 可能是JSON数组，也可能是以字符串索引("0","1","2"...)为key的dict(SignalR增量更新单条消息时常见这种"只更新索引N"的局部payload形状)。`sort_key`函数优先按数字排序，非数字key的排在后面兜底，避免`int()`转换抛异常。

## 待办

- 目前没有对`Message`文本做任何翻译，只加了中文标签前缀，正文还是纯英文。如果后续想要全中文播报，需要接入翻译或者维护一个常见消息模板的中英对照表(例如"GREEN LIGHT - PIT EXIT OPEN"这类固定句式其实可以做规则翻译)。
- `RED`旗和`Drs`/`CarEvent`两个Category目前只是理论映射，没有真实数据验证过展示效果，等真遇到再检查格式是否合理。
