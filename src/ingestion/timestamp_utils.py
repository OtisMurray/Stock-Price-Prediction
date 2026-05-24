from __future__ import annotations

from datetime import datetime, timedelta, time, timezone
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
    current_utc = reference_dt.astimezone(timezone.utc) if reference_dt else datetime.now(timezone.utc)
    eastern_now = current_utc.astimezone(US_MARKET_TZ)
    if eastern_now.weekday() >= 5:
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= eastern_now.time() < market_close


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
