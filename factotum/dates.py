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


# --- dates for the ear (spoken Review 2e, design §6.10) ------------------------------------------------------

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_ORD_ONES = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth",
             "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth"]


def ordinal_words(n: int) -> str:
    """1 → "first", 22 → "twenty-second", 30 → "thirtieth" (1 to 39; anything else as digits with a suffix)."""
    if 1 <= n < 20:
        return _ORD_ONES[n]
    if n in (20, 30):
        return {20: "twentieth", 30: "thirtieth"}[n]
    if 20 < n < 40:
        return ("twenty" if n < 30 else "thirty") + "-" + _ORD_ONES[n % 10]
    return f"{n}th"


def _when(value) -> tuple[date | None, tuple[int, int] | None]:
    """(the day, (hour, minute) when the value carries a time) of an ISO date or datetime string, a date or a datetime."""
    if isinstance(value, datetime):
        return value.date(), (value.hour, value.minute)
    if isinstance(value, date):
        return value, None
    s = str(value or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?", s)
    if not m:
        return None, None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, None
    hm = (int(m.group(4)), int(m.group(5))) if m.group(4) else None
    return d, hm if hm and hm[0] < 24 and hm[1] < 60 else None


def say_time(hour: int, minute: int) -> str:
    """"2 PM", "5:30 PM", "noon", "midnight"."""
    if (hour, minute) == (12, 0):
        return "noon"
    if (hour, minute) == (0, 0):
        return "midnight"
    h = hour % 12 or 12
    return f"{h}{'' if minute == 0 else f':{minute:02d}'} {'AM' if hour < 12 else 'PM'}"


def say_day(value, today: date, time: bool = True) -> str:
    """A day as a person says it, from Review's day: "today", "tomorrow", "yesterday", a weekday within six days
    ("Sunday"; before today "three days ago"), "the thirtieth" later this month, else "December fourth" (and the year when
    it is another: "March first, 2027"); with `time` and a time in the value, " at 2 PM". "" for what it cannot read."""
    d, hm = _when(value)
    if d is None:
        return ""
    n = (d - today).days
    if n == 0:
        out = "today"
    elif n == 1:
        out = "tomorrow"
    elif n == -1:
        out = "yesterday"
    elif 1 < n <= 6:
        out = DAY_NAMES[d.weekday()]
    elif -6 <= n < -1:
        out = ("two", "three", "four", "five", "six")[-n - 2] + " days ago"
    elif n > 0 and (d.year, d.month) == (today.year, today.month):
        out = "the " + ordinal_words(d.day)
    else:
        out = f"{MONTH_NAMES[d.month - 1]} {ordinal_words(d.day)}" + (f", {d.year}" if d.year != today.year else "")
    return out + (f" at {say_time(*hm)}" if time and hm else "")


def say_whose(day, today: date) -> str:
    """Whose, for a day: "today's", "yesterday's", "tomorrow's", "Monday's" within six days, else "November second's"."""
    d, _ = _when(day)
    if d is None:
        return ""
    n = (d - today).days
    if n in (0, 1, -1):
        return {0: "today's", 1: "tomorrow's", -1: "yesterday's"}[n]
    if -6 <= n <= 6:
        return DAY_NAMES[d.weekday()] + "'s"
    return f"{MONTH_NAMES[d.month - 1]} {ordinal_words(d.day)}'s"
