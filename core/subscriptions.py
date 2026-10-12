from __future__ import annotations

from typing import Awaitable, Callable, Optional

from .session_types import SESSION_KEYS

KNOWN_GROUPS_KEY = "known_groups"

KvGet = Callable[[str, object], Awaitable[object]]
KvPut = Callable[[str, object], Awaitable[None]]
KvDelete = Callable[[str], Awaitable[None]]


def _group_cfg_key(umo: str) -> str:
    return f"group_cfg:{umo}"


class SubscriptionStore:
    """群级开关覆盖存取，基于插件KV(put_kv_data/get_kv_data/delete_kv_data)。

    覆盖字段值为 None/缺失 = 跟随全局默认，约定详见 agents/core/subscriptions.md。
    """

    def __init__(self, kv_get: KvGet, kv_put: KvPut, kv_delete: KvDelete):
        self._get = kv_get
        self._put = kv_put
        self._delete = kv_delete

    async def known_groups(self) -> list[str]:
        return await self._get(KNOWN_GROUPS_KEY, [])

    async def register_group(self, umo: str):
        groups = await self.known_groups()
        if umo not in groups:
            groups.append(umo)
            await self._put(KNOWN_GROUPS_KEY, groups)
        await self._ensure_cfg(umo)

    async def unregister_group(self, umo: str):
        groups = await self.known_groups()
        if umo in groups:
            groups.remove(umo)
            await self._put(KNOWN_GROUPS_KEY, groups)
        await self._delete(_group_cfg_key(umo))

    async def _ensure_cfg(self, umo: str) -> dict:
        cfg = await self._get(_group_cfg_key(umo), None)
        if cfg is None:
            cfg = {
                "start_notice": {k: None for k in SESSION_KEYS},
                "race_control": {k: None for k in SESSION_KEYS},
            }
            await self._put(_group_cfg_key(umo), cfg)
        return cfg

    async def get_group_cfg(self, umo: str) -> dict:
        return await self._ensure_cfg(umo)

    async def set_override(self, umo: str, feature: str, session_key: str, value: Optional[bool]):
        cfg = await self._ensure_cfg(umo)
        if session_key == "ALL":
            for k in SESSION_KEYS:
                cfg[feature][k] = value
        else:
            cfg[feature][session_key] = value
        await self._put(_group_cfg_key(umo), cfg)

    async def get_effective_config(self, global_defaults: dict, umo: str) -> dict:
        """global_defaults形如 {"start_notice": {k: bool}, "race_control": {k: bool}}。"""
        cfg = await self.get_group_cfg(umo)
        effective: dict[str, dict[str, bool]] = {}
        for feature in ("start_notice", "race_control"):
            effective[feature] = {}
            for k in SESSION_KEYS:
                override = cfg.get(feature, {}).get(k)
                effective[feature][k] = (
                    override if override is not None else bool(global_defaults.get(feature, {}).get(k, True))
                )
        return effective

    async def list_target_groups(
        self,
        global_group_ids: list[str],
        global_defaults: dict,
        feature: str,
        session_key: str,
    ) -> list[str]:
        groups = set(global_group_ids) | set(await self.known_groups())
        targets = []
        for umo in groups:
            effective = await self.get_effective_config(global_defaults, umo)
            if effective.get(feature, {}).get(session_key):
                targets.append(umo)
        return targets
