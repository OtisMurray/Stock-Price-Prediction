from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from statistics import StatisticsError, correlation
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PRICE_CACHE_DIR = Path("data/cache/price_history")
PRICE_CACHE_MAX_AGE_SECONDS = 600
WATCHLIST_QUOTE_CACHE_PATH = Path("data/cache/watchlist_quotes/latest.json")
WATCHLIST_QUOTE_CACHE_MAX_AGE_SECONDS = 60
WATCHLIST_QUOTE_STALE_MAX_AGE_SECONDS = 60 * 15
WATCHLIST_QUOTE_FAIL_BACKOFF_SECONDS = 300
WORKER_HEARTBEAT_DIR = Path("data/cache/workers")
ROLLING_WINDOW_MINUTES = 5
ROLLING_WINDOW_STEP_MINUTES = 1
ROLLING_LOOKBACK_MINUTES = 360
MAX_CORRELATION_TICKERS = 6

_WATCHLIST_QUOTE_LAST_FAILURE_EPOCH = 0.0
_WATCHLIST_QUOTE_LAST_FAILURE_REASON = ""
_WATCHLIST_QUOTE_LAST_SUCCESS_ISO = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_dashboard_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_reference_datetime(row: dict[str, Any]) -> datetime | None:
    for key in ("published_at", "last_seen_at", "collected_at", "first_seen_at"):
        parsed = _parse_dashboard_datetime(row.get(key))
        if parsed:
            return parsed
    return None


def _row_exposure_weight(row: dict[str, Any]) -> float:
    explicit_weight = float(row.get("exposure_weight", 0.0) or 0.0)
    if explicit_weight > 0:
        return explicit_weight
    observation_count = int(
        row.get("exposure_observation_count", row.get("coverage_count", 0)) or 0
    )
    source_count = int(
        row.get("exposure_source_count", len(row.get("coverage_sources", []) or [])) or 0
    )
    observation_component = min(math.log1p(max(observation_count, 1)) * 0.45, 1.1)
    source_component = min(source_count * 0.12, 0.65)
    return max(1.0, 1.0 + observation_component + source_component)


def _ticker_contribution(row: dict[str, Any], matched_divisor: int) -> tuple[float, float]:
    sentiment_score = float(row.get("sentiment_score", 0.0) or 0.0)
    sentiment_confidence = max(0.25, float(row.get("sentiment_confidence", 0.0) or 0.0))
    ticker_relevance = max(
        0.25,
        float(row.get("ticker_relevance_confidence", 0.0) or 0.0),
    )
    signal_confidence = max(
        0.25,
        float(row.get("signal_confidence", sentiment_confidence) or sentiment_confidence),
    )
    exposure_weight = _row_exposure_weight(row)
    message_density = max(
        1.0,
        float(
            row.get(
                "exposure_observation_count",
                row.get("coverage_count", 1),
            )
            or 1.0
        ),
    )
    sentiment_support = (0.82 * sentiment_confidence) + (0.18 * signal_confidence)
    sentiment_contribution = (
        sentiment_score * ticker_relevance * sentiment_support * exposure_weight
    ) / max(matched_divisor, 1)
    density_contribution = (
        (message_density * 0.45) + (exposure_weight * 0.55)
    ) / max(matched_divisor, 1)
    return sentiment_contribution, density_contribution


