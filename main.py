from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .core.jolpica_api import JolpicaApiError, JolpicaClient
from .core.live_timing import LiveTimingClient
from .core.push_scheduler import PushScheduler
from .core.race_control_formatter import extract_messages, format_message, is_filtered
from .core.session_types import LABEL_TO_KEY, SESSION_LABELS, find_next_race_weekend
from .core.subscriptions import SubscriptionStore

_ON_OFF = {"开": True, "关": False, "on": True, "off": False, "ON": True, "OFF": False}
_SESSION_CHOICES = "练习赛/冲刺排位赛/排位赛/冲刺赛/正赛/全部"


def _build_mock_race_control_samples() -> list[dict]:
    """给/f1模拟推送用的写死样例，字段形状照抄2026赛季真实抓到的数据(debug/下的调研产物)。
    刻意包含一条BLUE，用来验证race_control_excluded_flags过滤确实生效。"""
    now = datetime.now(timezone.utc)

    def ts(offset_sec: int) -> str:
        return (now + timedelta(seconds=offset_sec)).strftime("%Y-%m-%dT%H:%M:%S")

    return [
        {"Utc": ts(0), "Lap": 12, "Category": "Flag", "Flag": "YELLOW", "Scope": "Sector", "Sector": 7, "Message": "YELLOW IN TRACK SECTOR 7"},
        {"Utc": ts(1), "Lap": 12, "Category": "Flag", "Flag": "BLUE", "Scope": "Driver", "RacingNumber": "77", "Message": "WAVED BLUE FLAG FOR CAR 77 (BOT) TIMED AT 20:38:16"},
        {"Utc": ts(2), "Lap": 15, "Category": "SafetyCar", "Status": "DEPLOYED", "Mode": "VSC", "Message": "VSC DEPLOYED"},
        {"Utc": ts(3), "Lap": 58, "Category": "Flag", "Flag": "CHEQUERED", "Scope": "Track", "Message": "CHEQUERED FLAG"},
    ]


