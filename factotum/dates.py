"""Small date helpers shared by scoring, lifecycle and the command grammar."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_date(s: str | None) -> date | None:
    """ISO date or datetime string → date (None stays None)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date() if len(s) > 10 else date.fromisoformat(s[:10])
    except ValueError:
        return None


def days_until(s: str | None, today: date) -> int | None:
    d = parse_date(s)
    return None if d is None else (d - today).days


def resolve_when(text: str, today: date) -> date | None:
    """'tomorrow', 'fri', 'friday', 'next week', 'next fri', '2026-11-06', 'nov 6' → date."""
    t = text.strip().lower()
    if not t:
        return None
    if t in ("today",):
        return today
    if t in ("tomorrow", "tmrw"):
        return today + timedelta(days=1)
    if t in ("next week", "nextweek"):
        return today + timedelta(days=7 - today.weekday())  # next Monday
    m = re.match(r"(next\s+)?([a-z]{3,9})$", t)
    if m and m.group(2)[:3] in WEEKDAYS:
        target = WEEKDAYS.index(m.group(2)[:3])
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7
        if m.group(1):
            delta += 7 if delta < 7 else 0
        return today + timedelta(days=delta)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"([a-z]{3,9})\s+(\d{1,2})$", t)
    if m:
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        if m.group(1)[:3] in months:
            month = months.index(m.group(1)[:3]) + 1
            d = date(today.year, month, int(m.group(2)))
            return d if d >= today else date(today.year + 1, month, int(m.group(2)))
    return None
