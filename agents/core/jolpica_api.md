# core/jolpica_api.py

## 设计思路

封装对 jolpica-f1 (`https://api.jolpi.ca/ergast/f1/`) 的HTTP调用，提供三个能力：当前赛季赛历、车手积分、车队积分。内部用`aiohttp.ClientSession`单例+简单内存缓存(不是KV持久化，重启即失效，可接受——数据本来就要定期刷新)。

## 设计目的

- 隔离数据源细节，上层(main.py指令、push_scheduler调度)只调用`get_current_season_schedule()`/`get_driver_standings()`/`get_constructor_standings()`，不关心URL拼接、字段解析。
- 缓存是为了：① 避免每次群里发`/赛历`都打一次外部API；② `push_scheduler`每60s tick一次，不缓存的话会对jolpica产生不必要的高频请求。

## 注意事项/坑

- **为什么选jolpica而非Ergast官方**: Ergast API已经废弃(官方公告停止维护)，jolpica-f1是社区公认的继任者，接口格式兼容(`MRData.RaceTable.Races[]` / `MRData.StandingsTable.StandingsLists[]`结构完全一致)，迁移成本低。
- **必须带自定义User-Agent**: jolpica官方要求调用方带自定义UA，不带可能被限流/封禁。已通过`_conf_schema.json`的`user_agent`配置项暴露给用户自定义，默认给了一个带插件标识的UA。
- **2026赛季数据可用性已实测验证**(2026-10-12测试): `current.json`正常返回23轮完整赛历(含FirstPractice/SecondPractice/ThirdPractice/Qualifying/Sprint/SprintQualifying字段)，`driverStandings`/`constructorStandings`也正常返回。所以"jolpica可能滞后"目前只是理论风险，不是当前已知问题——但新赛季刚开始的头几天仍可能有数据录入延迟。
- **字段名坑**: 冲刺排位赛的字段名jolpica现在用的是`SprintQualifying`，但历史上F1也用过`SprintShootout`这个叫法(规则改名)，`session_types.py`的`_FIELD_MAP`两个都做了映射，防止某个赛季/这个字段突然改名导致漏解析。
- **响应content-type**: jolpica返回的`Content-Type`不一定严格是`application/json`，`_get_json`里用了`resp.json(content_type=None)`绕过aiohttp的content-type校验，否则可能抛`ContentTypeError`。
- **缓存时长**: schedule缓存6小时(赛历变动频率很低)，standings缓存10分钟(比赛结束后积分会更新，不宜缓存太久，但也不需要秒级实时)。
- **异常类型统一**: 所有失败(HTTP非200、空数据)都包成`JolpicaApiError`，上层只需要catch这一个异常类型。

## 待办

- 当前"赛历数据缺失直接抛错"是刻意简化。后续如果需要更高可用性，可以接入 `livetiming.formula1.com/static/{year}/Index.json` 作为schedule的fallback数据源(字段结构不同，需要单独写一个parser)，但目前没做。
- `aiohttp.ClientSession`目前是进程级单例(每个`JolpicaClient`实例一个)，没有处理"插件重载(热重载)时旧session没关闭"的情况——依赖`terminate()`里调用`close()`，正常卸载路径没问题，但如果AstrBot强制kill插件而不走`terminate()`，session可能泄漏(影响很小，进程退出会自动回收)。