def _minute_floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def build_rolling_window_snapshot(
    rows: list[dict[str, Any]],
    watchlist_metadata: dict[str, dict[str, str]],
    *,
    reference_dt: datetime | None = None,
    lookback_minutes: int = ROLLING_LOOKBACK_MINUTES,
    window_minutes: int = ROLLING_WINDOW_MINUTES,
    step_minutes: int = ROLLING_WINDOW_STEP_MINUTES,
) -> dict[str, Any]:
    if reference_dt is not None:
        anchor_dt = reference_dt
    else:
        anchor_dt = max(
            (_row_reference_datetime(row) for row in rows),
            default=None,
        ) or datetime.now(timezone.utc)
    now_dt = _minute_floor(anchor_dt)
    window_delta = timedelta(minutes=window_minutes)
    step_delta = timedelta(minutes=step_minutes)
    history_start = now_dt - timedelta(minutes=lookback_minutes + window_minutes)

    prepared_rows: list[tuple[datetime, dict[str, Any], list[str]]] = []
    for row in rows:
        row_dt = _row_reference_datetime(row)
        if not row_dt or row_dt < history_start or row_dt > now_dt:
            continue
        matched_tickers = [
            str(value).strip().upper()
            for value in row.get("matched_tickers", []) or []
            if str(value).strip()
        ]
        if not matched_tickers:
            ticker_text = str(row.get("ticker", "")).strip().upper()
            if ticker_text:
                matched_tickers = [ticker_text]
        if not matched_tickers:
            continue
        prepared_rows.append((row_dt, row, matched_tickers))

    prepared_rows.sort(key=lambda item: item[0])

    ticker_windows: dict[str, list[dict[str, Any]]] = {}
    market_windows: list[dict[str, Any]] = []

    cursor = now_dt - timedelta(minutes=lookback_minutes)
    while cursor + window_delta <= now_dt:
        window_end = cursor + window_delta
        per_ticker: dict[str, dict[str, Any]] = {}
        total_sentiment = 0.0
        total_density = 0.0
        total_articles = 0

        for row_dt, row, matched_tickers in prepared_rows:
            if row_dt < cursor:
                continue
            if row_dt >= window_end:
                break
            matched_divisor = max(len(matched_tickers), 1)
            sentiment_contribution, density_contribution = _ticker_contribution(
                row, matched_divisor
            )
            if abs(sentiment_contribution) < 0.004:
                continue
            total_articles += 1
            total_sentiment += sentiment_contribution
            total_density += density_contribution
            for ticker in matched_tickers:
                metadata = watchlist_metadata.get(ticker, {})
                item = per_ticker.setdefault(
                    ticker,
                    {
                        "ticker": ticker,
                        "company": str(metadata.get("company", "")),
                        "sector": str(metadata.get("sector", "")),
                        "industry": str(metadata.get("industry", "")),
                        "sentiment_build": 0.0,
                        "message_density": 0.0,
                        "article_count": 0,
                        "bullish_count": 0,
                        "bearish_count": 0,
                    },
                )
                item["sentiment_build"] += sentiment_contribution
                item["message_density"] += density_contribution
                item["article_count"] += 1
                label = str(row.get("sentiment_label", "")).lower()
                if label == "bullish":
                    item["bullish_count"] += 1
                elif label == "bearish":
                    item["bearish_count"] += 1

        market_windows.append(
            {
                "window_start": cursor.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "window_end": window_end.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "market_sentiment_build": round(total_sentiment, 3),
                "market_message_density": round(total_density, 3),
                "article_count": total_articles,
                "ticker_count": len(per_ticker),
            }
        )

        for ticker, item in per_ticker.items():
            ticker_windows.setdefault(ticker, []).append(
                {
                    "window_start": cursor.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "window_end": window_end.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "sentiment_build": round(item["sentiment_build"], 4),
                    "message_density": round(item["message_density"], 4),
                    "article_count": int(item["article_count"]),
                    "bullish_count": int(item["bullish_count"]),
                    "bearish_count": int(item["bearish_count"]),
                }
            )

        cursor += step_delta

    active_tickers = sorted(
        (
            {
                "ticker": ticker,
                "company": watchlist_metadata.get(ticker, {}).get("company", ""),
                "window_count": len(points),
                "latest_sentiment_build": points[-1]["sentiment_build"],
                "latest_message_density": points[-1]["message_density"],
                "recent_article_count": sum(point["article_count"] for point in points[-5:]),
            }
            for ticker, points in ticker_windows.items()
            if points
        ),
        key=lambda item: (
            -(abs(float(item["latest_sentiment_build"])) + float(item["latest_message_density"])),
            -int(item["recent_article_count"]),
            item["ticker"],
        ),
    )

    return {
        "window_minutes": window_minutes,
        "step_minutes": step_minutes,
        "lookback_minutes": lookback_minutes,
        "market_windows": market_windows,
        "ticker_windows": ticker_windows,
        "active_tickers": active_tickers[:MAX_CORRELATION_TICKERS],
    }


