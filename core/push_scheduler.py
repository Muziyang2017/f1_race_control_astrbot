from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from astrbot.api.event import MessageChain

from .jolpica_api import JolpicaApiError, JolpicaClient
from .live_timing import LiveTimingClient
from .race_control_formatter import build_event_id, extract_messages, format_message, is_filtered
from .session_types import (
    DURATION_BUFFER_MIN,
    ESTIMATED_DURATION_MIN,
    SESSION_LABELS,
    find_active_or_upcoming_session,
)
from .subscriptions import SubscriptionStore

logger = logging.getLogger("astrbot_f1_push_scheduler")

# 结束信号来自两个独立的流，任一命中就提前断开live timing连接，不等预估时长走完：
# - SessionStatus流顶层Status字段：已实测验证真实值 {"Status": "Ends", "Started": "Finished"}
# - SessionInfo流的SessionStatus字段(与上面是不同流/不同字段，容易搞混)：已实测验证真实值
#   {"SessionStatus": "Finalised", "ArchiveStatus": {"Status": "Complete"}}
# 具体样本见 debug/race_control_raw_sample.json，是2026新加坡站正赛结束后抓到的真实历史快照。
_SESSION_STATUS_END_VALUES = {"Finalised", "Finalized", "Ends", "Inactive"}
_SESSION_INFO_END_VALUES = {"Finalised", "Finalized"}

# 单个session推送去重集合最多保留的event_id数，避免KV里无限增长。
_PUSHED_IDS_LIMIT = 500

# session开始前的强制刷新检查点：赛历缓存TTL有6小时，这几个点各强制刷新一次赛历，
# 降低"该决策的时候用的是过期赛历数据"的风险。每个检查点每个session只会触发一次。
_REFRESH_CHECKPOINTS = [
    ("24h", timedelta(hours=24)),
    ("1h", timedelta(hours=1)),
    ("10m", timedelta(minutes=10)),
]


