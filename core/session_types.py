from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

# 统一的5类赛段key。练习赛(FP1/FP2/FP3)合并成一个 FP 开关，和用户需求的5类通知粒度对齐。
SESSION_KEYS = ["FP", "SQ", "Q", "SPRINT", "RACE"]

SESSION_LABELS = {
    "FP": "练习赛",
    "SQ": "冲刺排位赛",
    "Q": "排位赛",
    "SPRINT": "冲刺赛",
    "RACE": "正赛",
}

LABEL_TO_KEY = {v: k for k, v in SESSION_LABELS.items()}
LABEL_TO_KEY["全部"] = "ALL"

# 预估时长(分钟)，用于判断race control连接的维持窗口。经验值非官方数据，
# 正赛如遇红旗中断可能大幅超时，详见 agents/core/session_types.md 的坑。
ESTIMATED_DURATION_MIN = {
    "FP": 70,
    "SQ": 50,
    "Q": 70,
    "SPRINT": 40,
    "RACE": 180,
}
DURATION_BUFFER_MIN = 30


@dataclass
class SessionInfo:
    key: str  # FP/SQ/Q/SPRINT/RACE
    label: str  # 原始字段名，如 FirstPractice/Qualifying/正赛
    start: datetime  # tz-aware UTC


@dataclass
class RaceWeekend:
    season: str
    round_no: str
    race_name: str
    circuit_name: str
    locality: str
    country: str
    sessions: list[SessionInfo] = field(default_factory=list)

    def sorted_sessions(self) -> list[SessionInfo]:
        return sorted(self.sessions, key=lambda s: s.start)


def _parse_dt(date_str: Optional[str], time_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    raw = f"{date_str}T{time_str}" if time_str else f"{date_str}T00:00:00Z"
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# jolpica schedule 字段名 -> 内部赛段key。SprintQualifying/SprintShootout 是同一赛段
# 在不同赛季的不同叫法(F1规则改名)，两个都映射到 SQ 做兼容。
_FIELD_MAP = [
    ("FirstPractice", "FP"),
    ("SecondPractice", "FP"),
    ("ThirdPractice", "FP"),
    ("SprintQualifying", "SQ"),
    ("SprintShootout", "SQ"),
    ("Qualifying", "Q"),
    ("Sprint", "SPRINT"),
]


def parse_schedule(mr_data: dict) -> list[RaceWeekend]:
    races = mr_data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    weekends: list[RaceWeekend] = []
    for r in races:
        weekend = RaceWeekend(
            season=r.get("season", ""),
            round_no=r.get("round", ""),
            race_name=r.get("raceName", ""),
            circuit_name=r.get("Circuit", {}).get("circuitName", ""),
            locality=r.get("Circuit", {}).get("Location", {}).get("locality", ""),
            country=r.get("Circuit", {}).get("Location", {}).get("country", ""),
        )
        race_dt = _parse_dt(r.get("date"), r.get("time"))
        if race_dt:
            weekend.sessions.append(SessionInfo(key="RACE", label="正赛", start=race_dt))
        for field_name, key in _FIELD_MAP:
            node = r.get(field_name)
            if not node:
                continue
            dt = _parse_dt(node.get("date"), node.get("time"))
            if dt:
                weekend.sessions.append(SessionInfo(key=key, label=field_name, start=dt))
        if weekend.sessions:
            weekends.append(weekend)
    return weekends


def find_next_race_weekend(weekends: list[RaceWeekend], now: datetime) -> Optional[RaceWeekend]:
    """取最近一场尚未完全结束的赛事周末，供 /赛历 指令使用。"""
    upcoming = [w for w in weekends if w.sorted_sessions() and w.sorted_sessions()[-1].start >= now]
    if not upcoming:
        return None
    upcoming.sort(key=lambda w: w.sorted_sessions()[0].start)
    return upcoming[0]


def find_active_or_upcoming_session(
    weekends: list[RaceWeekend], now: datetime
) -> Optional[tuple[RaceWeekend, SessionInfo, str]]:
    """返回当前应处理的 (weekend, session, key)：
    优先返回正处于[开始时间, 开始时间+预估时长+buffer]窗口内的session；
    否则返回时间上最近的未来session，供开赛通知提前判断。
    """
    candidates: list[tuple[RaceWeekend, SessionInfo, datetime]] = []
    for w in weekends:
        for s in w.sorted_sessions():
            window_end = s.start + timedelta(minutes=ESTIMATED_DURATION_MIN[s.key] + DURATION_BUFFER_MIN)
            candidates.append((w, s, window_end))

    for w, s, window_end in candidates:
        if s.start <= now <= window_end:
            return w, s, s.key

    future = [(w, s) for w, s, _ in candidates if s.start >= now]
    if not future:
        return None
    future.sort(key=lambda ws: ws[1].start)
    w, s = future[0]
    return w, s, s.key