def _price_cache_path(ticker: str) -> Path:
    PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return PRICE_CACHE_DIR / f"{ticker.upper()}_1m.json"


def _load_price_cache(ticker: str, *, max_age_seconds: int = PRICE_CACHE_MAX_AGE_SECONDS) -> dict[str, Any] | None:
    path = _price_cache_path(ticker)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fetched_at = _parse_dashboard_datetime(payload.get("fetched_at"))
    if not fetched_at:
        return None
    age_seconds = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    if age_seconds > max_age_seconds:
        return None
    return payload


def _save_price_cache(ticker: str, payload: dict[str, Any]) -> None:
    path = _price_cache_path(ticker)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_watchlist_quote_cache(
    *,
    max_age_seconds: int = WATCHLIST_QUOTE_CACHE_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    path = WATCHLIST_QUOTE_CACHE_PATH
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fetched_at = _parse_dashboard_datetime(payload.get("fetched_at"))
    if not fetched_at:
        return None
    age_seconds = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    if age_seconds > max_age_seconds:
        return None
    return payload


def _write_worker_heartbeat(name: str, payload: dict[str, Any]) -> None:
    WORKER_HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    heartbeat_path = WORKER_HEARTBEAT_DIR / f"{name}.heartbeat.json"
    heartbeat_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _quote_worker_heartbeat(
    *,
    status: str,
    provider: str,
    tracked_ticker_count: int,
    quoted_ticker_count: int,
    fresh_quote_count: int,
    stale_quote_count: int,
    unavailable_quote_count: int,
    failure_backoff_active: bool,
    reason: str = "",
) -> None:
    _write_worker_heartbeat(
        "quote_service",
        {
            "ts": _utc_now_iso(),
            "pid": os.getpid(),
            "status": status,
            "provider": provider,
            "tracked_ticker_count": int(tracked_ticker_count),
            "quoted_ticker_count": int(quoted_ticker_count),
            "fresh_quote_count": int(fresh_quote_count),
            "stale_quote_count": int(stale_quote_count),
            "unavailable_quote_count": int(unavailable_quote_count),
            "failure_backoff_active": bool(failure_backoff_active),
            "reason": str(reason or ""),
        },
    )


def _save_watchlist_quote_cache(payload: dict[str, Any]) -> None:
    WATCHLIST_QUOTE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_QUOTE_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _quote_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }


def _quote_row_from_price_series(ticker: str) -> dict[str, Any]:
    price_payload = fetch_intraday_price_series(ticker)
    return _quote_row_from_cached_payload(ticker, price_payload, source="price_series")


def _quote_row_from_cached_payload(
    ticker: str,
    price_payload: dict[str, Any] | None,
    *,
    source: str,
) -> dict[str, Any]:
    price_payload = price_payload or {}
    points = price_payload.get("points", []) or []
    latest_close = float(points[-1].get("close", 0.0) or 0.0) if points else 0.0
    latest_volume = int(points[-1].get("volume", 0) or 0) if points else 0
    previous_close = float(price_payload.get("meta", {}).get("previous_close", 0.0) or 0.0)
    if latest_close and previous_close:
        change_pct = ((latest_close - previous_close) / previous_close) * 100.0
    elif len(points) >= 2:
        prior_close = float(points[-2].get("close", 0.0) or 0.0)
        change_pct = ((latest_close - prior_close) / prior_close) * 100.0 if prior_close else 0.0
    else:
        change_pct = 0.0
    return {
        "ticker": ticker.upper(),
        "price": round(latest_close, 2),
        "change_pct": round(change_pct, 2),
        "volume": latest_volume,
        "previous_close": round(previous_close, 2) if previous_close else 0.0,
        "currency": str(price_payload.get("meta", {}).get("currency", "") or ""),
        "source": source,
    }


