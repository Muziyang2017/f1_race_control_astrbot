# f1_race_control_astrbot

AstrBot 插件：在 QQ 群查询 F1 赛历/积分，并实时推送开赛通知和比赛中的 race control（赛事控制）消息。

## 功能

**手动查询指令**

| 指令 | 说明 |
|---|---|
| `/赛历` | 发送距离当前时间最近一场赛事的完整赛程 |
| `/车手积分` | 当前赛季车手积分榜 |
| `/车队积分` | 当前赛季车队积分榜 |

**主动推送**（均按赛段分开关：练习赛 / 冲刺排位赛 / 排位赛 / 冲刺赛 / 正赛）

- 比赛开始通知：某个赛段开始时自动发一条通知
- Race control 实时推送：比赛进行中，黄旗/红旗/安全车/调查等官方赛事控制消息实时转发到群里

## 数据来源

- 赛历 / 车手积分 / 车队积分：[jolpica-f1](https://github.com/jolpica/jolpica-f1)（Ergast API 的继任者，免费）
- Race control 消息：F1 官方 livetiming 实时数据流（SignalR Core 协议），无需任何 F1TV 账号或鉴权

## 安装

1. 把本项目目录整个复制到 AstrBot 的 `data/plugins/` 下，例如：
   ```
   AstrBot/data/plugins/astrbot_plugin_f1_race_control/
     main.py
     metadata.yaml
     _conf_schema.json
     requirements.txt
     core/
     ...
   ```
2. 在 AstrBot 所用的 Python 环境里安装依赖：
   ```
   pip install -r requirements.txt
   ```
   （Docker 部署的话进容器里装，或用 WebUI 的插件依赖安装功能，如果有的话）
3. 重启 AstrBot，或在 WebUI「插件」页找到本插件点击重载（支持热重载，不需要整体重启）。

## 配置

装好后在 AstrBot WebUI 的插件配置页可以设置：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `user_agent` | 调用 jolpica-f1 API 用的 User-Agent（官方要求自定义，避免被限流） | `AstrBotF1Plugin/1.0 (...)` |
| `poll_interval_sec` | 后台轮询间隔（秒） | `60` |
| `default_group_ids` | 默认接收推送的群 `unified_msg_origin` 列表 | `[]` |
| `enable_start_notice` | 开赛通知全局默认开关，按赛段(FP/SQ/Q/SPRINT/RACE)分别配置 | 全部 `true` |
| `enable_race_control` | race control 推送全局默认开关，按赛段分别配置 | 全部 `true` |
| `race_control_excluded_flags` | race control 按 Flag 值过滤掉不推送的消息（不区分大小写），例如蓝旗这种高频低价值的通知 | `["BLUE"]` |

**群内指令**（可对本群单独覆盖全局配置）

| 指令 | 说明 |
|---|---|
| `/f1订阅本群` | 登记本群为推送目标（也可以只在 WebUI 的 `default_group_ids` 里配置群号，不发这条指令） |
| `/f1退订本群` | 取消本群登记 |
| `/f1通知 <赛段> <开\|关>` | 开关本群的开赛通知，赛段填：练习赛/冲刺排位赛/排位赛/冲刺赛/正赛/全部 |
| `/f1控制 <赛段> <开\|关>` | 开关本群的 race control 推送 |
| `/f1状态` | 查看本群当前生效的推送配置 |

群覆盖的优先级高于全局配置；没有单独覆盖过的赛段会跟随全局默认值。

## 调试

部署到服务器后不可能干等真实比赛来验证 race control 推送能不能用，插件带了一条临时调试指令：

| 指令 | 说明 |
|---|---|
| `/f1测试推送 [条数]` | 立刻连一次 F1 官方 livetiming，不等真实比赛/不等调度器判断窗口，抓到什么就按正式推送同样的流程（过滤 + 格式化）处理，发到当前群 |

用法：

1. 群里发 `/f1测试推送`（可带参数指定条数，默认 3，最多 10，例如 `/f1测试推送 5`）
2. 正常情况下会收到几条带 `[测试]` 前缀的格式化消息（内容是上一场正赛的历史记录，不是实时数据，因为比赛外连接只能拿到历史快照）
3. 如果提示"没抓到任何race control数据"，大概率是服务器连不上 `livetiming.formula1.com`（国内服务器常见，需要代理），先排查出网情况，不一定是插件本身的问题

这条指令是纯调试脚手架，不受任何群开关控制，谁发都会触发一次真实的外部连接。**调试确认链路通了之后建议从 `main.py` 里删掉**，不要带着留到正式环境。

## 已知限制

- 全新赛季刚开始、jolpica-f1 数据尚未录入时，`/赛历` 等指令会直接报错提示暂无数据，没有做静态 API 兜底。
- AstrBot 没有"列出机器人所在所有群"的官方接口，所以插件不会自动推送到所有群——必须在 WebUI 配置群号，或者群内自行执行 `/f1订阅本群`。
- Race control 的 SignalR 连接是按需建立/断开的，不是常驻连接。如果一个群是在比赛进行到一半才开启推送，只能收到开启之后的新消息，收不到之前已经发生的。
- 正赛若遇红旗中断导致比赛大幅延时，可能超出内部预估的时长窗口，插件会提前断开连接，存在漏推送比赛后段消息的风险。
- 赛历数据缓存6小时，为了不让"该决策的时候用的是过期赛历"，插件会在每场赛段开始前24小时/1小时/10分钟各强制刷新一次赛历。这能把过期窗口压缩到10分钟以内，但不是100%消除——如果赛程变更恰好发生在两个检查点之间，仍需等到下一个检查点才会生效。

## 项目结构与开发文档

```
main.py                 # 插件入口，指令注册
core/
  jolpica_api.py         # 赛历/积分数据源
  session_types.py        # 赛段定义与赛程查找
  live_timing.py           # F1官方SignalR实时流客户端
  race_control_formatter.py  # race control消息格式化与去重
  subscriptions.py          # 群级开关覆盖存取
  push_scheduler.py          # 后台推送调度
agents/                 # 面向二次开发/AI agent的知识库文档
  index.md               # 总览入口
  main.md, core/*.md     # 各模块的设计思路/目的/坑/待办
```

深入修改代码前建议先读 `agents/index.md`，里面记录了协议细节、已知坑和设计取舍的来龙去脉。
