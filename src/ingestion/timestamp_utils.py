from __future__ import annotations

from datetime import date, datetime, timedelta, time, timezone
from email.utils import parsedate_to_datetime
import re
from zoneinfo import ZoneInfo


RELATIVE_RE = re.compile(
    r"^\s*(?P<value>\d+)\s*(?P<unit>min|mins|minute|minutes|hour|hours|day|days)\s*$",
    re.IGNORECASE,
)
EPOCH_RE = re.compile(r"^\d{10}(?:\d{3})?$")

ABSOLUTE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%b-%d-%y",
    "%b-%d-%y %I:%M%p",
    "%b-%d-%Y",
    "%b-%d-%Y %I:%M%p",
    "%m/%d/%Y",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
)

US_MARKET_TZ = ZoneInfo("America/New_York")


def _nth_weekday_of_month(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _calculate_easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def us_equity_market_holidays(year: int) -> set[date]:
    holidays: set[date] = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday_of_month(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday_of_month(year, 2, 0, 3),   # Presidents Day
        _calculate_easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday_of_month(year, 5, 0),     # Memorial Day
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday_of_month(year, 9, 0, 1),   # Labor Day
        _nth_weekday_of_month(year, 11, 3, 4),  # Thanksgiving
        _observed_fixed_holiday(year, 12, 25),
    }
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))  # Juneteenth
    return holidays


def is_us_equity_market_holiday(reference_date: date) -> bool:
    return reference_date in us_equity_market_holidays(reference_date.year)


def us_equity_market_session(reference_dt: datetime | None = None) -> str:
    current_utc = reference_dt.astimezone(timezone.utc) if reference_dt else datetime.now(timezone.utc)
    eastern_now = current_utc.astimezone(US_MARKET_TZ)
    current_date = eastern_now.date()
    if eastern_now.weekday() >= 5:
        return "Market Closed (ET)"
    if is_us_equity_market_holiday(current_date):
        return "Market Holiday (ET)"

    current_time = eastern_now.time()
    premarket_start = time(4, 0)
    market_open = time(9, 30)
    market_close = time(16, 0)
    after_hours_close = time(20, 0)

    if premarket_start <= current_time < market_open:
        return "Pre-Market (ET)"
    if market_open <= current_time < market_close:
        return "Market Open (ET)"
    if market_close <= current_time < after_hours_close:
        return "After Hours (ET)"
    return "Market Closed (ET)"


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def _parse_reference_time(iso_text: str) -> datetime:
    normalized = (iso_text or "").replace("Z", "+00:00")
    if normalized:
        try:
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _parse_relative(cleaned: str, reference_iso: str) -> datetime | None:
    match = RELATIVE_RE.match(cleaned)
    if not match:
        return None
    value = int(match.group("value"))
    unit = match.group("unit").lower()
    reference_dt = _parse_reference_time(reference_iso)
    if unit.startswith("min"):
        return reference_dt - timedelta(minutes=value)
    if unit.startswith("hour"):
        return reference_dt - timedelta(hours=value)
    if unit.startswith("day"):
        return reference_dt - timedelta(days=value)
    return None


def _parse_absolute(cleaned: str) -> datetime | None:
    if EPOCH_RE.match(cleaned):
        try:
            raw_value = int(cleaned)
            if len(cleaned) == 13:
                return datetime.fromtimestamp(raw_value / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(raw_value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass

    try:
        dt = parsedate_to_datetime(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    iso_candidate = cleaned.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in ABSOLUTE_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_published_datetime(raw_value: str, *, collected_at: str) -> datetime | None:
    cleaned = _normalize_text(raw_value)
    if not cleaned:
        return None
    return _parse_relative(cleaned, collected_at) or _parse_absolute(cleaned)


def is_us_equity_market_open(reference_dt: datetime | None = None) -> bool:
    return us_equity_market_session(reference_dt) == "Market Open (ET)"


def normalize_published_fields(raw_value: str, *, collected_at: str) -> dict[str, str]:
    cleaned = _normalize_text(raw_value)
    if not cleaned:
        return {
            "published_raw": "",
            "published_display": "",
            "published_at": "",
        }

    parsed_dt = parse_published_datetime(cleaned, collected_at=collected_at)
    published_display = cleaned
    if parsed_dt and EPOCH_RE.match(cleaned):
        published_display = parsed_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {
        "published_raw": cleaned,
        "published_display": published_display,
        "published_at": (
            parsed_dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            if parsed_dt
            else ""
        ),
    }
