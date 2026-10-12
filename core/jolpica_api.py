from __future__ import annotations

import time
from typing import Any, Optional

import aiohttp

from .session_types import RaceWeekend, parse_schedule

BASE_URL = "https://api.jolpi.ca/ergast/f1"


class JolpicaApiError(Exception):
    pass


class JolpicaClient:
    """jolpica-f1 (Ergast继任者) API 封装。为什么选它、为什么需要自定义UA、
    缓存时长的理由见 agents/core/jolpica_api.md。
    """

    def __init__(self, user_agent: str = "AstrBotF1Plugin/1.0"):
        self._user_agent = user_agent
        self._session: Optional[aiohttp.ClientSession] = None
        self._schedule_cache: Optional[tuple[float, list[RaceWeekend]]] = None
        self._schedule_ttl = 6 * 3600
        self._standings_cache: dict[str, tuple[float, Any]] = {}
        self._standings_ttl = 600

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": self._user_agent})
        return self._session

    async def _get_json(self, path: str) -> dict:
        session = self._ensure_session()
        url = f"{BASE_URL}/{path}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise JolpicaApiError(f"jolpica请求失败: {resp.status} {url}")
            return await resp.json(content_type=None)

    async def get_current_season_schedule(self, force: bool = False) -> list[RaceWeekend]:
        now = time.monotonic()
        if not force and self._schedule_cache and now - self._schedule_cache[0] < self._schedule_ttl:
            return self._schedule_cache[1]
        data = await self._get_json("current.json")
        weekends = parse_schedule(data)
        if not weekends:
            raise JolpicaApiError("未获取到当前赛季赛历数据（可能是新赛季数据尚未录入）")
        self._schedule_cache = (now, weekends)
        return weekends

    async def _get_standings(self, kind: str) -> dict:
        now = time.monotonic()
        cached = self._standings_cache.get(kind)
        if cached and now - cached[0] < self._standings_ttl:
            return cached[1]
        data = await self._get_json(f"current/{kind}.json")
        lists = data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
        if not lists:
            raise JolpicaApiError(f"未获取到{kind}数据")
        result = lists[0]
        self._standings_cache[kind] = (now, result)
        return result

    async def get_driver_standings(self) -> list[dict]:
        standings = await self._get_standings("driverStandings")
        return standings.get("DriverStandings", [])

    async def get_constructor_standings(self) -> list[dict]:
        standings = await self._get_standings("constructorStandings")
        return standings.get("ConstructorStandings", [])
