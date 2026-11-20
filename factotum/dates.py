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


# --- a day as the owner says it (spoken Review stage 3, design §7.2) ------------------------------------------

_NUM_WORDS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                                           "eleven", "twelve", "thirteen", "fourteen"])} | {"a": 1, "an": 1, "a couple of": 2}
_ORDINALS = {ordinal_words(n): n for n in range(1, 32)} | {ordinal_words(n).replace("-", " "): n for n in range(21, 32)}
_MONTHS3 = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_WORDS = {m.lower() for m in MONTH_NAMES} | set(_MONTHS3) | {"sept"}
_WEEKDAY_WORDS = {d.lower() for d in DAY_NAMES} | set(WEEKDAYS) | {"tues", "thur", "thurs"}
_LEAD = re.compile(r"^(?:on|by|until|till|til|for|before|around|about|maybe|say|let's say|lets say|make it|how about) ")
_DAY_NUM = r"[0-9]{1,2}(?:st|nd|rd|th)?|[a-z]+(?:[- ](?!of\b)[a-z]+)?"


def _day_of(word: str) -> int | None:
    """"3", "3rd", "third", "twenty-first", "twenty first" → the day of the month (1 to 31), else None."""
    m = re.fullmatch(r"([0-9]{1,2})(?:st|nd|rd|th)?", word)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 31 else None
    return _ORDINALS.get(word)


def _in_month(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _next_with_day(today: date, day: int) -> date | None:
    """That day of this month when it is not past, else of the next month that has it (the 31st in November is December's)."""
    y, m = today.year, today.month
    for _ in range(13):
        d = _in_month(y, m, day)
        if d is not None and d >= today:
            return d
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None


def _month_day(today: date, month: int, day: int) -> date | None:
    """That day of that month this year when it is not past, else next year."""
    d = _in_month(today.year, month, day)
    if d is None:
        return None
    return d if d >= today else _in_month(today.year + 1, month, day)


def _when_reads(t: str) -> bool:
    """Whether `resolve_when` may read this: its weekday and month patterns look only at three letters ("sunshine" is a
    Sunday there, "novel 6" a sixth of November), so a word that is not a weekday or a month is kept from it."""
    m = re.fullmatch(r"(?:next )?([a-z]+)", t)
    if m:
        return m.group(1) in _WEEKDAY_WORDS or t in ("today", "tomorrow", "tmrw", "next week", "nextweek")
    m = re.fullmatch(r"([a-z]+) [0-9]{1,2}", t)
    return m is None or m.group(1) in _MONTH_WORDS


def resolve_spoken(text, today: date) -> date | None:
    """A day as a person says it to a voice assistant, from Review's day, or None when it is not clear. What
    `resolve_when` reads (the add line's words; that function is unchanged) and also: "this friday", "friday next
    week", "in 3 days" / "in two weeks" / "in a week" / "two weeks from now" (digits, or one to fourteen in words),
    "the day after tomorrow", "the 3rd" / "the third" / "the twenty-first" (this month if not past, else the next month
    that has it), "december 1st" / "december the first" / "the first of december" / "1 december" (this year if not
    past, else next), "end of the week" (Friday), "the weekend" (Saturday), "next weekend", "next month" (its first
    day). Never raises; the day it gives may be today or past (the caller says whether that will do)."""
    if not isinstance(text, str):
        return None
    t = re.sub(r"\s+", " ", text.strip().lower()[:80]).strip(" .,!?;:'\"")
    t = _LEAD.sub("", t)
    if not t:
        return None
    m = re.fullmatch(r"(next|this|coming|this coming) ([a-z]+)", t)
    if m and m.group(2) in _WEEKDAY_WORDS:                  # review 2026-11-20: "next tuesday" is Tuesday of next week, as
        wd = WEEKDAYS.index(m.group(2)[:3])                 # "tuesday next week" is (resolve_when reads it as the one after
        if m.group(1) == "next":                            # the coming one); "this tuesday" said on a Tuesday is today
            return today + timedelta(days=7 - today.weekday() + wd)
        return today + timedelta(days=(wd - today.weekday()) % 7)
    if _when_reads(t):
        try:
            d = resolve_when(t, today)
        except ValueError:                                  # "2026-02-30", "nov 31": no such day
            return None
        if d is not None:
            return d
    if t in ("the day after tomorrow", "day after tomorrow"):
        return today + timedelta(days=2)
    if t in ("end of the week", "the end of the week", "end of this week", "the end of this week", "end of week"):
        return today + timedelta(days=(4 - today.weekday()) % 7)
    if t in ("the weekend", "this weekend", "weekend", "over the weekend"):
        return today + timedelta(days=(5 - today.weekday()) % 7)
    if t == "next weekend":
        return today + timedelta(days=(5 - today.weekday()) % 7 + 7)
    if t in ("next month", "the start of next month", "start of next month", "the beginning of next month"):
        return date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    m = re.fullmatch(r"([a-z]+) next week|next week ([a-z]+)", t)
    if m:
        wd = m.group(1) or m.group(2)
        if wd not in _WEEKDAY_WORDS:
            return None
        return today + timedelta(days=7 - today.weekday() + WEEKDAYS.index(wd[:3]))
    m = re.fullmatch(r"(?:in |after )?([0-9]{1,3}|a couple of|[a-z]+) (days?|weeks?)(?: from (?:now|today))?", t)
    if m and (t.startswith(("in ", "after ")) or t.endswith((" from now", " from today"))):
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS.get(m.group(1))
        if n is None or not 0 < n <= (366 if m.group(2).startswith("day") else 52):
            return None
        return today + timedelta(days=n * (7 if m.group(2).startswith("week") else 1))
    m = re.fullmatch(rf"(?:the )?({_DAY_NUM})(?: of)? ([a-z]+)", t)                       # "1st december", "the first of december"
    if m and m.group(2) in _MONTH_WORDS and _day_of(m.group(1)) is not None:
        return _month_day(today, _MONTHS3.index(m.group(2)[:3]) + 1, _day_of(m.group(1)))
    m = re.fullmatch(rf"([a-z]+)\.? (?:the )?({_DAY_NUM})", t)                             # "december 1st", "dec the first"
    if m and m.group(1) in _MONTH_WORDS and _day_of(m.group(2)) is not None:
        return _month_day(today, _MONTHS3.index(m.group(1)[:3]) + 1, _day_of(m.group(2)))
    m = re.fullmatch(rf"the ({_DAY_NUM})|([0-9]{{1,2}}(?:st|nd|rd|th))", t)                 # "the 3rd", "the third", "3rd"
    if m:
        n = _day_of(m.group(1) or m.group(2))
        return _next_with_day(today, n) if n is not None else None
    return None


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
