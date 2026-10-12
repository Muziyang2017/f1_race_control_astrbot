# core/live_timing.py

## 设计思路

直接照搬F1-RaceControl-Desktop项目(`F1Client.java`/`F1Feed.java`)逆向出来的协议实现，用Python(aiohttp做negotiate，websockets做长连接)重写一遍。这部分协议细节**不是官方文档**，是对一个第三方开源项目做逆向调研得到的，所有行为都已经用真实F1 livetiming端点实测验证过(2026-10-12，非比赛时段，拿到了上一场正赛的完整RaceControlMessages历史快照+SessionStatus)。

## 设计目的

`LiveTimingClient`只负责"连一次、读到断线/异常/`stop()`为止"这一件事，不做自动重连、不做race control语义理解（那是`race_control_formatter.py`的事）。这样设计是为了让连接生命周期完全交给`push_scheduler`控制——scheduler决定"现在该不该连、该不该断"，`live_timing.py`只管"连上之后怎么读协议"。

## 注意事项/坑

- **negotiate无鉴权但必须带亲和cookie**: `POST https://livetiming.formula1.com/signalrcore/negotiate?negotiateVersion=1`，空body，不需要任何Authorization头。但响应的`Set-Cookie`(AWSALB/AWSALBTG等负载均衡亲和cookie)**必须**在后续WebSocket升级请求里通过`Cookie`头带上——不带的话WebSocket可能被路由到另一个后端节点，那边认不出这个一次性`connectionToken`，导致升级失败(4xx)。本实现里`_negotiate()`从`resp.cookies`里把所有cookie拼成`Cookie`头字符串传给`websockets.connect(..., extra_headers=...)`。
- **token一次性**: `connectionToken`只能用一次，断线后不能复用，必须重新negotiate。所以`run()`每次调用都会完整走一遍negotiate+connect，没有"复用连接"的概念。
- **SignalR记录用`\x1e`(0x1e, RS控制符)分隔**，不是按行分隔，也不是单个JSON对象一条WebSocket消息——一条WebSocket frame里可能粘着多条记录，也可能一条记录跨多个frame。`run()`里用`buffer`累积，按`\x1e`切分处理，没切完的留在buffer里等下一帧。
- **消息类型含义**（SignalR Core协议本身的通用语义，不是F1专属）：
  - `type:6` = 心跳(ping)，**必须**在收到后立即回复`{"type":6}`+分隔符，否则服务端会主动断开连接。
  - `type:3` = 某次Invocation的Completion消息。本插件只发了一次Subscribe调用(`invocationId:"0"`)，它的completion就是**初始快照**，`result`字段是个dict，key是流名(如`RaceControlMessages`)，value是该流当前的完整状态。
  - `type:1` = 服务端主动推送的Invocation消息，也就是**增量更新**。
- **2026-10协议变更，新旧两种shape必须都兼容**（这是从F1Client.java的代码注释里得知的真实踩坑记录，不是本项目自己遇到的）：
  - 老shape: `target`字段本身就是流名(如`"RaceControlMessages"`)，payload在`arguments[0]`。
  - 新shape: `target`固定为`"feed"`，流名挪到`arguments[0]`，payload在`arguments[1]`。
  - `_extract_delta()`两种都处理了，但**只处理了type==1的delta**；type==3的快照走的是`msg.get("result")`这个dict，没有新旧shape的问题(因为快照本来就是F1Client.java里描述的稳定结构)。如果F1之后再变协议，大概率还是在`type:1`的`target`/`arguments`结构上动手脚，应该优先检查`_extract_delta`。
- **`stop()`必须主动关闭websocket，不能只设标志位**——这是实测踩到的坑：如果只设`self._stop=True`，`async for raw in ws`会一直阻塞到下一条消息/心跳才检查标志位；如果连接恰好没有心跳/新消息，`run()`会一直卡住不返回。修复方式是`stop()`里调用`asyncio.create_task(self._ws.close())`主动触发连接关闭，让`async for`立即抛出/结束。
- **不做自动重连**：`run()`抛出异常或正常返回后，上层(`push_scheduler._start_live_timing`里的`_runner`)只是log一下，不会自动重试。是否重连由`push_scheduler`在下一次tick根据"该session是否还需要连接"重新判断决定，避免`live_timing.py`自己做无限重试吃掉异常、掩盖真实的网络问题。

## 待办

- 还没有在**真实比赛进行中**验证过连续接收增量(`type:1`)消息的情况——目前的验证只覆盖了"连接后立刻拿到的初始快照+心跳+正常关闭"，增量推送的新旧shape兼容逻辑是照搬F1Client.java的描述实现的，没有实测过新shape长什么样（当前抓到的快照走的是`type:3`的`result`字段，不经过`_extract_delta`）。第一次真实比赛窗口时应重点关注日志里有没有`_extract_delta`返回`(None, None)`的情况(说明shape没识别出来)。
- 没有对`websockets.connect`做超时控制(`open_timeout`等)，如果F1服务端连接建立卡住，`run()`可能长时间不返回，`push_scheduler`那边`asyncio.create_task`不会感知到卡住，只会在下次tick检查`_live_task.done()`时发现任务还没结束。可以考虑加`asyncio.wait_for`包一层。
