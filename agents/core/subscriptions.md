# core/subscriptions.py

## 设计思路

`SubscriptionStore`不直接依赖AstrBot的`Star`基类，而是在构造函数里接收三个函数(`kv_get`/`kv_put`/`kv_delete`)，调用方(`main.py`)传入`self.get_kv_data`/`self.put_kv_data`/`self.delete_kv_data`这几个绑定方法。这样设计是为了让这个模块可以脱离真实AstrBot环境单独测试(用一个内存dict模拟kv_get/put/delete即可，已验证过，见下方)。

## 设计目的

用户需求选择了"WebUI全局开关 + 群内指令覆盖"两层控制都要。`SubscriptionStore`就是实现这个"覆盖"语义的地方：全局默认值在`_conf_schema.json`里配置，群覆盖值存在插件KV里，两者在`get_effective_config`里合并成最终生效配置。

## 注意事项/坑

- **为什么用插件KV而不是自己写文件**: AstrBot官方推荐插件维度的小数据用内置KV(`put_kv_data`/`get_kv_data`/`delete_kv_data`，需要AstrBot≥4.9.2)，不需要自己管理文件路径和JSON读写，而且天然按插件隔离，不会和别的插件冲突。
- **覆盖值`None`=跟随全局默认，这是核心约定**: `group_cfg:{umo}`里每个赛段的值要么是`True`/`False`(明确覆盖)，要么是`None`(没覆盖过，跟全局)。`_ensure_cfg`初始化时所有赛段都设成`None`，不是`True`——如果初始化成`True`，用户后续在WebUI把全局默认改成`False`，已登记的群却不会跟着变（因为群配置里已经"固化"了一个`True`值），这就违背了"全局默认"的本意。**改这块代码时千万别把初始值从`None`改成`True`/`False`**。
- **`list_target_groups`的候选群集合 = WebUI配置的`default_group_ids` ∪ 已登记的`known_groups`**，取并集而不是只用其中一个。意味着：即使某个群没发过`/f1订阅本群`，只要管理员在WebUI的`default_group_ids`里加了它的umo，一样会被推送(走全局默认开关，因为它在KV里没有`group_cfg`记录，`get_effective_config`里`get_group_cfg`会自动给它初始化一个全`None`的覆盖，等效于全跟随全局)。
- **`set_override`的"全部"特殊处理**: `session_key=="ALL"`时会把5个赛段的值一起设成同一个`value`，这是`main.py`里`/f1通知 全部 开`这种指令的实现依据。

## 待办

- `known_groups`只会增不会自动清理——群解散/机器人被踢出群后，这个群的umo会一直留在`known_groups`里，`push_scheduler`每次tick都会对它算一遍`get_effective_config`(多余的KV读写开销，量级很小但理论上会随群数增长而变慢)。没做"检测到发送失败就自动清理"的逻辑，`/f1退订本群`是唯一的清理入口，依赖用户自己执行。
- 没有做"覆盖了一半赛段，另一半还是None"时`/f1状态`展示上的区分——目前`/f1状态`只展示最终生效值，看不出来某一项是"用户自己设的"还是"跟随全局的"。如果用户想知道"为什么这项是开的"，目前没法从指令输出直接看出来，得去翻KV数据。