def _quote_row_from_stale_cache(ticker: str) -> dict[str, Any]:
    cached = _load_price_cache(ticker, max_age_seconds=60 * 60 * 24 * 7)
    if cached:
        return _quote_row_from_cached_payload(ticker, cached, source="stale_price_cache")
    return {
        "ticker": ticker.upper(),
        "price": 0.0,
        "change_pct": 0.0,
        "volume": 0,
        "previous_close": 0.0,
        "currency": "",
        "source": "quote_unavailable",
    }


def _fetch_cnbc_quote_rows(batch: list[str]) -> dict[str, dict[str, Any]]:
    symbols = "|".join(batch)
    query = urlencode({"symbols": symbols, "requestMethod": "itv"})
    url = f"https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?{query}"
    request = Request(url, headers=_quote_headers())
    with urlopen(request, timeout=4) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = (((payload or {}).get("FormattedQuoteResult") or {}).get("FormattedQuote") or [])
    rows: dict[str, dict[str, Any]] = {}
    for row in results:
        ticker = str(row.get("symbol", "") or "").strip().upper()
        if not ticker:
            continue
        price = float(str(row.get("last", "0") or "0").replace(",", "") or 0.0)
        change_pct = float(str(row.get("change_pct", "0") or "0").replace("%", "") or 0.0)
        rows[ticker] = {
            "ticker": ticker,
            "price": round(price, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(str(row.get("volume", "0") or "0").replace(",", "") or 0),
            "previous_close": 0.0,
            "currency": "",
            "source": "cnbc_quote",
        }
    return rows


def _fetch_yahoo_quote_rows(batch: list[str]) -> dict[str, dict[str, Any]]:
    query = urlencode({"symbols": ",".join(batch)})
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?{query}"
    request = Request(url, headers=_quote_headers())
    with urlopen(request, timeout=4) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = (((payload or {}).get("quoteResponse") or {}).get("result") or [])
    rows: dict[str, dict[str, Any]] = {}
    for row in results:
        ticker = str(row.get("symbol", "") or "").strip().upper()
        if not ticker:
            continue
        regular_market_price = float(row.get("regularMarketPrice", 0.0) or 0.0)
        previous_close = float(row.get("regularMarketPreviousClose", 0.0) or 0.0)
        if regular_market_price and previous_close:
            change_pct = ((regular_market_price - previous_close) / previous_close) * 100.0
        else:
            change_pct = float(row.get("regularMarketChangePercent", 0.0) or 0.0)
        rows[ticker] = {
            "ticker": ticker,
            "price": round(regular_market_price, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(row.get("regularMarketVolume", 0) or 0),
            "previous_close": round(previous_close, 2) if previous_close else 0.0,
            "currency": str(row.get("currency", "") or ""),
            "source": "yahoo_quote",
        }
    return rows


def fetch_watchlist_quote_snapshot(tickers: list[str]) -> dict[str, Any]:
    global _WATCHLIST_QUOTE_LAST_FAILURE_EPOCH
    global _WATCHLIST_QUOTE_LAST_FAILURE_REASON
    global _WATCHLIST_QUOTE_LAST_SUCCESS_ISO
    normalized_tickers = sorted(
        {
            str(ticker).strip().upper()
            for ticker in tickers
            if str(ticker).strip()
        }
    )
    if not normalized_tickers:
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "quotes": {},
            "tracked_ticker_count": 0,
            "quoted_ticker_count": 0,
            "quote_service": {
                "provider": "none",
                "fresh_quote_count": 0,
                "stale_quote_count": 0,
                "unavailable_quote_count": 0,
                "failure_backoff_active": False,
                "last_failure_reason": "",
                "last_success_at": _WATCHLIST_QUOTE_LAST_SUCCESS_ISO,
            },
        }

    cached = _load_watchlist_quote_cache()
    cached_symbols = sorted((cached or {}).get("quotes", {}).keys())
    if cached and cached_symbols == normalized_tickers:
        quote_service = dict((cached or {}).get("quote_service", {}) or {})
        quoted_count = int((cached or {}).get("quoted_ticker_count", 0) or 0)
        _quote_worker_heartbeat(
            status="cached",
            provider=str(quote_service.get("provider", "cache") or "cache"),
            tracked_ticker_count=len(normalized_tickers),
            quoted_ticker_count=quoted_count,
            fresh_quote_count=int(quote_service.get("fresh_quote_count", 0) or 0),
            stale_quote_count=int(quote_service.get("stale_quote_count", 0) or 0),
            unavailable_quote_count=max(len(normalized_tickers) - quoted_count, 0),
            failure_backoff_active=bool(quote_service.get("failure_backoff_active", False)),
            reason=str(quote_service.get("last_failure_reason", "") or ""),
        )
        return cached

    stale_watchlist_cache = _load_watchlist_quote_cache(
        max_age_seconds=WATCHLIST_QUOTE_STALE_MAX_AGE_SECONDS
    )
    stale_symbols = sorted((stale_watchlist_cache or {}).get("quotes", {}).keys())
    now_epoch = datetime.now(timezone.utc).timestamp()
    failure_backoff_active = (
        _WATCHLIST_QUOTE_LAST_FAILURE_EPOCH > 0
        and (now_epoch - _WATCHLIST_QUOTE_LAST_FAILURE_EPOCH) < WATCHLIST_QUOTE_FAIL_BACKOFF_SECONDS
    )
    if (
        failure_backoff_active
        and stale_watchlist_cache
        and stale_symbols == normalized_tickers
    ):
        snapshot = {
            **stale_watchlist_cache,
            "quote_service": {
                **dict((stale_watchlist_cache or {}).get("quote_service", {}) or {}),
                "provider": "stale_watchlist_cache",
                "failure_backoff_active": True,
                "last_failure_reason": _WATCHLIST_QUOTE_LAST_FAILURE_REASON,
                "last_success_at": _WATCHLIST_QUOTE_LAST_SUCCESS_ISO,
            },
        }
        _quote_worker_heartbeat(
            status="backoff",
            provider="stale_watchlist_cache",
            tracked_ticker_count=len(normalized_tickers),
            quoted_ticker_count=int(snapshot.get("quoted_ticker_count", 0) or 0),
            fresh_quote_count=0,
            stale_quote_count=int(snapshot.get("quoted_ticker_count", 0) or 0),
            unavailable_quote_count=max(
                len(normalized_tickers) - int(snapshot.get("quoted_ticker_count", 0) or 0),
                0,
            ),
            failure_backoff_active=True,
            reason=_WATCHLIST_QUOTE_LAST_FAILURE_REASON,
        )
        return snapshot

    quote_rows: dict[str, Any] = {}
    provider_counts = {"fresh": 0, "stale": 0, "unavailable": 0}
    provider_name = "mixed"
    batch_size = 50
    provider_order = (
        ("cnbc_batch", _fetch_cnbc_quote_rows),
        ("yahoo_batch", _fetch_yahoo_quote_rows),
    )
    for batch_start in range(0, len(normalized_tickers), batch_size):
        batch = normalized_tickers[batch_start : batch_start + batch_size]
        batch_rows: dict[str, dict[str, Any]] = {}
        provider_error = ""
        used_provider = ""
        for current_provider_name, provider_fn in provider_order:
            try:
                batch_rows = provider_fn(batch)
            except (URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
                provider_error = f"{current_provider_name}:{exc}"
                batch_rows = {}
                continue
            if batch_rows:
                used_provider = current_provider_name
                provider_name = current_provider_name if provider_name == "mixed" else provider_name
                break

        for ticker in batch:
            row = batch_rows.get(ticker)
            if row and float(row.get("price", 0.0) or 0.0) > 0:
                quote_rows[ticker] = row
                provider_counts["fresh"] += 1
            else:
                stale_row = _quote_row_from_stale_cache(ticker)
                quote_rows[ticker] = stale_row
                if float(stale_row.get("price", 0.0) or 0.0) > 0:
                    provider_counts["stale"] += 1
                else:
                    provider_counts["unavailable"] += 1

        if not used_provider and provider_error:
            _WATCHLIST_QUOTE_LAST_FAILURE_EPOCH = now_epoch
            _WATCHLIST_QUOTE_LAST_FAILURE_REASON = provider_error

    if provider_counts["fresh"] > 0:
        _WATCHLIST_QUOTE_LAST_SUCCESS_ISO = _utc_now_iso()
        _WATCHLIST_QUOTE_LAST_FAILURE_EPOCH = 0.0
        _WATCHLIST_QUOTE_LAST_FAILURE_REASON = ""

    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "quotes": quote_rows,
        "tracked_ticker_count": len(normalized_tickers),
        "quoted_ticker_count": sum(1 for row in quote_rows.values() if float(row.get("price", 0.0) or 0.0) > 0),
        "quote_service": {
            "provider": provider_name,
            "fresh_quote_count": int(provider_counts["fresh"]),
            "stale_quote_count": int(provider_counts["stale"]),
            "unavailable_quote_count": int(provider_counts["unavailable"]),
            "failure_backoff_active": False,
            "last_failure_reason": _WATCHLIST_QUOTE_LAST_FAILURE_REASON,
            "last_success_at": _WATCHLIST_QUOTE_LAST_SUCCESS_ISO,
        },
    }
    _save_watchlist_quote_cache(snapshot)
    _quote_worker_heartbeat(
        status="ok" if provider_counts["fresh"] > 0 else "degraded",
        provider=provider_name,
        tracked_ticker_count=len(normalized_tickers),
        quoted_ticker_count=int(snapshot["quoted_ticker_count"]),
        fresh_quote_count=int(provider_counts["fresh"]),
        stale_quote_count=int(provider_counts["stale"]),
        unavailable_quote_count=int(provider_counts["unavailable"]),
        failure_backoff_active=False,
        reason=_WATCHLIST_QUOTE_LAST_FAILURE_REASON,
    )
    return snapshot


def fetch_intraday_price_series(ticker: str) -> dict[str, Any]:
    cached = _load_price_cache(ticker)
    if cached:
        return cached

    query = urlencode(
        {
            "interval": "1m",
            "range": "2d",
            "includePrePost": "true",
            "events": "div,splits",
        }
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            )
        },
    )

    try:
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        stale_path = _price_cache_path(ticker)
        if stale_path.exists():
            try:
                return json.loads(stale_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "ticker": ticker.upper(),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "points": [],
            "meta": {},
        }

    result = (((payload or {}).get("chart") or {}).get("result") or [{}])[0] or {}
    meta = result.get("meta", {}) or {}
    timestamps = result.get("timestamp", []) or []
    quote_list = (((result.get("indicators") or {}).get("quote")) or [{}])
    quote = quote_list[0] if quote_list else {}
    closes = quote.get("close", []) or []
    volumes = quote.get("volume", []) or []

    points: list[dict[str, Any]] = []
    for index, unix_ts in enumerate(timestamps):
        if unix_ts is None:
            continue
        close_value = closes[index] if index < len(closes) else None
        volume_value = volumes[index] if index < len(volumes) else None
        if close_value is None:
            continue
        dt = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
        points.append(
            {
                "ts": dt.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "close": float(close_value),
                "volume": int(volume_value or 0),
            }
        )

    normalized = {
        "ticker": ticker.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "meta": {
            "regular_market_price": float(meta.get("regularMarketPrice") or 0.0),
            "previous_close": float(meta.get("previousClose") or 0.0),
            "currency": str(meta.get("currency", "") or ""),
        },
        "points": points,
    }
    _save_price_cache(ticker, normalized)
    return normalized