class PushScheduler:
    """后台调度：轮询赛程 -> 判断开赛通知 / race control推送窗口 -> 按需管理SignalR连接。

    按需建连/断连(而非常驻连接)的理由、SessionStatus兜底未经真实比赛验证等
    风险点见 agents/core/push_scheduler.md。
    """

    def __init__(
        self,
        context,
        config,
        jolpica: JolpicaClient,
        store: SubscriptionStore,
        kv_get,
        kv_put,
    ):
        self._context = context
        self._config = config
        self._jolpica = jolpica
        self._store = store
        self._kv_get = kv_get
        self._kv_put = kv_put

        self._stop_event = asyncio.Event()
        self._live_task: Optional[asyncio.Task] = None
        self._live_client: Optional[LiveTimingClient] = None
        self._live_session_id: Optional[str] = None
        self._live_session_key: Optional[str] = None

    def stop(self):
        self._stop_event.set()
        self._stop_live_timing()

    def _global_defaults(self) -> dict:
        return {
            "start_notice": dict(self._config.get("enable_start_notice", {})),
            "race_control": dict(self._config.get("enable_race_control", {})),
        }

    async def run(self):
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("F1推送调度tick异常")
            try:
                timeout = self._config.get("poll_interval_sec", 60)
                await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    async def _tick(self):
        try:
            weekends = await self._jolpica.get_current_season_schedule()
        except JolpicaApiError as e:
            logger.warning("刷新赛历失败: %s", e)
            return

        now = datetime.now(timezone.utc)
        found = find_active_or_upcoming_session(weekends, now)
        if not found:
            return
        weekend, session, key = found
        session_id = self._build_session_id(weekend, key, session)

        if await self._maybe_force_refresh_schedule(session, session_id, now):
            try:
                weekends = await self._jolpica.get_current_season_schedule()
            except JolpicaApiError as e:
                logger.warning("强制刷新赛历后重新获取失败: %s", e)
            else:
                now = datetime.now(timezone.utc)
                found = find_active_or_upcoming_session(weekends, now)
                if not found:
                    return
                weekend, session, key = found
                session_id = self._build_session_id(weekend, key, session)

        await self._maybe_send_start_notice(weekend, session, key, session_id, now)
        await self._maybe_manage_live_timing(session, key, session_id, now)

    @staticmethod
    def _build_session_id(weekend, key: str, session) -> str:
        return f"{weekend.season}-{weekend.round_no}-{key}-{session.start.isoformat()}"

    async def _maybe_force_refresh_schedule(self, session, session_id: str, now) -> bool:
        """session开始前的24h/1h/10min检查点各强制刷新一次赛历缓存。

        同一时间可能有多个检查点同时到期(比如插件刚启动、session已经很近了)，
        这里只实际发一次强制刷新请求，但会把到期的检查点一起标记掉，不会重复打请求。
        """
        if now >= session.start:
            return False

        remaining = session.start - now
        pending_labels = [
            label for label, threshold in _REFRESH_CHECKPOINTS
            if remaining <= threshold and not await self._kv_get(f"refreshed:{session_id}:{label}", False)
        ]
        if not pending_labels:
            return False

        try:
            await self._jolpica.get_current_season_schedule(force=True)
        except JolpicaApiError:
            logger.exception("强制刷新赛历失败(session=%s, checkpoints=%s)", session_id, pending_labels)
            return False

        for label in pending_labels:
            await self._kv_put(f"refreshed:{session_id}:{label}", True)
        return True

    async def _maybe_send_start_notice(self, weekend, session, key, session_id, now):
        if now < session.start:
            return
        notified_flag_key = f"notified:{session_id}"
        already = await self._kv_get(notified_flag_key, False)
        if already:
            return

        defaults = self._global_defaults()
        group_ids = self._config.get("default_group_ids", [])
        targets = await self._store.list_target_groups(group_ids, defaults, "start_notice", key)

        text = f"🏎️ {weekend.race_name} - {SESSION_LABELS[key]}({session.label}) 开始了！"
        for umo in targets:
            try:
                await self._context.send_message(umo, MessageChain().message(text))
            except Exception:
                logger.exception("开赛通知发送失败: umo=%s", umo)

        await self._kv_put(notified_flag_key, True)

    async def _maybe_manage_live_timing(self, session, key, session_id, now):
        window_end = session.start + timedelta(minutes=ESTIMATED_DURATION_MIN[key] + DURATION_BUFFER_MIN)
        in_window = (session.start - timedelta(minutes=5)) <= now <= window_end

        defaults = self._global_defaults()
        group_ids = self._config.get("default_group_ids", [])
        targets = await self._store.list_target_groups(group_ids, defaults, "race_control", key)

        should_connect = in_window and bool(targets)
        currently_connected = self._live_task is not None and not self._live_task.done()

        if should_connect:
            if not currently_connected or self._live_session_id != session_id:
                if currently_connected:
                    self._stop_live_timing()
                self._start_live_timing(session_id, key)
        else:
            if currently_connected:
                self._stop_live_timing()

    def _start_live_timing(self, session_id: str, key: str):
        self._live_session_id = session_id
        self._live_session_key = key
        self._live_client = LiveTimingClient(["RaceControlMessages", "SessionStatus", "SessionInfo"])
        live_client = self._live_client

        async def _record_handler(stream_name: str, payload: Any):
            if stream_name == "RaceControlMessages":
                await self._handle_race_control(session_id, key, payload)
            elif stream_name == "SessionStatus":
                await self._handle_session_status(payload)
            elif stream_name == "SessionInfo":
                await self._handle_session_info(payload)

        async def _runner():
            try:
                await live_client.run(_record_handler)
            except Exception:
                logger.exception("live timing连接异常(session=%s)", session_id)

        self._live_task = asyncio.create_task(_runner())

    def _stop_live_timing(self):
        if self._live_client:
            self._live_client.stop()
        self._live_task = None
        self._live_client = None
        self._live_session_id = None
        self._live_session_key = None

    async def _handle_session_status(self, payload: Any):
        if isinstance(payload, dict) and payload.get("Status") in _SESSION_STATUS_END_VALUES:
            self._stop_live_timing()

    async def _handle_session_info(self, payload: Any):
        if isinstance(payload, dict) and payload.get("SessionStatus") in _SESSION_INFO_END_VALUES:
            self._stop_live_timing()

    async def _handle_race_control(self, session_id: str, key: str, payload: Any):
        if not isinstance(payload, dict):
            return
        pushed_key = f"pushed:{session_id}"
        pushed_ids: list[str] = list(await self._kv_get(pushed_key, []))
        pushed_set = set(pushed_ids)

        defaults = self._global_defaults()
        group_ids = self._config.get("default_group_ids", [])
        excluded_flags = self._config.get("race_control_excluded_flags", [])

        new_messages = []
        for raw in extract_messages(payload):
            event_id = build_event_id(raw)
            if event_id in pushed_set:
                continue
            pushed_set.add(event_id)
            pushed_ids.append(event_id)
            if is_filtered(raw, excluded_flags):
                continue
            text = format_message(raw)
            if text:
                new_messages.append(text)

        if new_messages:
            targets = await self._store.list_target_groups(group_ids, defaults, "race_control", key)
            for text in new_messages:
                for umo in targets:
                    try:
                        await self._context.send_message(umo, MessageChain().message(text))
                    except Exception:
                        logger.exception("race control推送失败: umo=%s", umo)

        await self._kv_put(pushed_key, pushed_ids[-_PUSHED_IDS_LIMIT:])
