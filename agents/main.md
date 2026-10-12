# main.py

## 设计思路

`F1RaceControlPlugin(Star)`是AstrBot加载的唯一入口，`__init__`里把所有依赖(`JolpicaClient`/`SubscriptionStore`/`PushScheduler`)组装起来并启动后台任务，`terminate()`负责对称地收尾。指令handler本身都很薄，查询类直接调`core/jolpica_api.py`格式化输出，管理类直接调`core/subscriptions.py`。

## 设计目的

作为AstrBot插件框架要求的唯一入口文件，承担"组装"和"指令路由"两个职责，具体业务逻辑都下沉到`core/`里，main.py不应该再长出新的业务逻辑。

## 指令列表

| 指令 | 类型 | 对应core模块 |
|---|---|---|
| `/赛历` | 查询 | `jolpica_api.get_current_season_schedule` + `session_types.find_next_race_weekend` |
| `/车手积分` | 查询 | `jolpica_api.get_driver_standings` |
| `/车队积分` | 查询 | `jolpica_api.get_constructor_standings` |
| `/f1订阅本群` | 管理 | `subscriptions.register_group` |
| `/f1退订本群` | 管理 | `subscriptions.unregister_group` |
| `/f1通知 <赛段> <开/关>` | 管理 | `subscriptions.set_override(feature="start_notice")` |
| `/f1控制 <赛段> <开/关>` | 管理 | `subscriptions.set_override(feature="race_control")` |
| `/f1状态` | 查询 | `subscriptions.get_effective_config` |
| `/f1测试推送 [条数]` | **调试(临时)** | 直连`live_timing.LiveTimingClient`，不走`push_scheduler`的窗口判断 |
| `/f1模拟推送` | **调试(临时)** | 不碰网络，用写死的样例数据跑格式化+过滤+`send_message` |
| `/f1help` | 查询 | 纯静态文本，列出以上所有指令，新增指令时记得同步改这里(没做自动生成) |

赛段参数可选值: 练习赛/冲刺排位赛/排位赛/冲刺赛/正赛/全部 (对应`core/session_types.py`的`LABEL_TO_KEY`)。

## 调试指令说明

两条调试指令配合使用，分别隔离"F1网络连通性"和"AstrBot推送链路"这两个独立变量：

**`/f1测试推送 [条数]`**：

1. 连一次F1官方SignalR流(即使比赛外，也能拿到上一场的历史快照，内容是真实数据，已验证过)；
2. 等5秒收集`RaceControlMessages`，用`race_control_formatter.is_filtered()`套一遍`race_control_excluded_flags`过滤(跟正式推送行为一致，默认会把蓝旗消息滤掉，不然调试看到的和实际推送的不一样，调试没意义)，再格式化；
3. 取最后`limit`条(默认3，最多10)，走和正式推送**完全相同**的`self.context.send_message(umo, MessageChain)`路径发到当前群，消息前加`[测试]`前缀区分。

**`/f1模拟推送`**：完全不建立任何网络连接，用`_build_mock_race_control_samples()`写死的4条样例(黄旗/蓝旗/安全车/方格旗，字段形状照抄`debug/`调研产物里的真实数据)直接跑过滤+格式化+`send_message`，消息前加`[模拟]`前缀。背景：实测发现有的云服务器(如腾讯云)出口IP会被F1用的CloudFront WAF规则按403拦截("Request blocked"，换浏览器UA也没用，是IP/ASN级别拦截)，这种情况下`/f1测试推送`永远拿不到数据，没法区分"是F1连不上"还是"AstrBot推送本身有问题"。加这条指令就是为了把这两个变量分开：`/f1模拟推送`通但`/f1测试推送`不通 → 确定是网络/WAF问题，插件代码没毛病，解决不了只能换出口IP或走代理(代理目前代码里没支持)；`/f1模拟推送`也不通 → 问题在AstrBot推送链路本身(权限/`send_message`实现/平台适配器等)，要往这个方向查。

**调试完两条都建议删掉**，不要带着留到正式环境——都是纯调试脚手架，不受任何群开关控制，`/f1测试推送`还会触发真实的外部连接。

## 注意事项/坑

- `/f1通知`和`/f1控制`的handler都委托给私有方法`_toggle`(传入`feature`区分)，避免重复写参数校验逻辑。AstrBot的`@filter.command`要求每个指令对应独立的handler方法，不能直接复用同一个装饰过的方法，所以是两个薄壳方法各自`async for r in self._toggle(...): yield r`转发。
- **指令权限目前不限管理员**，群里任何人都能执行`/f1通知`/`/f1控制`改本群配置，这是刻意简化(MVP)。如果群管理觉得这样太随意，可以在对应`@filter.command`上加`permission_type=filter.PermissionType.ADMIN`，但目前没加。
- `main.py`里的`_global_defaults`逻辑在`/f1状态`和`push_scheduler.py`里各写了一份(两处都是`{"start_notice": dict(config.get(...)), "race_control": dict(config.get(...))}`)，属于重复代码但目前没抽公共函数——两边读的是同一个`self.config`，语义一致，重复度低，暂不处理。

## 待办

- 没有加任何指令的使用帮助/catch-all错误提示（比如用户输错赛段名时只会提示"赛段名不对"，没有更友好的交互，比如用AstrBot的下拉选项/指令组功能）。
- 没有国际化(多语言)处理，所有文案都是中文硬编码，符合当前用户群体(QQ群中文用户)的需求，但如果以后要支持其他平台/语言，需要重构成`plugin-i18n`机制。