class F1RaceControlPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._jolpica = JolpicaClient(user_agent=config.get("user_agent", "AstrBotF1Plugin/1.0"))
        self._store = SubscriptionStore(self.get_kv_data, self.put_kv_data, self.delete_kv_data)
        self._scheduler = PushScheduler(
            context=self.context,
            config=self.config,
            jolpica=self._jolpica,
            store=self._store,
            kv_get=self.get_kv_data,
            kv_put=self.put_kv_data,
        )
        self._scheduler_task = asyncio.create_task(self._scheduler.run())

    async def terminate(self):
        self._scheduler.stop()
        if self._scheduler_task:
            self._scheduler_task.cancel()
        await self._jolpica.close()

    # ---------------- 手动查询指令 ----------------

    @filter.command("赛历")
    async def cmd_schedule(self, event: AstrMessageEvent):
        try:
            weekends = await self._jolpica.get_current_season_schedule()
        except JolpicaApiError as e:
            yield event.plain_result(f"获取赛历失败: {e}")
            return

        now = datetime.now(timezone.utc)
        weekend = find_next_race_weekend(weekends, now)
        if not weekend:
            yield event.plain_result("暂无即将进行的赛事信息。")
            return

        lines = [
            f"🏁 {weekend.race_name}（第{weekend.round_no}轮）",
            f"赛道: {weekend.circuit_name}（{weekend.locality}, {weekend.country}）",
        ]
        for s in weekend.sorted_sessions():
            local_str = s.start.astimezone().strftime("%m-%d %H:%M")
            lines.append(f"  {SESSION_LABELS[s.key]}（{s.label}）: {local_str}")
        yield event.plain_result("\n".join(lines))

    @filter.command("车手积分")
    async def cmd_driver_standings(self, event: AstrMessageEvent):
        try:
            standings = await self._jolpica.get_driver_standings()
        except JolpicaApiError as e:
            yield event.plain_result(f"获取车手积分失败: {e}")
            return

        lines = ["🏆 车手积分榜"]
        for item in standings:
            driver = item.get("Driver", {})
            constructors = item.get("Constructors", [])
            team = constructors[0].get("name", "") if constructors else ""
            name = f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()
            lines.append(f"{item.get('position')}. {name}（{team}）- {item.get('points')}分")
        yield event.plain_result("\n".join(lines))

    @filter.command("车队积分")
    async def cmd_constructor_standings(self, event: AstrMessageEvent):
        try:
            standings = await self._jolpica.get_constructor_standings()
        except JolpicaApiError as e:
            yield event.plain_result(f"获取车队积分失败: {e}")
            return

        lines = ["🏆 车队积分榜"]
        for item in standings:
            constructor = item.get("Constructor", {})
            lines.append(f"{item.get('position')}. {constructor.get('name', '')} - {item.get('points')}分")
        yield event.plain_result("\n".join(lines))

    # ---------------- 订阅与开关管理 ----------------

    @filter.command("f1订阅本群")
    async def cmd_subscribe(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        await self._store.register_group(umo)
        yield event.plain_result(
            "已登记本群，默认跟随全局推送设置。\n"
            "可用 /f1通知 /f1控制 单独调整本群开关，/f1状态 查看当前生效配置。"
        )

    @filter.command("f1退订本群")
    async def cmd_unsubscribe(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        await self._store.unregister_group(umo)
        yield event.plain_result("已取消本群登记（若本群在WebUI的默认群列表中，仍需管理员在WebUI移除才会完全停止推送）。")

    @filter.command("f1通知")
    async def cmd_toggle_start_notice(self, event: AstrMessageEvent, session_label: str, switch: str):
        async for r in self._toggle(event, "start_notice", "开赛通知", session_label, switch):
            yield r

    @filter.command("f1控制")
    async def cmd_toggle_race_control(self, event: AstrMessageEvent, session_label: str, switch: str):
        async for r in self._toggle(event, "race_control", "race control推送", session_label, switch):
            yield r

    async def _toggle(self, event: AstrMessageEvent, feature: str, feature_label: str, session_label: str, switch: str):
        key = LABEL_TO_KEY.get(session_label)
        if key is None:
            yield event.plain_result(f"赛段名不对，可选: {_SESSION_CHOICES}")
            return
        value = _ON_OFF.get(switch)
        if value is None:
            yield event.plain_result("开关请写 开 或 关")
            return

        umo = event.unified_msg_origin
        await self._store.register_group(umo)
        await self._store.set_override(umo, feature, key, value)
        yield event.plain_result(f"本群「{session_label}」{feature_label}已{'开启' if value else '关闭'}。")

    # ---------------- 调试指令(临时) ----------------

    @filter.command("f1测试推送")
    async def cmd_test_push(self, event: AstrMessageEvent, limit: str = "3"):
        """临时调试指令：立刻连一次F1官方SignalR流，不等真实比赛/不等调度器判断窗口，
        抓到什么就格式化什么(同样套用race_control_excluded_flags过滤，跟正式推送行为一致)，
        走和正式推送同一条context.send_message路径发到本群。
        用来在部署环境上验证"服务器能不能连上F1livetiming"+"消息格式是否正常"+
        "AstrBot能不能主动推送"这条完整链路。调试完建议删掉这条指令，不要留到生产。
        """
        try:
            count = max(1, min(int(limit), 10))
        except ValueError:
            count = 3

        yield event.plain_result("开始测试：正在连接F1官方livetiming...")

        collected: list[str] = []
        excluded_flags = self.config.get("race_control_excluded_flags", [])

        async def on_record(stream_name, payload):
            if stream_name != "RaceControlMessages":
                return
            for raw in extract_messages(payload):
                if is_filtered(raw, excluded_flags):
                    continue
                text = format_message(raw)
                if text:
                    collected.append(text)

        client = LiveTimingClient(["RaceControlMessages"])
        task = asyncio.create_task(client.run(on_record))
        await asyncio.sleep(5)
        client.stop()
        try:
            await asyncio.wait_for(task, timeout=5)
        except Exception:
            logger.exception("f1测试推送: live timing连接异常")

        if not collected:
            yield event.plain_result("没抓到任何race control数据，可能是服务器连不上F1官方livetiming(检查出网/是否需要代理)，或接口暂时无数据。")
            return

        sample = collected[-count:]
        umo = event.unified_msg_origin
        for text in sample:
            await self.context.send_message(umo, MessageChain().message(f"[测试]{text}"))
        yield event.plain_result(f"测试完成：共抓到{len(collected)}条，已用send_message推送{len(sample)}条到本群。")

    @filter.command("f1模拟推送")
    async def cmd_mock_push(self, event: AstrMessageEvent):
        """临时调试指令(第二条)：完全不连网络，用几条写死的真实格式样例消息
        (黄旗/蓝旗/安全车/方格旗)跑一遍过滤+格式化+send_message，用来在"服务器连不上
        F1官方livetiming"的环境里单独验证AstrBot推送链路本身没问题。
        跟/f1测试推送配合定位问题：这条不通 -> 推送链路本身有问题(跟F1网络无关)；
        这条通但/f1测试推送不通 -> 问题在网络/F1那边(比如被CloudFront WAF拦)，不在插件。
        """
        excluded_flags = self.config.get("race_control_excluded_flags", [])
        samples = _build_mock_race_control_samples()
        kept = [raw for raw in samples if not is_filtered(raw, excluded_flags)]
        filtered_out = len(samples) - len(kept)

        umo = event.unified_msg_origin
        sent = 0
        for raw in kept:
            text = format_message(raw)
            if not text:
                continue
            await self.context.send_message(umo, MessageChain().message(f"[模拟]{text}"))
            sent += 1

        yield event.plain_result(f"模拟完成：共{len(samples)}条样例，过滤掉{filtered_out}条，已推送{sent}条到本群。")

    @filter.command("f1状态")
    async def cmd_status(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        defaults = {
            "start_notice": dict(self.config.get("enable_start_notice", {})),
            "race_control": dict(self.config.get("enable_race_control", {})),
        }
        effective = await self._store.get_effective_config(defaults, umo)

        lines = ["📋 本群F1推送状态"]
        for feature, label in (("start_notice", "开赛通知"), ("race_control", "race control推送")):
            parts = [f"{SESSION_LABELS[k]}:{'开' if v else '关'}" for k, v in effective[feature].items()]
            lines.append(f"{label} -> " + " ".join(parts))
        yield event.plain_result("\n".join(lines))
