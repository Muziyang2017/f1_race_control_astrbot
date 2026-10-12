from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.jolpica_api import JolpicaApiError, JolpicaClient
from .core.push_scheduler import PushScheduler
from .core.session_types import LABEL_TO_KEY, SESSION_LABELS, find_next_race_weekend
from .core.subscriptions import SubscriptionStore

_ON_OFF = {"开": True, "关": False, "on": True, "off": False, "ON": True, "OFF": False}
_SESSION_CHOICES = "练习赛/冲刺排位赛/排位赛/冲刺赛/正赛/全部"


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
