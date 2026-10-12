from __future__ import annotations

from typing import Any, Optional

# 按Flag优先匹配，匹配不到再按Category兜底。映射表是人工拟定，非官方标准，
# 后续应按实际收到的Category/Flag枚举值调整，见 agents/core/race_control_formatter.md。
_FLAG_LABELS = {
    "GREEN": "🟢绿旗",
    "YELLOW": "🟡黄旗",
    "DOUBLE YELLOW": "🟡🟡双黄旗",
    "RED": "🔴红旗",
    "CHEQUERED": "🏁结束旗",
    "BLUE": "🔵蓝旗",
    "CLEAR": "✅解除",
    "BLACK AND WHITE": "⬛⬜黑白旗",
}

_CATEGORY_LABELS = {
    "SafetyCar": "🚨安全车",
    "Flag": "🚩旗情",
    "Drs": "🛠️DRS",
    "CarEvent": "🚗赛车事件",
    "Other": "📋其他",
}


def format_message(raw: dict[str, Any]) -> Optional[str]:
    text = raw.get("Message") or raw.get("Text")
    if not text:
        return None
    utc = raw.get("Utc", "")
    category = raw.get("Category")
    flag = raw.get("Flag")
    sector = raw.get("Sector")
    lap = raw.get("Lap")

    prefix = _FLAG_LABELS.get(flag) if flag else None
    if not prefix:
        prefix = _CATEGORY_LABELS.get(category, "📢赛事通告")

    detail_parts = []
    if lap:
        detail_parts.append(f"第{lap}圈")
    if sector:
        detail_parts.append(f"S{sector}赛段")
    detail = f"[{' '.join(detail_parts)}] " if detail_parts else ""

    time_part = utc.split("T")[-1][:8] if "T" in utc else utc
    return f"{prefix} {time_part} {detail}{text}"


def build_event_id(raw: dict[str, Any]) -> str:
    """去重公式照搬F1-RaceControl-Desktop安卓版: utc|flag|sector|text。"""
    utc = raw.get("Utc", "")
    flag = raw.get("Flag", "")
    sector = raw.get("Sector", "")
    text = raw.get("Message") or raw.get("Text") or ""
    return f"{utc}|{flag}|{sector}|{text}"


def extract_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """RaceControlMessages payload 里 Messages 可能是数组，也可能是以索引为key的对象。"""
    messages = payload.get("Messages")
    if messages is None:
        return []
    if isinstance(messages, list):
        return [m for m in messages if isinstance(m, dict)]
    if isinstance(messages, dict):
        def sort_key(k: str):
            try:
                return (0, int(k))
            except ValueError:
                return (1, k)

        return [
            messages[k]
            for k in sorted(messages.keys(), key=sort_key)
            if isinstance(messages[k], dict)
        ]
    return []
