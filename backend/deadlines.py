"""Task deadlines: real dates instead of free text, and spreading them across a project's timeline.

Deadlines are stored as local ISO datetimes ("2026-10-18T18:00"). Older free-text labels such as
"Today 8:00 PM", "Tomorrow 10 AM" or the AI planner's "Day 2 - 4:00 PM" are still understood.
"""
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)
_DAY_N = re.compile(r"\bday\s*(\d+)\b", re.I)


def to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def parse_deadline(text: Optional[str], base: Optional[datetime] = None) -> Optional[datetime]:
    """A datetime for an ISO string or a relative label; None when the text has no usable date.

    Relative labels are read against `base` (default: now). "Day 1" is the base day, "Day 2" the next."""
    d = (text or "").strip()
    if not d:
        return None
    try:
        dt = datetime.fromisoformat(d)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    base = base or datetime.now()
    low = d.lower()
    day_n = _DAY_N.search(low)
    if day_n:
        offset = max(0, int(day_n.group(1)) - 1)
    elif "tomorrow" in low:
        offset = 1
    elif "yesterday" in low:
        offset = -1
    elif "today" in low or "tonight" in low:
        offset = 0
    else:
        return None
    m = _TIME.search(d)
    hour, minute = (int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0), int(m.group(2) or 0)) if m else (18, 0)
    day = base.date() + timedelta(days=offset)
    return datetime(day.year, day.month, day.day, hour, minute)


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_CLOCK = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)"
_LEAD = r"(?:\b(?:till|until|by|before|due|deadline|on)\s+)?"
_DATE_PATTERNS = [  # (regex, kind)
    (re.compile(_LEAD + r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"(?:\s*,?\s*(?:at\s+)?" + _CLOCK + r")?", re.I), "dm"),
    (re.compile(_LEAD + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(?:at\s+)?" + _CLOCK + r")?", re.I), "md"),
    (re.compile(_LEAD + r"\b(today|tonight|tomorrow)\b(?:\s*(?:at\s+)?" + _CLOCK + r")?", re.I), "rel"),
    (re.compile(r"\b(?:till|until|by|before)\s+" + _CLOCK, re.I), "clock"),
]


def extract_deadline(text: str, now: Optional[datetime] = None) -> tuple[str, str]:
    """Pull a deadline out of free text: "make the spreadsheet till 18 Oct 12pm" -> ("make the spreadsheet", ISO).

    Returns (text without the date phrase, ISO deadline or "")."""
    now = now or datetime.now()
    for rx, kind in _DATE_PATTERNS:
        m = rx.search(text or "")
        if not m:
            continue
        g = m.groups()
        hour, minute = 18, 0  # a date without a time means end of the working day
        if kind in ("dm", "md"):
            day, mon = (int(g[0]), g[1]) if kind == "dm" else (int(g[1]), g[0])
            month = _MONTHS.get(mon[:3].lower())
            if g[2]:
                hour, minute = int(g[2]) % 12 + (12 if g[4].lower() == "pm" else 0), int(g[3] or 0)
            try:
                dt = datetime(now.year, month, day, hour, minute)
            except (TypeError, ValueError):
                continue
            if dt < now - timedelta(days=30):  # "5 Jan" in December means next year
                dt = dt.replace(year=now.year + 1)
        elif kind == "rel":
            if g[1]:
                hour, minute = int(g[1]) % 12 + (12 if g[3].lower() == "pm" else 0), int(g[2] or 0)
            day = now.date() + timedelta(days=1 if g[0].lower() == "tomorrow" else 0)
            dt = datetime(day.year, day.month, day.day, hour, minute)
        else:
            hour, minute = int(g[0]) % 12 + (12 if g[2].lower() == "pm" else 0), int(g[1] or 0)
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt < now:
                dt += timedelta(days=1)
        rest = (text[:m.start()] + " " + text[m.end():]).strip()
        return re.sub(r"\s{2,}", " ", rest).strip(" ,.-"), to_iso(dt)
    return (text or "").strip(), ""


def normalize(text: Optional[str], base: Optional[datetime] = None) -> str:
    """Store a real date when we can read one; keep the original text otherwise."""
    dt = parse_deadline(text, base)
    return to_iso(dt) if dt else (text or "").strip()


def is_overdue(text: Optional[str], now: Optional[datetime] = None) -> bool:
    dt = parse_deadline(text, now)
    return bool(dt and dt < (now or datetime.now()))


def humanize(text: Optional[str]) -> str:
    """'Sat 18 Oct, 6:00 PM' for messages; free text is returned as is."""
    dt = parse_deadline(text)
    if not dt:
        return (text or "").strip()
    return f"{dt:%a} {dt.day} {dt:%b}, {dt.strftime('%I:%M %p').lstrip('0')}"


def _round_quarter(dt: datetime) -> datetime:
    """Round up to the next quarter hour: 5:07 PM -> 5:15 PM."""
    dt = dt.replace(second=0, microsecond=0)
    return dt + timedelta(minutes=(15 - dt.minute % 15) % 15)


def distribute(tasks: Iterable, start: datetime, end: datetime) -> dict[int, str]:
    """Spread deadlines between `start` and `end` along the dependency chain.

    Tasks are grouped into stages (no dependency = stage 1, depends on a stage-1 task = stage 2, ...).
    Each stage gets an equal slice of the timeline and its tasks are due when the slice ends, so a task
    is always due after the work it waits on. The last stage is due exactly at the project deadline.
    Works with Task objects or dicts (id, depends_on_task_id)."""
    items = list(tasks)
    if not items or end <= start:
        return {}

    def field(t, k):
        return t.get(k) if isinstance(t, dict) else getattr(t, k)

    by_id = {field(t, "id"): t for t in items}
    depth: dict[int, int] = {}

    def depth_of(t, seen=()):
        tid = field(t, "id")
        if tid in depth:
            return depth[tid]
        up = by_id.get(field(t, "depends_on_task_id"))
        depth[tid] = 0 if (up is None or tid in seen) else depth_of(up, seen + (tid,)) + 1
        return depth[tid]

    for t in items:
        depth_of(t)
    stages = max(depth.values()) + 1
    out = {}
    for t in items:
        k = depth[field(t, "id")]
        due = end if k == stages - 1 else min(end, _round_quarter(_at_working_fraction(start, end, (k + 1) / stages)))
        out[field(t, "id")] = to_iso(due)
    return out


WORK_START, WORK_END = 9, 21  # intermediate deadlines land between 9 AM and 9 PM, never at 3 AM


def _at_working_fraction(start: datetime, end: datetime, fraction: float) -> datetime:
    """The moment `fraction` of the way through the working hours between start and end."""
    windows, day = [], start.date()
    while datetime(day.year, day.month, day.day) <= end:
        lo = max(start, datetime(day.year, day.month, day.day, WORK_START))
        hi = min(end, datetime(day.year, day.month, day.day, WORK_END))
        if hi > lo:
            windows.append((lo, hi))
        day += timedelta(days=1)
    total = sum((hi - lo for lo, hi in windows), timedelta())
    if not total:  # the whole timeline is outside working hours: split it evenly
        return start + (end - start) * fraction
    left = total * fraction
    for lo, hi in windows:
        if left <= hi - lo:
            return lo + left
        left -= hi - lo
    return windows[-1][1]
