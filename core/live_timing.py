from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

import aiohttp
import websockets

logger = logging.getLogger("astrbot_f1_live_timing")

NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate?negotiateVersion=1"
WS_URL_TMPL = "wss://livetiming.formula1.com/signalrcore?id={token}"
RECORD_SEP = "\x1e"

RecordHandler = Callable[[str, Any], Optional[Awaitable[None]]]


class LiveTimingClient:
    """F1官方livetiming SignalR Core流客户端。

    协议细节(negotiate无鉴权但必须带亲和cookie、token一次性、0x1e分帧、
    type 1/3/6含义、新旧shape兼容)是对 F1-RaceControl-Desktop
    (F1Client.java/F1Feed.java) 逆向调研得到的，非官方文档，详见
    agents/core/live_timing.md。

    本类只负责"连一次、读到断线/异常/stop()为止"，不做自动重连——
    重连策略交给调用方(push_scheduler)判断是否还需要这个session的数据。
    """

    def __init__(self, streams: list[str]):
        self._streams = streams
        self._stop = False
        self._ws = None

    def stop(self):
        """标记停止并主动关闭连接。只设标志位不够——`async for raw in ws`
        会一直阻塞到下一条消息/心跳才检查标志位，实测在没有心跳时会卡住，
        必须主动close()才能立即中断读循环。"""
        self._stop = True
        if self._ws is not None:
            asyncio.create_task(self._ws.close())

    async def _negotiate(self) -> tuple[str, str]:
        async with aiohttp.ClientSession() as session:
            async with session.post(NEGOTIATE_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"negotiate失败: {resp.status}")
                data = await resp.json(content_type=None)
                token = data.get("connectionToken")
                if not token:
                    raise RuntimeError("negotiate未返回connectionToken")
                cookie_parts = [f"{c.key}={c.value}" for c in resp.cookies.values()]
                return token, "; ".join(cookie_parts)

    async def run(self, on_record: RecordHandler):
        self._stop = False
        token, cookie_header = await self._negotiate()
        url = WS_URL_TMPL.format(token=token)
        headers = {"Cookie": cookie_header} if cookie_header else {}

        async with websockets.connect(url, extra_headers=headers, max_size=None) as ws:
            self._ws = ws
            await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEP)
            subscribe_msg = {
                "type": 1,
                "target": "Subscribe",
                "arguments": [self._streams],
                "invocationId": "0",
            }
            await ws.send(json.dumps(subscribe_msg) + RECORD_SEP)

            buffer = ""
            async for raw in ws:
                if self._stop:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                buffer += raw
                while RECORD_SEP in buffer:
                    record, buffer = buffer.split(RECORD_SEP, 1)
                    if not record:
                        continue
                    try:
                        msg = json.loads(record)
                    except json.JSONDecodeError:
                        logger.warning("无法解析SignalR记录: %s", record[:200])
                        continue
                    await self._handle_record(ws, msg, on_record)
                if self._stop:
                    break

    async def _handle_record(self, ws, msg: dict, on_record: RecordHandler):
        msg_type = msg.get("type")
        if msg_type == 6:
            # 心跳必须回复，否则服务端会断开连接。
            await ws.send(json.dumps({"type": 6}) + RECORD_SEP)
            return
        if msg_type == 3:
            # Subscribe调用的completion，带初始快照，一次性包含所有订阅流。
            result = msg.get("result")
            if isinstance(result, dict):
                for stream_name, payload in result.items():
                    await self._dispatch(on_record, stream_name, payload)
            return
        if msg_type == 1:
            stream_name, payload = self._extract_delta(msg)
            if stream_name is not None:
                await self._dispatch(on_record, stream_name, payload)

    @staticmethod
    async def _dispatch(on_record: RecordHandler, stream_name: str, payload: Any):
        result = on_record(stream_name, payload)
        if asyncio.iscoroutine(result):
            await result

    @staticmethod
    def _extract_delta(msg: dict) -> tuple[Optional[str], Any]:
        """2026-10 F1协议变更，新旧两种shape需要都兼容：
        老shape: target本身就是流名，payload在arguments[0]；
        新shape: target固定为"feed"，流名在arguments[0]，payload在arguments[1]。
        """
        target = msg.get("target")
        arguments = msg.get("arguments")
        if not arguments:
            return None, None
        if target == "feed":
            if len(arguments) >= 2:
                return arguments[0], arguments[1]
            return None, None
        if target:
            return target, arguments[0]
        return None, None
