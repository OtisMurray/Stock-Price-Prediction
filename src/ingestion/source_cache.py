from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso_to_epoch(iso_text: str) -> float:
    if not iso_text:
        return 0.0
    normalized = iso_text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def load_source_cache(path: str) -> dict[str, Any]:
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_source_cache(path: str, payload: dict[str, Any]) -> None:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_cached_rows(
    payload: dict[str, Any],
    *,
    cache_key: str,
    max_age_seconds: int,
    now_epoch: float,
) -> tuple[list[dict[str, Any]] | None, float]:
    if max_age_seconds <= 0:
        return None, 0.0
    cache_entry = payload.get(cache_key)
    if not isinstance(cache_entry, dict):
        return None, 0.0
    fetched_at = str(cache_entry.get("fetched_at", ""))
    age_seconds = now_epoch - _parse_iso_to_epoch(fetched_at)
    if age_seconds < 0 or age_seconds > max_age_seconds:
        return None, max(age_seconds, 0.0)
    rows = cache_entry.get("rows", [])
    if not isinstance(rows, list):
        return None, age_seconds
    normalized_rows = [row for row in rows if isinstance(row, dict)]
    if not normalized_rows:
        return None, age_seconds
    return normalized_rows, age_seconds


def set_cached_rows(
    payload: dict[str, Any],
    *,
    cache_key: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    payload[cache_key] = {
        "fetched_at": _utc_now_iso(),
        "rows": rows,
    }