def _price_at_or_before(points: list[dict[str, Any]], target_dt: datetime) -> dict[str, Any] | None:
    selected: dict[str, Any] | None = None
    for point in points:
        point_dt = _parse_dashboard_datetime(point.get("ts"))
        if not point_dt:
            continue
        if point_dt <= target_dt:
            selected = point
        else:
            break
    return selected


def _safe_correlation(first: list[float], second: list[float]) -> float:
    if len(first) < 3 or len(second) < 3 or len(first) != len(second):
        return 0.0
    if len(set(round(value, 6) for value in first)) <= 1:
        return 0.0
    if len(set(round(value, 6) for value in second)) <= 1:
        return 0.0
    try:
        return float(correlation(first, second))
    except StatisticsError:
        return 0.0


def build_correlation_snapshot(
    rows: list[dict[str, Any]],
    watchlist_metadata: dict[str, dict[str, str]],
    *,
    reference_dt: datetime | None = None,
    quote_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rolling = build_rolling_window_snapshot(
        rows,
        watchlist_metadata,
        reference_dt=reference_dt,
    )
    ticker_windows = rolling.get("ticker_windows", {})
    active_tickers = [item["ticker"] for item in rolling.get("active_tickers", [])]

    correlation_rows: list[dict[str, Any]] = []
    for ticker in active_tickers:
        windows = list(ticker_windows.get(ticker, []))
        if len(windows) < 4:
            continue

        price_payload = fetch_intraday_price_series(ticker)
        price_points = price_payload.get("points", []) or []
        if len(price_points) < 10:
            continue

        density_values: list[float] = []
        sentiment_values: list[float] = []
        return_values: list[float] = []
        article_values: list[int] = []

        for window in windows:
            start_dt = _parse_dashboard_datetime(window.get("window_start"))
            end_dt = _parse_dashboard_datetime(window.get("window_end"))
            if not start_dt or not end_dt:
                continue
            start_point = _price_at_or_before(price_points, start_dt)
            end_point = _price_at_or_before(price_points, end_dt)
            if not start_point or not end_point:
                continue
            start_close = float(start_point.get("close", 0.0) or 0.0)
            end_close = float(end_point.get("close", 0.0) or 0.0)
            if start_close <= 0 or end_close <= 0:
                continue
            return_pct = ((end_close - start_close) / start_close) * 100.0
            density_values.append(float(window.get("message_density", 0.0) or 0.0))
            sentiment_values.append(float(window.get("sentiment_build", 0.0) or 0.0))
            return_values.append(return_pct)
            article_values.append(int(window.get("article_count", 0) or 0))

        if len(return_values) < 3:
            continue

        density_price_corr = _safe_correlation(density_values, return_values)
        sentiment_price_corr = _safe_correlation(sentiment_values, return_values)
        combined_correlation = (
            abs(sentiment_price_corr) * 0.6
            + abs(density_price_corr) * 0.4
        )

        latest_window = windows[-1]
        quote_row = ((quote_snapshot or {}).get("quotes", {}) or {}).get(ticker, {})
        latest_point = price_points[-1] if price_points else {}
        latest_close = float(quote_row.get("price", 0.0) or latest_point.get("close", 0.0) or 0.0)
        latest_volume = int(quote_row.get("volume", 0) or latest_point.get("volume", 0) or 0)
        change_pct = float(quote_row.get("change_pct", 0.0) or 0.0)
        if not quote_row:
            previous_close = float(price_payload.get("meta", {}).get("previous_close", 0.0) or 0.0)
            if latest_close and previous_close:
                change_pct = ((latest_close - previous_close) / previous_close) * 100.0
            elif len(price_points) >= 2:
                prior_close = float(price_points[-2].get("close", 0.0) or 0.0)
                change_pct = ((latest_close - prior_close) / prior_close) * 100.0 if prior_close else 0.0
            else:
                change_pct = 0.0

        correlation_rows.append(
            {
                "ticker": ticker,
                "company": str(watchlist_metadata.get(ticker, {}).get("company", "")),
                "sector": str(watchlist_metadata.get(ticker, {}).get("sector", "")),
                "industry": str(watchlist_metadata.get(ticker, {}).get("industry", "")),
                "price": round(latest_close, 2),
                "change_pct": round(change_pct, 2),
                "volume": latest_volume,
                "sentiment_build": round(float(latest_window.get("sentiment_build", 0.0) or 0.0), 3),
                "message_density": round(float(latest_window.get("message_density", 0.0) or 0.0), 3),
                "articles": int(sum(article_values[-5:]) or 0),
                "window_count": len(return_values),
                "density_price_corr": round(density_price_corr, 3),
                "sentiment_price_corr": round(sentiment_price_corr, 3),
                "combined_correlation": round(combined_correlation, 3),
                "latest_window_end": str(latest_window.get("window_end", "")),
            }
        )

    correlation_rows.sort(
        key=lambda item: (
            -float(item["combined_correlation"]),
            -int(item["articles"]),
            item["ticker"],
        )
    )

    quoted_leader_count = sum(
        1 for row in correlation_rows if float(row.get("price", 0.0) or 0.0) > 0
    )

    return {
        "window_minutes": rolling.get("window_minutes", ROLLING_WINDOW_MINUTES),
        "step_minutes": rolling.get("step_minutes", ROLLING_WINDOW_STEP_MINUTES),
        "lookback_minutes": rolling.get("lookback_minutes", ROLLING_LOOKBACK_MINUTES),
        "leaders": correlation_rows[:10],
        "top_density_linked": sorted(
            correlation_rows,
            key=lambda item: (-abs(float(item["density_price_corr"])), item["ticker"]),
        )[:8],
        "top_sentiment_linked": sorted(
            correlation_rows,
            key=lambda item: (-abs(float(item["sentiment_price_corr"])), item["ticker"]),
        )[:8],
        "market_windows": rolling.get("market_windows", [])[-12:],
        "quote_coverage": {
            "tracked_ticker_count": int((quote_snapshot or {}).get("tracked_ticker_count", 0) or 0),
            "quoted_ticker_count": int((quote_snapshot or {}).get("quoted_ticker_count", 0) or 0),
            "quoted_leader_count": int(quoted_leader_count),
            "leader_count": int(len(correlation_rows)),
            "fetched_at": str((quote_snapshot or {}).get("fetched_at", "") or ""),
        },
        "quote_service": dict((quote_snapshot or {}).get("quote_service", {}) or {}),
    }


def build_momentum_marketboard(
    momentum_snapshot: dict[str, Any],
    watchlist_metadata: dict[str, dict[str, str]],
    quote_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    leader_rows = list(momentum_snapshot.get("leaders", []) or [])
    card_rows: list[dict[str, Any]] = []
    quotes = ((quote_snapshot or {}).get("quotes", {}) or {})
    for item in leader_rows[:10]:
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        quote_row = quotes.get(ticker, {}) or _quote_row_from_stale_cache(ticker)
        latest_close = float(quote_row.get("price", 0.0) or 0.0)
        latest_volume = int(quote_row.get("volume", 0) or 0)
        change_pct = float(quote_row.get("change_pct", 0.0) or 0.0)

        card_rows.append(
            {
                "ticker": ticker,
                "company": str(item.get("company") or watchlist_metadata.get(ticker, {}).get("company", "")),
                "price": round(latest_close, 2),
                "change_pct": round(change_pct, 2),
                "volume": latest_volume,
                "sentiment": round(float(item.get("momentum_score", 0.0) or 0.0), 3),
                "articles": int(item.get("article_count", 0) or 0),
                "density": round(float(item.get("message_density_score", 0.0) or 0.0), 3),
                "bullish_count": int(item.get("bullish_count", 0) or 0),
                "bearish_count": int(item.get("bearish_count", 0) or 0),
                "label": str(item.get("label", "")),
            }
        )

    quoted_leader_count = sum(
        1 for row in card_rows if float(row.get("price", 0.0) or 0.0) > 0
    )

    return {
        "leaders": card_rows,
        "window_totals": dict(momentum_snapshot.get("window_totals", {}) or {}),
        "message_density_windows": dict(
            momentum_snapshot.get("message_density_windows", {}) or {}
        ),
        "quote_coverage": {
            "tracked_ticker_count": int((quote_snapshot or {}).get("tracked_ticker_count", 0) or 0),
            "quoted_ticker_count": int((quote_snapshot or {}).get("quoted_ticker_count", 0) or 0),
            "quoted_leader_count": int(quoted_leader_count),
            "leader_count": int(len(card_rows)),
            "fetched_at": str((quote_snapshot or {}).get("fetched_at", "") or ""),
        },
        "quote_service": dict((quote_snapshot or {}).get("quote_service", {}) or {}),
    }
