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

**群内指令**（可对本群单独覆盖全局配置）

| 指令 | 说明 |
|---|---|
| `/f1订阅本群` | 登记本群为推送目标（也可以只在 WebUI 的 `default_group_ids` 里配置群号，不发这条指令） |
| `/f1退订本群` | 取消本群登记 |
| `/f1通知 <赛段> <开\|关>` | 开关本群的开赛通知，赛段填：练习赛/冲刺排位赛/排位赛/冲刺赛/正赛/全部 |
| `/f1控制 <赛段> <开\|关>` | 开关本群的 race control 推送 |
| `/f1状态` | 查看本群当前生效的推送配置 |

群覆盖的优先级高于全局配置；没有单独覆盖过的赛段会跟随全局默认值。

## 已知限制

- 全新赛季刚开始、jolpica-f1 数据尚未录入时，`/赛历` 等指令会直接报错提示暂无数据，没有做静态 API 兜底。
- AstrBot 没有"列出机器人所在所有群"的官方接口，所以插件不会自动推送到所有群——必须在 WebUI 配置群号，或者群内自行执行 `/f1订阅本群`。
- Race control 的 SignalR 连接是按需建立/断开的，不是常驻连接。如果一个群是在比赛进行到一半才开启推送，只能收到开启之后的新消息，收不到之前已经发生的。
- 正赛若遇红旗中断导致比赛大幅延时，可能超出内部预估的时长窗口，插件会提前断开连接，存在漏推送比赛后段消息的风险。

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
