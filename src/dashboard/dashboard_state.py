from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.runners.collect_watchlist_snapshot import build_watchlist_snapshot
from src.ingestion.rss_sources import RSS_SOURCES
from src.ingestion.structured_sources import STRUCTURED_SOURCES
from src.analysis import score_article_sentiment, sentiment_runtime_status, summarize_article_sentiment
from src.dashboard.market_analytics import (
    build_correlation_snapshot,
    build_momentum_marketboard,
    fetch_watchlist_quote_snapshot,
)
from src.dashboard.translation_utils import likely_non_english
from src.storage import (
    fetch_latest_market_article_pool,
    fetch_latest_ticker_snapshot,
    fetch_latest_ticker_universe,
    fetch_latest_watchlist_snapshot,
    fetch_ticker_source_history,
    persist_watchlist_snapshot,
    refresh_story_sentiment_snapshots,
)
from src.ingestion.timestamp_utils import US_MARKET_TZ, parse_published_datetime, us_equity_market_session

HOT_TICKER_LIMIT = 20
AUTO_FINBERT_DEFAULT_LIMIT = 750
SOURCE_TIER_PRIORITY = {
    "primary_structured": 0,
    "secondary_structured": 1,
    "supplemental_unstructured": 2,
    "monitor_only": 3,
}

MACRO_GOVERNMENT_KEYWORDS = (
    "federal reserve",
    "fed",
    "treasury",
    "white house",
    "administration",
    "congress",
    "senate",
    "house of representatives",
    "sec",
    "cpi",
    "pce",
    "inflation",
    "unemployment",
    "jobs report",
    "labor market",
    "gdp",
    "economic growth",
    "consumer spending",
    "retail sales",
    "interest rate",
    "rate cut",
    "rate hike",
    "tariff",
    "trade policy",
    "budget",
    "deficit",
    "stimulus",
    "sanction",
    "regulation",
    "regulatory",
)

MACRO_GOVERNMENT_EVENT_TYPES = {
    "regulatory_or_geopolitical",
}

MARKETS_CATEGORY_KEYWORDS = (
    "dow",
    "s&p",
    "nasdaq",
    "wall street",
    "stock futures",
    "stocks today",
    "market today",
    "markets",
    "treasury yields",
    "bond market",
    "risk appetite",
    "market pulse",
)

CRYPTO_CATEGORY_KEYWORDS = (
    "crypto",
    "bitcoin",
    "ethereum",
    "xrp",
    "solana",
    "stablecoin",
    "token",
    "defi",
    "blockchain",
)

COMMODITIES_CATEGORY_KEYWORDS = (
    "oil",
    "wti",
    "brent",
    "crude",
    "natural gas",
    "gold",
    "silver",
    "copper",
    "commodity",
    "commodities",
    "opec",
)

FILING_CATEGORY_KEYWORDS = (
    "10-k",
    "10-q",
    "8-k",
    "13f",
    "13d",
    "proxy statement",
    "sec filing",
    "annual report",
    "quarterly report",
    "form s-",
)

PRESS_RELEASE_SOURCE_KEYS = {
    "pr_newswire",
    "pr_newswire_all",
    "access_newswire",
    "globe_newswire",
}

PRESS_RELEASE_SOURCE_NAMES = {
    "pr newswire",
    "access newswire",
    "globenewswire",
}

CATEGORY_PRIORITY = (
    "economy",
    "filings",
    "press_releases",
    "crypto",
    "commodities",
    "markets",
    "equities",
)

CATEGORY_LABELS = {
    "equities": "Equities",
    "markets": "Markets",
    "press_releases": "Press Releases",
    "crypto": "Crypto",
    "economy": "Economy",
    "filings": "Filings",
    "commodities": "Commodities",
}

SYNDICATED_PROVIDER_PATTERNS = (
    ("benzinga.com", "Benzinga"),
    ("reuters.com", "Reuters"),
    ("sec.gov", "SEC"),
)


def _running_on_railway() -> bool:
    return bool(
        os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
        or os.environ.get("RAILWAY_ENVIRONMENT_ID")
    )


def market_session_label(reference_dt: datetime | None = None) -> str:
    return us_equity_market_session(reference_dt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Serve a local dashboard for the watchlist news pipeline with a manual "
            "update button and a server-side cooldown."
        )
    )
    parser.add_argument(
        "--watchlist-file",
        default="data/watchlists/us_market_watchlist_100.json",
        help="JSON watchlist file containing ticker, company, and keyword entries.",
    )
    parser.add_argument(
        "--snapshot-file",
        default="data/cache/watchlist_snapshot_100_latest.json",
        help="Path to the latest watchlist snapshot JSON file.",
    )
    parser.add_argument(
        "--dashboard-state-file",
        default="tmp/dashboard_state.json",
        help="Path to the dashboard state JSON file.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the dashboard server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the dashboard server.",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=90,
        help="Minimum number of seconds between manual refreshes.",
    )
    parser.add_argument(
        "--rss-limit",
        type=int,
        default=0,
        help="Maximum RSS entries to inspect per source for each ticker. Use 0 for no explicit cap.",
    )
    parser.add_argument(
        "--structured-limit",
        type=int,
        default=0,
        help="Maximum structured entries to inspect per source for each ticker. Use 0 for no explicit cap.",
    )
    parser.add_argument(
        "--state-file",
        default="tmp/seen_structured_headlines_today.json",
        help="JSON file used to remember structured links already processed today.",
    )
    parser.add_argument(
        "--skip-rss",
        action="store_true",
        help="Skip the baseline RSS sources.",
    )
    parser.add_argument(
        "--skip-structured",
        action="store_true",
        help="Skip the structured sources.",
    )
    parser.add_argument(
        "--sqlite-db",
        default="data/cache/watchlist_pipeline.db",
        help="SQLite database path for persisting dashboard-driven refreshes.",
    )
    return parser.parse_args()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


WORKER_HEARTBEAT_DIR = Path("data/cache/workers")


def _write_worker_heartbeat(name: str, payload: dict[str, Any]) -> None:
    WORKER_HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKER_HEARTBEAT_DIR / f"{name}.heartbeat.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_worker_heartbeat(name: str) -> dict[str, Any]:
    path = WORKER_HEARTBEAT_DIR / f"{name}.heartbeat.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


EASTERN_TZ = ZoneInfo("America/New_York")


def format_eastern_time(iso_text: str) -> str:
    if not iso_text:
        return ""
    normalized = iso_text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return iso_text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    eastern = dt.astimezone(EASTERN_TZ)
    return eastern.strftime("%Y-%m-%d %I:%M:%S %p ET")


def format_published_display(
    published_at: str,
    published_raw: str,
    *,
    collected_at: str = "",
) -> str:
    published_at_text = str(published_at or "").strip()
    if published_at_text:
        return format_eastern_time(published_at_text)

    published_raw_text = str(published_raw or "").strip()
    if not published_raw_text:
        return ""

    parsed_dt = parse_published_datetime(
        published_raw_text,
        collected_at=collected_at or _iso_now(),
    )
    if parsed_dt:
        return format_eastern_time(parsed_dt.isoformat(timespec="seconds").replace("+00:00", "Z"))
    return published_raw_text


def summarize_macro_government_climate(articles: list[dict[str, Any]]) -> dict[str, Any]:
    relevant_articles: list[dict[str, Any]] = []
    source_names: set[str] = set()

    for article in articles:
        title = str(article.get("title", "") or "").lower()
        summary = str(article.get("summary", "") or "").lower()
        source_name = str(article.get("source_name", "") or "")
        source_key = str(article.get("source_key", "") or "").lower()
        event_type = str(article.get("event_type", "") or "")
        text_blob = f"{title} {summary}"
        keyword_match = any(keyword in text_blob for keyword in MACRO_GOVERNMENT_KEYWORDS)
        government_source_match = "sec" in source_key or "government" in source_key
        event_match = event_type in MACRO_GOVERNMENT_EVENT_TYPES
        if keyword_match or government_source_match or event_match:
            relevant_articles.append(article)
            if source_name:
                source_names.add(source_name)

    if not relevant_articles:
        return {
            "label": "Limited Signal",
            "score": 0.0,
            "confidence": 0.0,
            "signal_confidence": 0.0,
            "avg_relevance_confidence": 0.0,
            "article_count": 0,
            "source_count": 0,
        }

    sentiment = summarize_article_sentiment(relevant_articles)
    score = float(sentiment.get("score", 0.0) or 0.0)
    confidence = float(sentiment.get("confidence", 0.0) or 0.0)

    if score >= 0.32 and confidence >= 0.5:
        label = "Very Good"
    elif score >= 0.12:
        label = "Good"
    elif score <= -0.32 and confidence >= 0.5:
        label = "Poor"
    elif score <= -0.12:
        label = "Weak"
    else:
        label = "Mixed"

    return {
        **sentiment,
        "label": label,
        "article_count": len(relevant_articles),
        "source_count": len(source_names),
    }


def story_id(ticker: str, row: dict[str, Any]) -> str:
    base = (
        str(row.get("canonical_link", "")),
        str(row.get("normalized_title_key", "")),
        str(row.get("link", "")),
        str(row.get("title", "")),
    )
    compact = next((part for part in base if part), "")
    return f"{ticker}::{compact}"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_watchlist_metadata(path: str) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    payload = load_json(Path(path), [])
    if not isinstance(payload, list):
        return metadata
    for item in payload:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        metadata[ticker] = {
            "company": str(item.get("company", "")).strip(),
            "sector": str(item.get("sector", "")).strip(),
            "industry": str(item.get("industry", "")).strip(),
        }
    return metadata


def bucket_label(bucket: str) -> str:
    return {
        "stories": "Primary",
        "related_context": "Related",
        "review_candidates": "Review",
        "rejections": "Rejected",
    }.get(bucket, bucket.replace("_", " ").title())


def bucket_priority(bucket: str) -> int:
    return {
        "stories": 0,
        "related_context": 1,
        "review_candidates": 2,
        "rejections": 3,
    }.get(bucket, 9)


def float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def source_descriptor(source_key: str, source_group: str) -> tuple[str, str]:
    normalized_key = str(source_key or "").strip().lower()
    normalized_group = str(source_group or "").strip().lower()
    if normalized_group == "structured_news" and normalized_key in STRUCTURED_SOURCES:
        source = STRUCTURED_SOURCES[normalized_key]
        return source.source_family, source.quality_tier
    if normalized_group == "baseline_rss" and normalized_key in RSS_SOURCES:
        source = RSS_SOURCES[normalized_key]
        return source.source_family, source.quality_tier
    if normalized_key == "stocktwits":
        return "unstructured", "supplemental_unstructured"
    return "structured", "secondary_structured"


def source_tier_rank(source_key: str, source_group: str) -> int:
    _, quality_tier = source_descriptor(source_key, source_group)
    return SOURCE_TIER_PRIORITY.get(quality_tier, 9)


def enrich_article_source_metadata(article: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(article)
    source_key = str(enriched.get("source_key", ""))
    source_group = str(enriched.get("source_group", ""))
    source_family, quality_tier = source_descriptor(source_key, source_group)
    enriched["source_family"] = source_family
    enriched["source_quality_tier"] = quality_tier
    enriched["source_tier_rank"] = SOURCE_TIER_PRIORITY.get(quality_tier, 9)
    # Reuse persisted sentiment whenever it already exists in SQLite-backed
    # article-pool rows; rescoring every visible article at request time can
    # stall hosted startup and page loads.
    has_cached_sentiment = any(
        key in enriched
        for key in (
            "sentiment_label",
            "sentiment_score",
            "sentiment_confidence",
            "signal_confidence",
            "sentiment_pipeline_stage",
            "sentiment_model_used",
        )
    )
    if not has_cached_sentiment:
        enriched.update(score_article_sentiment(enriched, allow_finbert=False))
    raw_source_name = str(enriched.get("source_name", "") or "").strip()
    provider_name = infer_syndicated_provider(enriched)
    enriched["provider_name"] = provider_name
    if provider_name and provider_name.lower() not in raw_source_name.lower():
        enriched["display_source_name"] = f"{provider_name} via {raw_source_name}" if raw_source_name else provider_name
    else:
        enriched["display_source_name"] = raw_source_name or provider_name
    primary_category, category_tags = classify_article_categories(enriched)
    enriched["primary_category"] = primary_category
    enriched["category_tags"] = category_tags
    return enriched


def infer_syndicated_provider(article: dict[str, Any]) -> str:
    source_key = str(article.get("source_key", "") or "").strip().lower()
    if source_key.startswith("sec"):
        return "SEC"
    blobs = " ".join(
        str(article.get(field, "") or "").lower()
        for field in (
            "link",
            "canonical_link",
            "summary",
            "notes",
            "title",
            "source_name",
        )
    )
    for needle, label in SYNDICATED_PROVIDER_PATTERNS:
        if needle in blobs:
            return label
    if "provider: reuters" in blobs:
        return "Reuters"
    return ""


def classify_article_categories(article: dict[str, Any]) -> tuple[str, list[str]]:
    title = str(article.get("title", "") or "").lower()
    summary = str(article.get("summary", "") or "").lower()
    source_key = str(article.get("source_key", "") or "").lower()
    source_name = str(article.get("source_name", "") or "").lower()
    event_type = str(article.get("event_type", "") or "").lower()
    event_types = [
        str(value).lower()
        for value in article.get("event_types", []) or []
        if str(value).strip()
    ]
    text_blob = " ".join(
        part
        for part in (
            title,
            summary,
            event_type,
            " ".join(event_types),
        )
        if part
    )
    matched_tickers = article.get("matched_tickers", []) or []
    matched_ticker_count = int(article.get("matched_ticker_count", len(matched_tickers)) or len(matched_tickers))

    tags: set[str] = set()

    if source_key in PRESS_RELEASE_SOURCE_KEYS or source_name in PRESS_RELEASE_SOURCE_NAMES:
        tags.add("press_releases")

    if source_key.startswith("sec") or any(keyword in text_blob for keyword in FILING_CATEGORY_KEYWORDS):
        tags.add("filings")

    if any(keyword in text_blob for keyword in MACRO_GOVERNMENT_KEYWORDS):
        tags.add("economy")

    if any(keyword in text_blob for keyword in CRYPTO_CATEGORY_KEYWORDS):
        tags.add("crypto")

    if any(keyword in text_blob for keyword in COMMODITIES_CATEGORY_KEYWORDS):
        tags.add("commodities")

    if any(keyword in text_blob for keyword in MARKETS_CATEGORY_KEYWORDS):
        tags.add("markets")

    if (
        matched_ticker_count > 0
        or event_type in {"earnings_or_guidance", "analyst_rating_or_target", "general_company_focus", "executive_change"}
        or any(
            value in {"earnings_or_guidance", "analyst_rating_or_target", "general_company_focus", "executive_change"}
            for value in event_types
        )
    ):
        tags.add("equities")

    if not tags:
        tags.add("equities")

    ordered_tags = [category for category in CATEGORY_PRIORITY if category in tags]
    primary_category = ordered_tags[0] if ordered_tags else "equities"
    return primary_category, ordered_tags


def flatten_feed_rows(tickers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in tickers:
        ticker = str(item.get("ticker", ""))
        company = str(item.get("company", ""))
        sector = str(item.get("sector", ""))
        industry = str(item.get("industry", ""))
        for bucket in ("stories", "related_context", "review_candidates"):
            for rank, row in enumerate(item.get(bucket, []), start=1):
                signal_strength = float_value(row.get("signal_strength"))
                source_key = str(row.get("source_key", ""))
                source_group = str(row.get("source_group", ""))
                source_family, quality_tier = source_descriptor(source_key, source_group)
                published_at = str(row.get("published_at", ""))
                published_raw = str(row.get("published_raw", ""))
                collected_at = str(row.get("collected_at", ""))
                sentiment_fields = score_article_sentiment(row, allow_finbert=False)
                primary_category, category_tags = classify_article_categories(
                    {
                        **row,
                        "matched_ticker_count": 1 if ticker else 0,
                        "matched_tickers": [ticker] if ticker else [],
                    }
                )
                rows.append(
                    {
                        "story_key": str(row.get("story_key", "")),
                        "ticker": ticker,
                        "company": company,
                        "sector": sector,
                        "industry": industry,
                        "bucket": bucket,
                        "bucket_label": bucket_label(bucket),
                        "bucket_priority": bucket_priority(bucket),
                        "rank": rank,
                        "title": str(row.get("title", "")),
                        "link": str(row.get("link", "")),
                        "source_name": str(row.get("display_source_name", row.get("source_name", ""))),
                        "source_origin_name": str(row.get("source_name", "")),
                        "provider_name": str(row.get("provider_name", "")),
                        "source_key": source_key,
                        "source_family": source_family,
                        "source_quality_tier": quality_tier,
                        "source_tier_rank": SOURCE_TIER_PRIORITY.get(quality_tier, 9),
                        "event_type": str(row.get("event_type", "")),
                        "signal_strength": signal_strength,
                        "signal_display": str(row.get("signal_strength", "")) or "0",
                        "published": format_published_display(
                            published_at,
                            published_raw,
                            collected_at=collected_at,
                        ),
                        "published_raw": published_raw,
                        "published_at": published_at,
                        "first_seen_at": str(row.get("first_seen_at", "")),
                        "last_seen_at": str(row.get("last_seen_at", "")),
                        "collected_at": collected_at,
                        "coverage_count": int(row.get("coverage_count", 0) or 0),
                        "exposure_observation_count": int(row.get("coverage_count", 0) or 0),
                        "exposure_source_count": len(row.get("coverage_sources", []) or []),
                        "exposure_weight": _row_exposure_weight(row),
                        "summary": str(row.get("summary", "")),
                        "is_new": bool(row.get("is_new")),
                        "needs_translation": likely_non_english(
                            f"{str(row.get('title', '') or '')} {str(row.get('summary', '') or '')}"
                        ),
                        "primary_category": primary_category,
                        "category_tags": category_tags,
                        **sentiment_fields,
                        "prediction_weight": round(
                            _prediction_weight(
                                sentiment_score=float(sentiment_fields.get("sentiment_score", 0.0) or 0.0),
                                sentiment_confidence=float(sentiment_fields.get("sentiment_confidence", 0.0) or 0.0),
                                ticker_relevance=float(sentiment_fields.get("ticker_relevance_confidence", 0.0) or 0.0),
                                exposure_weight=_row_exposure_weight(row),
                            ),
                            3,
                        ),
                    }
                )

    rows.sort(
        key=lambda row: (
            0 if row["is_new"] else 1,
            row["bucket_priority"],
            row.get("source_tier_rank", 9),
            -row["signal_strength"],
            row["ticker"],
            row["rank"],
        )
    )
    return rows


def flatten_article_pool_rows(
    article_pool: dict[str, Any],
    watchlist_metadata: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, article in enumerate(article_pool.get("articles", []) or [], start=1):
        article = enrich_article_source_metadata(article)
        matched_tickers = [
            str(value).strip().upper()
            for value in article.get("matched_tickers", []) or []
            if str(value).strip()
        ]
        matched_ticker_count = len(matched_tickers)
        primary_ticker = matched_tickers[0] if matched_tickers else ""
        ticker_metadata = watchlist_metadata.get(primary_ticker, {}) if matched_ticker_count == 1 else {}
        buckets = {str(value) for value in article.get("buckets", []) if str(value).strip()}
        if "stories" in buckets:
            bucket = "stories"
        elif "related_context" in buckets:
            bucket = "related_context"
        elif "review_candidates" in buckets:
            bucket = "review_candidates"
        else:
            bucket = "stories"
        event_types = [str(value) for value in article.get("event_types", []) if str(value).strip()]
        signal_strength = float_value(article.get("signal_strength"))
        published_at = str(article.get("published_at", ""))
        published_raw = str(article.get("published_raw", ""))
        collected_at = str(article.get("collected_at", ""))
        rows.append(
            {
                "story_key": str(article.get("story_key", "")),
                "ticker": primary_ticker,
                "company": str(ticker_metadata.get("company", "")),
                "sector": str(ticker_metadata.get("sector", "")),
                "industry": str(ticker_metadata.get("industry", "")),
                "bucket": bucket,
                "bucket_label": bucket_label(bucket),
                "bucket_priority": bucket_priority(bucket),
                "rank": rank,
                "title": str(article.get("title", "")),
                "link": str(article.get("link", "")),
                "source_name": str(article.get("display_source_name", article.get("source_name", ""))),
                "source_origin_name": str(article.get("source_name", "")),
                "provider_name": str(article.get("provider_name", "")),
                "source_key": str(article.get("source_key", "")),
                "source_family": str(article.get("source_family", "")),
                "source_quality_tier": str(article.get("source_quality_tier", "")),
                "source_tier_rank": int(article.get("source_tier_rank", 9) or 9),
                "event_type": event_types[0] if event_types else "",
                "signal_strength": signal_strength,
                "signal_display": f"{signal_strength:g}",
                "published": format_published_display(
                    published_at,
                    published_raw,
                    collected_at=collected_at,
                ),
                "published_raw": published_raw,
                "published_at": published_at,
                "first_seen_at": str(article.get("first_seen_at", "")),
                "last_seen_at": str(article.get("last_seen_at", "")),
                "collected_at": collected_at,
                "coverage_count": int(article.get("coverage_count", 0) or 0),
                "coverage_sources": list(article.get("coverage_sources", []) or []),
                "exposure_observation_count": int(article.get("exposure_observation_count", article.get("coverage_count", 0)) or 0),
                "exposure_source_count": int(article.get("exposure_source_count", 0) or 0),
                "exposure_weight": float(article.get("exposure_weight", 0.0) or 0.0),
                "summary": "",
                "is_new": bool(article.get("is_new")),
                "matched_tickers": matched_tickers,
                "matched_ticker_count": matched_ticker_count,
                "primary_category": str(article.get("primary_category", "")),
                "category_tags": list(article.get("category_tags", []) or []),
                "needs_translation": likely_non_english(str(article.get("title", "") or "")),
                "sentiment_label": str(article.get("sentiment_label", "")),
                "sentiment_score": float(article.get("sentiment_score", 0.0) or 0.0),
                "sentiment_confidence": float(article.get("sentiment_confidence", 0.0) or 0.0),
                "raw_sentiment_confidence": float(article.get("raw_sentiment_confidence", 0.0) or 0.0),
                "signal_confidence": float(article.get("signal_confidence", 0.0) or 0.0),
                "ticker_relevance_confidence": float(article.get("ticker_relevance_confidence", 0.0) or 0.0),
                "ticker_relevance_markers": list(article.get("ticker_relevance_markers", []) or []),
                "sentiment_pipeline_stage": str(article.get("sentiment_pipeline_stage", "")),
                "sentiment_model_used": str(article.get("sentiment_model_used", "")),
                "future_model_target": str(article.get("future_model_target", "")),
                "finbert_ready": bool(article.get("finbert_ready")),
                "finbert_readiness_reason": str(article.get("finbert_readiness_reason", "")),
                "finbert_model_name": str(article.get("finbert_model_name", "")),
                "finbert_model_available": bool(article.get("finbert_model_available")),
                "finbert_label": str(article.get("finbert_label", "")),
                "finbert_score": float(article.get("finbert_score", 0.0) or 0.0),
                "finbert_confidence": float(article.get("finbert_confidence", 0.0) or 0.0),
                "finbert_input_length": int(article.get("finbert_input_length", 0) or 0),
                "finbert_uses_translation": bool(article.get("finbert_uses_translation")),
                "market_impact_bias": str(article.get("market_impact_bias", "")),
                "sentiment_positive_markers": list(article.get("sentiment_positive_markers", []) or []),
                "sentiment_negative_markers": list(article.get("sentiment_negative_markers", []) or []),
                "prediction_weight": round(
                    _prediction_weight(
                        sentiment_score=float(article.get("sentiment_score", 0.0) or 0.0),
                        sentiment_confidence=float(article.get("sentiment_confidence", 0.0) or 0.0),
                        ticker_relevance=float(article.get("ticker_relevance_confidence", 0.0) or 0.0),
                        exposure_weight=float(article.get("exposure_weight", 0.0) or 0.0) or 1.0,
                    ),
                    3,
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            0 if row["is_new"] else 1,
            row["bucket_priority"],
            row.get("source_tier_rank", 9),
            -row["signal_strength"],
            row["ticker"],
            row["rank"],
        )
    )
    return rows


def summarize_ticker_payload(ticker_payload: dict[str, Any]) -> dict[str, Any]:
    stories = list(ticker_payload.get("stories", []))
    related = list(ticker_payload.get("related_context", []))
    review = list(ticker_payload.get("review_candidates", []))
    all_rows = stories + related + review
    scored_rows = [enrich_article_source_metadata(row) for row in all_rows]

    source_counts: dict[str, int] = {}
    for row in scored_rows:
        source_name = str(row.get("source_name", "")).strip()
        if source_name:
            source_counts[source_name] = source_counts.get(source_name, 0) + 1

    first_seen_values = [str(row.get("first_seen_at", "")).strip() for row in scored_rows if str(row.get("first_seen_at", "")).strip()]
    last_seen_values = [str(row.get("last_seen_at", "")).strip() for row in scored_rows if str(row.get("last_seen_at", "")).strip()]
    strongest_positive = max(
        (
            row for row in scored_rows
            if str(row.get("sentiment_label", "")).lower() == "bullish"
        ),
        key=lambda row: (
            float_value(row.get("sentiment_confidence")),
            float_value(row.get("sentiment_score")),
            float_value(row.get("signal_strength")),
        ),
        default=None,
    )
    strongest_negative = max(
        (
            row for row in scored_rows
            if str(row.get("sentiment_label", "")).lower() == "bearish"
        ),
        key=lambda row: (
            float_value(row.get("sentiment_confidence")),
            abs(float_value(row.get("sentiment_score"))),
            float_value(row.get("signal_strength")),
        ),
        default=None,
    )

    return {
        "raw_match_count": int(ticker_payload.get("raw_match_count", 0) or 0),
        "primary_count": len(stories),
        "related_count": len(related),
        "review_count": len(review),
        "source_count": len(source_counts),
        "sources": [
            {"source_name": source_name, "row_count": count}
            for source_name, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "freshness": {
            "first_seen_earliest": min(first_seen_values) if first_seen_values else "",
            "last_seen_latest": max(last_seen_values) if last_seen_values else "",
        },
        "sentiment": summarize_article_sentiment(scored_rows),
        "strongest_positive_title": str(strongest_positive.get("title", "")) if strongest_positive else "",
        "strongest_negative_title": str(strongest_negative.get("title", "")) if strongest_negative else "",
    }


def _mark_translation_flags(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marked: list[dict[str, Any]] = []
    for row in rows:
        row_copy = enrich_article_source_metadata(row)
        title = str(row_copy.get("title", "") or "")
        summary = str(row_copy.get("summary", "") or "")
        row_copy["needs_translation"] = likely_non_english(f"{title} {summary}")
        marked.append(row_copy)
    return marked


def build_live_ticker_counts(article_pool: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for article in article_pool.get("articles", []) or []:
        matched_tickers = [str(value).upper() for value in article.get("matched_tickers", []) if str(value).strip()]
        buckets = {str(value) for value in article.get("buckets", []) if str(value).strip()}
        is_new = bool(article.get("is_new"))
        for ticker in matched_tickers:
            item = counts.setdefault(
                ticker,
                {
                    "primary_count": 0,
                    "related_count": 0,
                    "review_count": 0,
                    "new_primary_count": 0,
                },
            )
            if "stories" in buckets:
                item["primary_count"] += 1
                if is_new:
                    item["new_primary_count"] += 1
            if "related_context" in buckets:
                item["related_count"] += 1
            if "review_candidates" in buckets:
                item["review_count"] += 1
    return counts


def build_source_visibility_counts(article_pool: dict[str, Any]) -> dict[tuple[str, str], dict[str, int]]:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for article in article_pool.get("articles", []) or []:
        source_key = str(article.get("source_key", "")).strip()
        source_name = str(article.get("source_name", "")).strip()
        if not source_key and not source_name:
            continue
        key = (source_key, source_name)
        item = counts.setdefault(
            key,
            {
                "visible_story_count": 0,
                "visible_primary_count": 0,
                "visible_new_count": 0,
            },
        )
        item["visible_story_count"] += 1
        buckets = set(article.get("buckets", []) or [])
        if "stories" in buckets:
            item["visible_primary_count"] += 1
        if article.get("is_new"):
            item["visible_new_count"] += 1
    return counts


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


def _prediction_weight(
    *,
    sentiment_score: float,
    sentiment_confidence: float,
    ticker_relevance: float,
    exposure_weight: float,
) -> float:
    sentiment_intensity = abs(sentiment_score)
    if sentiment_intensity <= 0:
        return 0.0
    sentiment_floor = max(sentiment_intensity, 0.06)
    confidence_support = max(sentiment_confidence, 0.2)
    relevance_support = max(ticker_relevance, 0.2)
    exposure_support = max(exposure_weight, 1.0)
    return sentiment_floor * confidence_support * relevance_support * exposure_support


def build_momentum_snapshot(
    rows: list[dict[str, Any]],
    watchlist_metadata: dict[str, dict[str, str]],
    *,
    reference_dt: datetime | None = None,
) -> dict[str, Any]:
    if reference_dt is not None:
        now_dt = reference_dt
    else:
        latest_row_dt = max(
            (_row_reference_datetime(row) for row in rows),
            default=None,
        )
        now_dt = latest_row_dt or datetime.now(timezone.utc)
    ticker_scores: dict[str, dict[str, Any]] = {}
    market_windows = {"1h": 0.0, "6h": 0.0, "24h": 0.0}
    density_windows = {"1h": 0.0, "6h": 0.0, "24h": 0.0}

    for row in rows:
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

        row_dt = _row_reference_datetime(row)
        if not row_dt:
            continue

        age_hours = max((now_dt - row_dt).total_seconds() / 3600.0, 0.0)
        if age_hours > 24:
            continue

        sentiment_score = float(row.get("sentiment_score", 0.0) or 0.0)
        sentiment_confidence = max(0.25, float(row.get("sentiment_confidence", 0.0) or 0.0))
        ticker_relevance = max(0.25, float(row.get("ticker_relevance_confidence", 0.0) or 0.0))
        signal_confidence = max(0.25, float(row.get("signal_confidence", sentiment_confidence) or sentiment_confidence))
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
        matched_divisor = max(len(matched_tickers), 1)
        contribution = (sentiment_score * ticker_relevance * sentiment_support * exposure_weight) / matched_divisor
        density_contribution = ((message_density * 0.45) + (exposure_weight * 0.55)) / matched_divisor

        if abs(contribution) < 0.004:
            continue

        if age_hours <= 1:
            window_key = "1h"
        elif age_hours <= 6:
            window_key = "6h"
        else:
            window_key = "24h"

        market_windows[window_key] += contribution
        density_windows[window_key] += density_contribution

        for ticker in matched_tickers:
            metadata = watchlist_metadata.get(ticker, {})
            item = ticker_scores.setdefault(
                ticker,
                {
                    "ticker": ticker,
                    "company": str(metadata.get("company", "")),
                    "sector": str(metadata.get("sector", "")),
                    "industry": str(metadata.get("industry", "")),
                    "article_count": 0,
                    "bullish_count": 0,
                    "bearish_count": 0,
                    "source_names": set(),
                    "message_count": 0.0,
                    "one_hour_score": 0.0,
                    "six_hour_score": 0.0,
                    "twentyfour_hour_score": 0.0,
                    "one_hour_density": 0.0,
                    "six_hour_density": 0.0,
                    "twentyfour_hour_density": 0.0,
                    "latest_seen_at": "",
                },
            )
            item["article_count"] += 1
            item["message_count"] += density_contribution
            item["source_names"].add(str(row.get("source_name", "") or ""))
            item["latest_seen_at"] = max(item["latest_seen_at"], str(row.get("last_seen_at", "") or str(row.get("collected_at", "")) or ""))
            if str(row.get("sentiment_label", "")).lower() == "bullish":
                item["bullish_count"] += 1
            elif str(row.get("sentiment_label", "")).lower() == "bearish":
                item["bearish_count"] += 1

            if window_key == "1h":
                item["one_hour_score"] += contribution
                item["one_hour_density"] += density_contribution
            elif window_key == "6h":
                item["six_hour_score"] += contribution
                item["six_hour_density"] += density_contribution
            else:
                item["twentyfour_hour_score"] += contribution
                item["twentyfour_hour_density"] += density_contribution

    ranked_rows: list[dict[str, Any]] = []
    for item in ticker_scores.values():
        momentum_score = (
            item["one_hour_score"] * 1.25
            + item["six_hour_score"] * 0.7
            + item["twentyfour_hour_score"] * 0.35
        )
        message_density_score = (
            item["one_hour_density"] * 1.25
            + item["six_hour_density"] * 0.7
            + item["twentyfour_hour_density"] * 0.35
        )
        if momentum_score >= 0.16:
            label = "Bullish Build"
        elif momentum_score <= -0.16:
            label = "Bearish Build"
        else:
            label = "Mixed"
        ranked_rows.append(
            {
                "ticker": item["ticker"],
                "company": item["company"],
                "sector": item["sector"],
                "industry": item["industry"],
                "momentum_score": round(momentum_score, 3),
                "message_density_score": round(message_density_score, 3),
                "one_hour_score": round(item["one_hour_score"], 3),
                "six_hour_score": round(item["six_hour_score"], 3),
                "twentyfour_hour_score": round(item["twentyfour_hour_score"], 3),
                "one_hour_density": round(item["one_hour_density"], 3),
                "six_hour_density": round(item["six_hour_density"], 3),
                "twentyfour_hour_density": round(item["twentyfour_hour_density"], 3),
                "article_count": item["article_count"],
                "message_count": round(item["message_count"], 3),
                "source_count": len([value for value in item["source_names"] if value]),
                "bullish_count": item["bullish_count"],
                "bearish_count": item["bearish_count"],
                "label": label,
                "latest_seen_at": item["latest_seen_at"],
            }
        )

    positive_rows = sorted(
        [row for row in ranked_rows if row["momentum_score"] > 0],
        key=lambda row: (-row["momentum_score"], -row["one_hour_score"], row["ticker"]),
    )
    negative_rows = sorted(
        [row for row in ranked_rows if row["momentum_score"] < 0],
        key=lambda row: (row["momentum_score"], row["one_hour_score"], row["ticker"]),
    )

    return {
        "top_positive": positive_rows[:8],
        "top_negative": negative_rows[:8],
        "leaders": sorted(ranked_rows, key=lambda row: (-abs(row["momentum_score"]), row["ticker"]))[:10],
        "window_totals": {
            "1h": round(market_windows["1h"], 3),
            "6h": round(market_windows["6h"], 3),
            "24h": round(market_windows["24h"], 3),
        },
        "message_density_windows": {
            "1h": round(density_windows["1h"], 3),
            "6h": round(density_windows["6h"], 3),
            "24h": round(density_windows["24h"], 3),
        },
    }


def build_chart_snapshot(
    rows: list[dict[str, Any]],
    market_sentiment: dict[str, Any],
    momentum_snapshot: dict[str, Any],
) -> dict[str, Any]:
    sentiment_distribution = {
        "bullish": int(market_sentiment.get("bullish_count", 0) or 0),
        "bearish": int(market_sentiment.get("bearish_count", 0) or 0),
        "mixed": int(market_sentiment.get("mixed_count", 0) or 0),
        "neutral": int(market_sentiment.get("neutral_count", 0) or 0),
    }

    source_counts: dict[str, int] = {}
    for row in rows:
        source_name = str(row.get("source_name", "") or "").strip()
        if source_name:
            source_counts[source_name] = source_counts.get(source_name, 0) + 1

    top_sources = [
        {"label": source_name, "value": count}
        for source_name, count in sorted(
            source_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:6]
    ]

    momentum_windows = momentum_snapshot.get("window_totals", {})
    density_windows = momentum_snapshot.get("message_density_windows", {})

    return {
        "sentiment_distribution": [
            {"label": "Bullish", "value": sentiment_distribution["bullish"], "tone": "bullish"},
            {"label": "Bearish", "value": sentiment_distribution["bearish"], "tone": "bearish"},
            {"label": "Mixed", "value": sentiment_distribution["mixed"], "tone": "mixed"},
            {"label": "Neutral", "value": sentiment_distribution["neutral"], "tone": "neutral"},
        ],
        "source_visibility": top_sources,
        "market_pulse": [
            {"label": "1H", "value": float(momentum_windows.get("1h", 0.0) or 0.0)},
            {"label": "6H", "value": float(momentum_windows.get("6h", 0.0) or 0.0)},
            {"label": "24H", "value": float(momentum_windows.get("24h", 0.0) or 0.0)},
        ],
        "message_density": [
            {"label": "1H", "value": float(density_windows.get("1h", 0.0) or 0.0), "tone": "accent"},
            {"label": "6H", "value": float(density_windows.get("6h", 0.0) or 0.0), "tone": "accent"},
            {"label": "24H", "value": float(density_windows.get("24h", 0.0) or 0.0), "tone": "accent"},
        ],
    }


def build_category_snapshot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        category_key = str(row.get("primary_category", "") or "").strip().lower()
        if not category_key:
            continue
        counts[category_key] = counts.get(category_key, 0) + 1

    return [
        {
            "key": key,
            "label": CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
            "count": count,
        }
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_sentiment_audit_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_rows = len(rows)
    ready_rows = [row for row in rows if row.get("finbert_ready")]
    active_rows = [
        row
        for row in rows
        if str(row.get("sentiment_pipeline_stage", "")) == "hybrid_finbert_rule"
    ]
    rule_rows = [
        row
        for row in rows
        if str(row.get("sentiment_model_used", "") or "rule_based") == "rule_based"
    ]
    low_confidence_rows = [
        row
        for row in rows
        if float_value(row.get("sentiment_confidence")) < 0.35
    ]
    high_relevance_low_confidence_rows = [
        row
        for row in low_confidence_rows
        if float_value(row.get("ticker_relevance_confidence")) >= 0.55
    ]

    readiness_reasons: dict[str, int] = {}
    label_counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("finbert_readiness_reason", "") or "not_scored")
        readiness_reasons[reason] = readiness_reasons.get(reason, 0) + 1
        label = str(row.get("sentiment_label", "") or "neutral").lower()
        label_counts[label] = label_counts.get(label, 0) + 1

    def sample_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": str(row.get("title", "")),
            "ticker": str(row.get("ticker", "")),
            "matched_tickers": list(row.get("matched_tickers", []) or []),
            "source_name": str(row.get("source_name", "")),
            "sentiment_label": str(row.get("sentiment_label", "")),
            "sentiment_score": float_value(row.get("sentiment_score")),
            "sentiment_confidence": float_value(row.get("sentiment_confidence")),
            "ticker_relevance_confidence": float_value(row.get("ticker_relevance_confidence")),
            "sentiment_model_used": str(row.get("sentiment_model_used", "") or "rule_based"),
            "finbert_label": str(row.get("finbert_label", "")),
            "finbert_confidence": float_value(row.get("finbert_confidence")),
            "finbert_readiness_reason": str(row.get("finbert_readiness_reason", "")),
            "link": str(row.get("link", "")),
        }

    active_examples = sorted(
        active_rows,
        key=lambda row: (
            -float_value(row.get("finbert_confidence")),
            -float_value(row.get("sentiment_confidence")),
            str(row.get("title", "")),
        ),
    )[:4]
    rule_examples = sorted(
        rule_rows,
        key=lambda row: (
            -float_value(row.get("signal_strength")),
            -float_value(row.get("ticker_relevance_confidence")),
            str(row.get("title", "")),
        ),
    )[:4]
    review_examples = sorted(
        high_relevance_low_confidence_rows or low_confidence_rows,
        key=lambda row: (
            -float_value(row.get("ticker_relevance_confidence")),
            float_value(row.get("sentiment_confidence")),
            str(row.get("title", "")),
        ),
    )[:4]
    activation_gap_count = max(len(ready_rows) - len(active_rows), 0)
    recommended_next_actions: list[dict[str, Any]] = []
    if total_rows and len(ready_rows) < total_rows:
        recommended_next_actions.append(
            {
                "area": "FinBERT readiness",
                "priority": "high",
                "count": total_rows - len(ready_rows),
                "action": "Fix remaining model-input blockers before expanding prediction features.",
            }
        )
    if activation_gap_count:
        activation_action = (
            "Run the sentiment snapshot refresh job so ready rows receive stored FinBERT or hybrid outputs."
            if not active_rows
            else "Decide whether to force FinBERT on high-confidence rule rows; the current hybrid path intentionally keeps them on rules."
        )
        recommended_next_actions.append(
            {
                "area": "FinBERT activation",
                "priority": "high" if activation_gap_count >= max(total_rows * 0.25, 1) else "medium",
                "count": activation_gap_count,
                "action": activation_action,
            }
        )
    if high_relevance_low_confidence_rows:
        recommended_next_actions.append(
            {
                "area": "Calibration review",
                "priority": "medium",
                "count": len(high_relevance_low_confidence_rows),
                "action": "Review high-relevance, low-confidence stories and tune event or marker weights.",
            }
        )
    if not recommended_next_actions and total_rows:
        recommended_next_actions.append(
            {
                "area": "Prediction handoff",
                "priority": "ready",
                "count": total_rows,
                "action": "Sentiment coverage is ready to feed prediction and optimization feature engineering.",
            }
        )

    return {
        "total_rows": total_rows,
        "finbert_ready_count": len(ready_rows),
        "finbert_active_count": len(active_rows),
        "finbert_activation_gap_count": activation_gap_count,
        "rule_based_count": len(rule_rows),
        "low_confidence_count": len(low_confidence_rows),
        "high_relevance_low_confidence_count": len(high_relevance_low_confidence_rows),
        "readiness_reasons": sorted(
            (
                {"reason": reason, "count": count}
                for reason, count in readiness_reasons.items()
            ),
            key=lambda item: (-item["count"], item["reason"]),
        ),
        "label_counts": sorted(
            (
                {"label": label.title(), "count": count}
                for label, count in label_counts.items()
            ),
            key=lambda item: (-item["count"], item["label"]),
        ),
        "active_examples": [sample_row(row) for row in active_examples],
        "rule_based_examples": [sample_row(row) for row in rule_examples],
        "review_examples": [sample_row(row) for row in review_examples],
        "recommended_next_actions": recommended_next_actions,
    }


class DashboardState:
    def __init__(
        self,
        *,
        watchlist_file: str,
        snapshot_file: str,
        dashboard_state_file: str,
        cooldown_seconds: int,
        rss_limit: int,
        structured_limit: int,
        state_file: str,
        skip_rss: bool,
        skip_structured: bool,
        sqlite_db: str,
    ) -> None:
        self.watchlist_file = watchlist_file
        self.snapshot_path = Path(snapshot_file)
        self.dashboard_state_path = Path(dashboard_state_file)
        self.cooldown_seconds = cooldown_seconds
        self.rss_limit = rss_limit
        self.structured_limit = structured_limit
        self.state_file = state_file
        self.skip_rss = skip_rss
        self.skip_structured = skip_structured
        self.sqlite_db = sqlite_db
        self.watchlist_metadata = load_watchlist_metadata(watchlist_file)
        self.lock = threading.Lock()
        self.analytics_lock = threading.Lock()
        self.update_in_progress = False
        self.finbert_backfill_in_progress = False
        self._analytics_cache: dict[str, dict[str, Any]] = {}

        self.snapshot: dict[str, Any] = self._load_primary_snapshot()
        persisted = load_json(
            self.dashboard_state_path,
            {
                "last_refresh_epoch": 0.0,
                "last_refresh_iso": "",
                "seen_story_ids": [],
                "last_status": "Dashboard initialized.",
                "finbert_backfill_status": "",
                "finbert_backfill_in_progress": False,
            },
        )
        self.last_refresh_epoch = float(persisted.get("last_refresh_epoch", 0.0) or 0.0)
        self.last_refresh_iso = str(persisted.get("last_refresh_iso", ""))
        self.seen_story_ids = set(str(item) for item in persisted.get("seen_story_ids", []))
        self.last_status = str(persisted.get("last_status", "Dashboard initialized."))
        self.finbert_backfill_status = str(persisted.get("finbert_backfill_status", ""))
        self.finbert_backfill_in_progress = bool(persisted.get("finbert_backfill_in_progress", False))

        if not self.snapshot:
            # A startup rebuild should not fabricate a giant "new story" spike
            # just because the process restarted.
            self.snapshot = self._run_snapshot_update(mark_all_seen=True)
            self.last_status = "Initial snapshot created for dashboard startup."
            self._persist_state()

    def _load_primary_snapshot(self) -> dict[str, Any]:
        if self.sqlite_db:
            db_snapshot = fetch_latest_watchlist_snapshot(self.sqlite_db)
            if db_snapshot:
                return db_snapshot
        return load_json(self.snapshot_path, {})

    def _persist_state(self) -> None:
        self.dashboard_state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_refresh_epoch": self.last_refresh_epoch,
            "last_refresh_iso": self.last_refresh_iso,
            "seen_story_ids": sorted(self.seen_story_ids),
            "last_status": self.last_status,
            "finbert_backfill_status": self.finbert_backfill_status,
            "finbert_backfill_in_progress": self.finbert_backfill_in_progress,
        }
        self.dashboard_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _sync_runtime_state_from_disk(self) -> None:
        if not self.dashboard_state_path.exists():
            return
        persisted = load_json(self.dashboard_state_path, {})
        if not persisted:
            return
        self.last_refresh_epoch = float(persisted.get("last_refresh_epoch", self.last_refresh_epoch) or self.last_refresh_epoch)
        self.last_refresh_iso = str(persisted.get("last_refresh_iso", self.last_refresh_iso or ""))
        self.last_status = str(persisted.get("last_status", self.last_status or "Dashboard initialized."))
        self.finbert_backfill_status = str(persisted.get("finbert_backfill_status", self.finbert_backfill_status or ""))
        self.finbert_backfill_in_progress = bool(
            persisted.get("finbert_backfill_in_progress", self.finbert_backfill_in_progress)
        )

    def _enrich_ticker_metadata(self, ticker_payload: dict[str, Any]) -> dict[str, Any]:
        ticker_copy = dict(ticker_payload)
        ticker = str(ticker_copy.get("ticker", ""))
        metadata = self.watchlist_metadata.get(ticker, {})
        if metadata:
            ticker_copy["company"] = str(ticker_copy.get("company", "") or metadata.get("company", ""))
            ticker_copy["sector"] = str(ticker_copy.get("sector", "") or metadata.get("sector", ""))
            ticker_copy["industry"] = str(ticker_copy.get("industry", "") or metadata.get("industry", ""))
        for bucket in ("stories", "related_context", "review_candidates", "rejections"):
            normalized_rows: list[dict[str, Any]] = []
            for row in ticker_copy.get(bucket, []) or []:
                row_copy = dict(row)
                row_copy["published_display"] = format_published_display(
                    str(row_copy.get("published_at", "")),
                    str(row_copy.get("published_raw", "")),
                    collected_at=str(row_copy.get("collected_at", "")),
                )
                normalized_rows.append(row_copy)
            ticker_copy[bucket] = normalized_rows
        return ticker_copy

    def _annotate_snapshot(self, snapshot: dict[str, Any], *, mark_all_seen: bool) -> dict[str, Any]:
        snapshot = dict(snapshot)
        annotated_tickers: list[dict[str, Any]] = []
        newly_seen_ids: set[str] = set()

        for ticker_payload in snapshot.get("tickers", []):
            ticker_copy = self._enrich_ticker_metadata(ticker_payload)
            ticker = str(ticker_copy.get("ticker", ""))
            new_primary_count = 0

            annotated_stories = []
            for row in ticker_copy.get("stories", []):
                story_copy = dict(row)
                current_story_id = story_id(ticker, story_copy)
                is_new = current_story_id not in self.seen_story_ids
                story_copy["story_id"] = current_story_id
                story_copy["is_new"] = is_new
                annotated_stories.append(story_copy)
                if is_new:
                    new_primary_count += 1
                    newly_seen_ids.add(current_story_id)

            annotated_related = []
            for row in ticker_copy.get("related_context", []):
                row_copy = dict(row)
                current_story_id = story_id(ticker, row_copy)
                is_new = current_story_id not in self.seen_story_ids
                row_copy["story_id"] = current_story_id
                row_copy["is_new"] = is_new
                annotated_related.append(row_copy)
                if is_new:
                    newly_seen_ids.add(current_story_id)

            annotated_review = []
            for row in ticker_copy.get("review_candidates", []):
                row_copy = dict(row)
                current_story_id = story_id(ticker, row_copy)
                is_new = current_story_id not in self.seen_story_ids
                row_copy["story_id"] = current_story_id
                row_copy["is_new"] = is_new
                annotated_review.append(row_copy)
                if is_new:
                    newly_seen_ids.add(current_story_id)

            ticker_copy["stories"] = annotated_stories
            ticker_copy["related_context"] = annotated_related
            ticker_copy["review_candidates"] = annotated_review
            ticker_copy["new_primary_count"] = new_primary_count
            annotated_tickers.append(ticker_copy)

        snapshot["tickers"] = annotated_tickers
        if mark_all_seen:
            for ticker_payload in annotated_tickers:
                ticker = str(ticker_payload.get("ticker", ""))
                for bucket in ("stories", "related_context", "review_candidates"):
                    for row in ticker_payload.get(bucket, []):
                        self.seen_story_ids.add(story_id(ticker, row))
        else:
            self.seen_story_ids.update(newly_seen_ids)
        return snapshot

    def _derive_active_tickers(self) -> set[str]:
        active: set[str] = set()

        if self.sqlite_db:
            article_pool = fetch_latest_market_article_pool(self.sqlite_db)
            scored_tickers: dict[str, float] = {}
            for article in article_pool.get("articles", []) or []:
                buckets = set(article.get("buckets", []) or [])
                is_new = bool(article.get("is_new"))
                signal_strength = float(article.get("signal_strength", 0.0) or 0.0)
                for ticker in article.get("matched_tickers", []) or []:
                    normalized = str(ticker).strip().upper()
                    if normalized:
                        score = 1.0
                        if "stories" in buckets:
                            score += 2.0
                        if is_new:
                            score += 2.0
                        score += min(signal_strength / 25.0, 3.0)
                        scored_tickers[normalized] = scored_tickers.get(normalized, 0.0) + score
            if scored_tickers:
                ranked = sorted(scored_tickers.items(), key=lambda item: (-item[1], item[0]))
                active = {ticker for ticker, _ in ranked[:HOT_TICKER_LIMIT]}
                if active:
                    return active

        snapshot = self.snapshot or self._load_primary_snapshot() or {}
        scored_fallback: list[tuple[str, float]] = []
        for item in snapshot.get("tickers", []):
            ticker = str(item.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            stats = item.get("stats", {}) or {}
            score = (
                float(int(stats.get("clustered_story_count", 0) or 0) * 3)
                + float(int(stats.get("related_context_rows", 0) or 0))
                + float(int(stats.get("review_candidate_rows", 0) or 0) * 0.5)
                + float(int(item.get("new_primary_count", 0) or 0) * 4)
                + float(int(item.get("raw_match_count", 0) or 0) * 0.15)
            )
            if score > 0:
                scored_fallback.append((ticker, score))
        if scored_fallback:
            ranked = sorted(scored_fallback, key=lambda item: (-item[1], item[0]))
            return {ticker for ticker, _ in ranked[:HOT_TICKER_LIMIT]}
        return active

    def _run_snapshot_update(self, *, mark_all_seen: bool) -> dict[str, Any]:
        snapshot = build_watchlist_snapshot(
            watchlist_file=self.watchlist_file,
            rss_limit=self.rss_limit,
            structured_limit=self.structured_limit,
            state_file=self.state_file,
            # For the live dashboard we want a stable recent feed, not a
            # "show only never-before-seen links today" audit mode.
            include_seen=True,
            skip_rss=self.skip_rss,
            skip_structured=self.skip_structured,
            sqlite_db="",
            active_tickers=self._derive_active_tickers(),
        )
        annotated = self._annotate_snapshot(snapshot, mark_all_seen=mark_all_seen)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(annotated, indent=2), encoding="utf-8")
        if self.sqlite_db:
            persist_watchlist_snapshot(self.sqlite_db, annotated)
        return annotated

    def _ensure_watchlist_coverage(self, tickers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing_by_ticker = {
            str(item.get("ticker", "")).strip().upper(): dict(item)
            for item in tickers
            if str(item.get("ticker", "")).strip()
        }
        covered: list[dict[str, Any]] = []
        for ticker, metadata in self.watchlist_metadata.items():
            ticker_key = str(ticker).strip().upper()
            item = existing_by_ticker.pop(ticker_key, None)
            if item is None:
                item = {
                    "ticker": ticker_key,
                    "company": str(metadata.get("company", "")),
                    "sector": str(metadata.get("sector", "")),
                    "industry": str(metadata.get("industry", "")),
                    "stories": [],
                    "related_context": [],
                    "review_candidates": [],
                    "rejections": [],
                    "new_primary_count": 0,
                    "raw_match_count": 0,
                    "stats": {
                        "clustered_story_count": 0,
                        "related_context_rows": 0,
                        "review_candidate_rows": 0,
                    },
                }
            covered.append(self._enrich_ticker_metadata(item))
        return covered

    def _cached_payload(
        self,
        cache_key: str,
        *,
        ttl_seconds: int,
        builder: Any,
    ) -> dict[str, Any]:
        now_epoch = time.time()
        with self.analytics_lock:
            cached = self._analytics_cache.get(cache_key)
            if cached and (now_epoch - float(cached.get("built_at_epoch", 0.0) or 0.0)) <= ttl_seconds:
                return dict(cached.get("payload", {}))

        payload = builder()
        with self.analytics_lock:
            self._analytics_cache[cache_key] = {
                "built_at_epoch": now_epoch,
                "payload": payload,
            }
        return payload

    def _auto_finbert_enabled(self) -> bool:
        default = "1"
        value = str(os.environ.get("STOCK_DASHBOARD_AUTO_FINBERT", default) or default).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _auto_finbert_limit(self) -> int:
        safe_default = min(AUTO_FINBERT_DEFAULT_LIMIT, 250) if _running_on_railway() else AUTO_FINBERT_DEFAULT_LIMIT
        raw = str(os.environ.get("STOCK_DASHBOARD_AUTO_FINBERT_LIMIT", safe_default) or safe_default)
        try:
            return max(1, int(raw))
        except ValueError:
            return safe_default

    def _run_post_refresh_finbert_backfill(self) -> dict[str, Any]:
        if not self.sqlite_db or not self._auto_finbert_enabled():
            return {
                "ok": False,
                "skipped": True,
                "reason": "disabled",
            }

        article_pool = fetch_latest_market_article_pool(self.sqlite_db)
        retained_articles = article_pool.get("articles", []) or []
        if not retained_articles:
            return {
                "ok": False,
                "skipped": True,
                "reason": "no_retained_articles",
            }

        limit = min(len(retained_articles), self._auto_finbert_limit())
        previous_allow = os.environ.get("ALLOW_RAILWAY_FINBERT")
        previous_context = os.environ.get("FINBERT_INFERENCE_CONTEXT")
        previous_disable = os.environ.get("DISABLE_LOCAL_FINBERT")
        previous_download = os.environ.get("FINBERT_ALLOW_DOWNLOAD")
        previous_hf_home = os.environ.get("HF_HOME")
        previous_transformers_cache = os.environ.get("TRANSFORMERS_CACHE")
        model_cache_dir = Path(self.sqlite_db).expanduser().resolve().parent / "hf_cache"
        model_cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.environ["ALLOW_RAILWAY_FINBERT"] = "1"
            os.environ["FINBERT_INFERENCE_CONTEXT"] = "backfill"
            os.environ["DISABLE_LOCAL_FINBERT"] = "0"
            os.environ["FINBERT_ALLOW_DOWNLOAD"] = "1"
            os.environ["HF_HOME"] = str(model_cache_dir)
            os.environ["TRANSFORMERS_CACHE"] = str(model_cache_dir)
            return refresh_story_sentiment_snapshots(
                self.sqlite_db,
                limit=limit,
                dry_run=False,
                force_finbert_ready=True,
            )
        finally:
            if previous_allow is None:
                os.environ.pop("ALLOW_RAILWAY_FINBERT", None)
            else:
                os.environ["ALLOW_RAILWAY_FINBERT"] = previous_allow
            if previous_context is None:
                os.environ.pop("FINBERT_INFERENCE_CONTEXT", None)
            else:
                os.environ["FINBERT_INFERENCE_CONTEXT"] = previous_context
            if previous_disable is None:
                os.environ.pop("DISABLE_LOCAL_FINBERT", None)
            else:
                os.environ["DISABLE_LOCAL_FINBERT"] = previous_disable
            if previous_download is None:
                os.environ.pop("FINBERT_ALLOW_DOWNLOAD", None)
            else:
                os.environ["FINBERT_ALLOW_DOWNLOAD"] = previous_download
            if previous_hf_home is None:
                os.environ.pop("HF_HOME", None)
            else:
                os.environ["HF_HOME"] = previous_hf_home
            if previous_transformers_cache is None:
                os.environ.pop("TRANSFORMERS_CACHE", None)
            else:
                os.environ["TRANSFORMERS_CACHE"] = previous_transformers_cache

    def _launch_async_finbert_backfill(self) -> bool:
        if not self.sqlite_db or not self._auto_finbert_enabled():
            return False
        with self.lock:
            if self.finbert_backfill_in_progress:
                return False
            self.finbert_backfill_in_progress = True
        self.finbert_backfill_status = "FinBERT backfill queued."
        _write_worker_heartbeat(
            "finbert_backfill",
            {
                "ts": _iso_now(),
                "pid": os.getpid(),
                "status": "queued",
                "in_progress": True,
                "message": self.finbert_backfill_status,
            },
        )
        self._persist_state()

        def _worker() -> None:
            try:
                self.finbert_backfill_status = "FinBERT backfill running on the retained article pool."
                _write_worker_heartbeat(
                    "finbert_backfill",
                    {
                        "ts": _iso_now(),
                        "pid": os.getpid(),
                        "status": "running",
                        "in_progress": True,
                        "message": self.finbert_backfill_status,
                    },
                )
                self._persist_state()
                try:
                    finbert_result = self._run_post_refresh_finbert_backfill()
                except Exception as exc:
                    finbert_result = {"ok": False, "skipped": True, "reason": f"error:{exc}"}
                if finbert_result.get("ok") and not finbert_result.get("skipped"):
                    reused_count = int(finbert_result.get("finbert_reused_count", 0) or 0)
                    inferred_count = int(finbert_result.get("finbert_inference_count", 0) or 0)
                    self.finbert_backfill_status = (
                        f"FinBERT cached for {int(finbert_result.get('finbert_applied_count', 0) or 0)} "
                        f"of {int(finbert_result.get('candidate_count', 0) or 0)} retained articles "
                        f"(reused {reused_count}, inferred {inferred_count})."
                    )
                    _write_worker_heartbeat(
                        "finbert_backfill",
                        {
                            "ts": _iso_now(),
                            "pid": os.getpid(),
                            "status": "ok",
                            "in_progress": True,
                            "message": self.finbert_backfill_status,
                            "candidate_count": int(finbert_result.get("candidate_count", 0) or 0),
                            "applied_count": int(finbert_result.get("finbert_applied_count", 0) or 0),
                            "reused_count": reused_count,
                            "inferred_count": inferred_count,
                        },
                    )
                elif str(finbert_result.get("reason", "")).startswith("error:"):
                    self.finbert_backfill_status = "FinBERT backfill skipped after a model-runtime error."
                    _write_worker_heartbeat(
                        "finbert_backfill",
                        {
                            "ts": _iso_now(),
                            "pid": os.getpid(),
                            "status": "error",
                            "in_progress": True,
                            "message": self.finbert_backfill_status,
                            "reason": str(finbert_result.get("reason", "")),
                        },
                    )
                else:
                    reason = str(finbert_result.get("reason", "skipped") or "skipped")
                    self.finbert_backfill_status = f"FinBERT backfill skipped: {reason}."
                    _write_worker_heartbeat(
                        "finbert_backfill",
                        {
                            "ts": _iso_now(),
                            "pid": os.getpid(),
                            "status": "skipped",
                            "in_progress": True,
                            "message": self.finbert_backfill_status,
                            "reason": reason,
                        },
                    )
                self._persist_state()
            finally:
                with self.lock:
                    self.finbert_backfill_in_progress = False
                heartbeat = _read_worker_heartbeat("finbert_backfill")
                heartbeat["in_progress"] = False
                heartbeat["ts"] = _iso_now()
                _write_worker_heartbeat("finbert_backfill", heartbeat)
                self._persist_state()

        threading.Thread(target=_worker, name="finbert-backfill", daemon=True).start()
        return True

    def cooldown_remaining(self) -> int:
        elapsed = time.time() - self.last_refresh_epoch
        remaining = self.cooldown_seconds - int(elapsed)
        return max(0, remaining)

    def state_payload(self) -> dict[str, Any]:
        self._sync_runtime_state_from_disk()
        snapshot = self.snapshot or self._load_primary_snapshot() or {"tickers": []}
        tickers = [self._enrich_ticker_metadata(item) for item in snapshot.get("tickers", [])]
        article_pool = fetch_latest_market_article_pool(self.sqlite_db) if self.sqlite_db else {}
        enriched_article_pool_articles = [
            enrich_article_source_metadata(article)
            for article in article_pool.get("articles", []) or []
        ]
        if article_pool:
            article_pool = {
                **article_pool,
                "articles": enriched_article_pool_articles,
            }
        live_counts = build_live_ticker_counts(article_pool)
        source_visibility_counts = build_source_visibility_counts(article_pool)
        if live_counts:
            refreshed_tickers: list[dict[str, Any]] = []
            for item in tickers:
                ticker_copy = dict(item)
                ticker = str(ticker_copy.get("ticker", "")).upper()
                counts = live_counts.get(ticker)
                if counts:
                    stats = dict(ticker_copy.get("stats", {}))
                    stats["clustered_story_count"] = counts["primary_count"]
                    stats["related_context_rows"] = counts["related_count"]
                    stats["review_candidate_rows"] = counts["review_count"]
                    ticker_copy["stats"] = stats
                    ticker_copy["new_primary_count"] = counts["new_primary_count"]
                else:
                    stats = dict(ticker_copy.get("stats", {}))
                    stats["clustered_story_count"] = 0
                    stats["related_context_rows"] = 0
                    stats["review_candidate_rows"] = 0
                    ticker_copy["stats"] = stats
                    ticker_copy["new_primary_count"] = 0
                refreshed_tickers.append(ticker_copy)
            tickers = refreshed_tickers
        tickers = self._ensure_watchlist_coverage(tickers)
        if article_pool.get("articles"):
            feed_rows = flatten_article_pool_rows(article_pool, self.watchlist_metadata)
        else:
            feed_rows = flatten_feed_rows(tickers)
        source_health = []
        for row in snapshot.get("source_health", []):
            enriched = dict(row)
            if (
                str(enriched.get("source_group", "")).strip() == "structured_news"
                and str(enriched.get("source_key", "")).strip() == "accessnewswire"
            ):
                continue
            visibility = source_visibility_counts.get(
                (
                    str(enriched.get("source_key", "")).strip(),
                    str(enriched.get("source_name", "")).strip(),
                ),
                {},
            )
            enriched["visible_story_count"] = int(visibility.get("visible_story_count", 0) or 0)
            enriched["visible_primary_count"] = int(visibility.get("visible_primary_count", 0) or 0)
            enriched["visible_new_count"] = int(visibility.get("visible_new_count", 0) or 0)
            source_health.append(enriched)
        unique_sources = sorted({row["source_name"] for row in feed_rows if row["source_name"]})
        unique_event_types = sorted({row["event_type"] for row in feed_rows if row["event_type"]})
        unique_sectors = sorted({str(item.get("sector", "")) for item in tickers if str(item.get("sector", ""))})
        unique_industries = sorted({str(item.get("industry", "")) for item in tickers if str(item.get("industry", ""))})
        new_rows = sum(1 for row in feed_rows if row["is_new"])
        ok_source_count = sum(1 for row in source_health if row.get("ok"))
        market_sentiment = summarize_article_sentiment(enriched_article_pool_articles or feed_rows)
        macro_government_climate = summarize_macro_government_climate(enriched_article_pool_articles or feed_rows)
        momentum_snapshot = build_momentum_snapshot(feed_rows, self.watchlist_metadata)
        category_snapshot = build_category_snapshot(feed_rows)
        sentiment_audit_snapshot = build_sentiment_audit_snapshot(enriched_article_pool_articles or feed_rows)
        momentum_lookup = {
            str(item.get("ticker", "")): item
            for item in momentum_snapshot.get("leaders", [])
            if str(item.get("ticker", ""))
        }
        if momentum_lookup:
            refreshed_tickers_with_momentum: list[dict[str, Any]] = []
            for item in tickers:
                ticker_copy = dict(item)
                ticker_copy["momentum"] = momentum_lookup.get(str(ticker_copy.get("ticker", "")), {})
                refreshed_tickers_with_momentum.append(ticker_copy)
            tickers = refreshed_tickers_with_momentum
        chart_snapshot = build_chart_snapshot(feed_rows, market_sentiment, momentum_snapshot)
        sentiment_status = sentiment_runtime_status()
        finbert_worker_health = _read_worker_heartbeat("finbert_backfill")
        quote_service_health = _read_worker_heartbeat("quote_service")
        visible_sentiment_rows = feed_rows
        sentiment_count_rows = enriched_article_pool_articles or feed_rows
        finbert_ready_count = sum(1 for article in visible_sentiment_rows if article.get("finbert_ready"))
        finbert_applied_count = sum(
            1
            for article in visible_sentiment_rows
            if str(article.get("sentiment_pipeline_stage", "")) == "hybrid_finbert_rule"
        )
        translation_pending_count = sum(
            1
            for article in visible_sentiment_rows
            if str(article.get("finbert_readiness_reason", "")) == "translation_pending"
        )
        display_status = self.last_status
        if self.sqlite_db and self.last_refresh_iso and str(self.last_status).startswith("Watchlist refreshed successfully"):
            new_primary_total = sum(
                1
                for article in article_pool.get("articles", []) or []
                if article.get("is_new") and "stories" in set(article.get("buckets", []) or [])
            )
            display_status = (
                f"Watchlist refreshed successfully at {format_eastern_time(self.last_refresh_iso)}. "
                f"New primary stories found: {new_primary_total}."
            )
        return {
            "generated_at": snapshot.get("generated_at", ""),
            "generated_at_display": format_eastern_time(str(snapshot.get("generated_at", ""))),
            "market_session": market_session_label(),
            "pipeline_mode": str(snapshot.get("pipeline_mode", "")),
            "collection_elapsed_seconds": float(snapshot.get("collection_elapsed_seconds", 0.0) or 0.0),
            "last_refresh_iso": self.last_refresh_iso,
            "last_refresh_display": format_eastern_time(self.last_refresh_iso),
            "last_status": display_status,
            "finbert_backfill_status": self.finbert_backfill_status,
            "cooldown_seconds": self.cooldown_seconds,
            "cooldown_remaining": self.cooldown_remaining(),
            "update_in_progress": self.update_in_progress,
            "finbert_backfill_in_progress": self.finbert_backfill_in_progress,
            "tickers": tickers,
            "feed_rows": feed_rows,
            "source_health": source_health,
            "summary": {
                "total_rows": len(feed_rows),
                "new_rows": new_rows,
                "ticker_count": len(tickers),
                "configured_watchlist_count": len(self.watchlist_metadata),
                "snapshot_ticker_count": len(snapshot.get("tickers", []) or []),
                "source_count": len(unique_sources),
                "source_health_total": len(source_health),
                "source_health_ok": ok_source_count,
                "market_sentiment": market_sentiment,
                "macro_government_climate": macro_government_climate,
                "momentum": momentum_snapshot,
                "charts": chart_snapshot,
                "categories": category_snapshot,
                "sentiment_audit": sentiment_audit_snapshot,
                "finbert_ready_count": finbert_ready_count,
                "finbert_applied_count": finbert_applied_count,
                "translation_pending_count": translation_pending_count,
                "sentiment_runtime": sentiment_status,
                "finbert_backfill_status": self.finbert_backfill_status,
                "finbert_backfill_in_progress": self.finbert_backfill_in_progress,
                "worker_health": {
                    "finbert_backfill": finbert_worker_health,
                    "quote_service": quote_service_health,
                },
            },
            "filters": {
                "tickers": [str(item.get("ticker", "")) for item in tickers if str(item.get("ticker", ""))],
                "sectors": unique_sectors,
                "industries": unique_industries,
                "sources": unique_sources,
                "event_types": unique_event_types,
                "buckets": ["Primary", "Related", "Review"],
                "categories": [str(item.get("key", "")) for item in category_snapshot if str(item.get("key", ""))],
            },
        }

    def momentum_payload(self) -> dict[str, Any]:
        def _build() -> dict[str, Any]:
            payload = self.state_payload()
            summary = payload.get("summary", {})
            momentum_snapshot = dict(summary.get("momentum", {}) or {})
            quote_snapshot = fetch_watchlist_quote_snapshot(list(self.watchlist_metadata.keys()))
            return {
                "generated_at": payload.get("generated_at", ""),
                "market_session": payload.get("market_session", ""),
                "momentum": momentum_snapshot,
                "marketboard": build_momentum_marketboard(
                    momentum_snapshot,
                    self.watchlist_metadata,
                    quote_snapshot=quote_snapshot,
                ),
            }

        return self._cached_payload(
            "momentum_payload",
            ttl_seconds=300,
            builder=_build,
        )

    def correlation_payload(self) -> dict[str, Any]:
        def _build() -> dict[str, Any]:
            payload = self.state_payload()
            quote_snapshot = fetch_watchlist_quote_snapshot(list(self.watchlist_metadata.keys()))
            correlation_snapshot = build_correlation_snapshot(
                list(payload.get("feed_rows", []) or []),
                self.watchlist_metadata,
                quote_snapshot=quote_snapshot,
            )
            return {
                "generated_at": payload.get("generated_at", ""),
                "market_session": payload.get("market_session", ""),
                "correlation": correlation_snapshot,
            }

        return self._cached_payload(
            "correlation_payload",
            ttl_seconds=300,
            builder=_build,
        )

    def trigger_update(self) -> tuple[bool, str]:
        with self.lock:
            if self.update_in_progress:
                return False, "Update already in progress."
            remaining = self.cooldown_remaining()
            if remaining > 0:
                return False, f"Update locked for {remaining} more seconds."
            self.update_in_progress = True
            # Start the cooldown when the user clicks update so the refresh
            # runtime counts toward the lock window instead of being added on top.
            self.last_refresh_epoch = time.time()

        try:
            updated = self._run_snapshot_update(mark_all_seen=False)
            self.snapshot = updated
            self.last_refresh_iso = _iso_now()
            with self.analytics_lock:
                self._analytics_cache.clear()
            if self.sqlite_db:
                article_pool = fetch_latest_market_article_pool(self.sqlite_db)
                new_primary_total = sum(
                    1
                    for article in article_pool.get("articles", []) or []
                    if article.get("is_new") and "stories" in set(article.get("buckets", []) or [])
                )
                finbert_started = self._launch_async_finbert_backfill()
            else:
                finbert_started = False
                new_primary_total = sum(int(item.get("new_primary_count", 0)) for item in updated.get("tickers", []))
            self.last_status = (
                f"Watchlist refreshed successfully at {format_eastern_time(self.last_refresh_iso)}. "
                f"New primary stories found: {new_primary_total}."
            )
            if finbert_started:
                self.finbert_backfill_status = "FinBERT backfill queued."
                self.last_status += " FinBERT backfill started in the background."
            self._persist_state()
            return True, self.last_status
        except Exception as exc:
            self.last_status = f"Update failed: {exc}"
            self._persist_state()
            return False, self.last_status
        finally:
            with self.lock:
                self.update_in_progress = False

    def ticker_detail(self, ticker: str) -> dict[str, Any]:
        normalized_ticker = str(ticker).strip().upper()
        if not normalized_ticker:
            return {}

        if self.sqlite_db:
            db_payload = fetch_latest_ticker_snapshot(self.sqlite_db, normalized_ticker)
            ticker_payload = dict(db_payload.get("ticker", {})) if db_payload else {}
            if ticker_payload:
                enriched = self._enrich_ticker_metadata(ticker_payload)
                for bucket in ("stories", "related_context", "review_candidates", "rejections"):
                    enriched[bucket] = _mark_translation_flags(list(enriched.get(bucket, [])))
                return {
                    "generated_at": db_payload.get("generated_at", ""),
                    "pipeline_mode": db_payload.get("pipeline_mode", ""),
                    "collection_elapsed_seconds": db_payload.get("collection_elapsed_seconds", 0.0),
                    "source_health": db_payload.get("source_health", []),
                    "source_history": fetch_ticker_source_history(self.sqlite_db, normalized_ticker),
                    "ticker": enriched,
                    "summary": summarize_ticker_payload(enriched),
                }

        snapshot = self.snapshot or self._load_primary_snapshot() or {}
        for ticker_payload in snapshot.get("tickers", []):
            if str(ticker_payload.get("ticker", "")).strip().upper() == normalized_ticker:
                enriched = self._enrich_ticker_metadata(ticker_payload)
                for bucket in ("stories", "related_context", "review_candidates", "rejections"):
                    enriched[bucket] = _mark_translation_flags(list(enriched.get(bucket, [])))
                return {
                    "generated_at": snapshot.get("generated_at", ""),
                    "pipeline_mode": snapshot.get("pipeline_mode", ""),
                    "collection_elapsed_seconds": snapshot.get("collection_elapsed_seconds", 0.0),
                    "source_health": snapshot.get("source_health", []),
                    "source_history": [],
                    "ticker": enriched,
                    "summary": summarize_ticker_payload(enriched),
                }
        return {}

    def ticker_universe(self) -> dict[str, Any]:
        if self.sqlite_db:
            db_payload = fetch_latest_ticker_universe(self.sqlite_db)
            if db_payload:
                return db_payload

        snapshot = self.snapshot or self._load_primary_snapshot() or {}
        tickers = [self._enrich_ticker_metadata(item) for item in snapshot.get("tickers", [])]
        return {
            "generated_at": snapshot.get("generated_at", ""),
            "pipeline_mode": snapshot.get("pipeline_mode", ""),
            "collection_elapsed_seconds": snapshot.get("collection_elapsed_seconds", 0.0),
            "tickers": [
                {
                    "ticker": str(item.get("ticker", "")),
                    "company": str(item.get("company", "")),
                    "sector": str(item.get("sector", "")),
                    "industry": str(item.get("industry", "")),
                    "raw_match_count": int(item.get("raw_match_count", 0) or 0),
                    "primary_count": int(item.get("stats", {}).get("clustered_story_count", 0) or 0),
                    "related_count": int(item.get("stats", {}).get("related_context_rows", 0) or 0),
                    "review_count": int(item.get("stats", {}).get("review_candidate_rows", 0) or 0),
                    "rejected_count": int(item.get("stats", {}).get("rejected_rows", 0) or 0),
                }
                for item in tickers
            ],
        }

    def market_article_pool(self) -> dict[str, Any]:
        if self.sqlite_db:
            db_payload = fetch_latest_market_article_pool(self.sqlite_db)
            if db_payload:
                enriched = dict(db_payload)
                enriched["articles"] = [
                    enrich_article_source_metadata(article)
                    for article in db_payload.get("articles", []) or []
                ]
                return enriched

        snapshot = self.snapshot or self._load_primary_snapshot() or {}
        articles_by_story: dict[str, dict[str, Any]] = {}
        for ticker_payload in snapshot.get("tickers", []):
            ticker = str(ticker_payload.get("ticker", "")).strip().upper()
            for bucket in ("stories", "related_context", "review_candidates"):
                for row in ticker_payload.get(bucket, []):
                    story_key = str(
                        row.get("canonical_link")
                        or row.get("normalized_title_key")
                        or row.get("link")
                        or row.get("title")
                        or ""
                    )
                    if not story_key:
                        continue
                    article = articles_by_story.setdefault(
                        story_key,
                        {
                            "story_key": story_key,
                            "canonical_link": str(row.get("canonical_link", "")),
                            "normalized_title_key": str(row.get("normalized_title_key", "")),
                            "title": str(row.get("title", "")),
                            "link": str(row.get("link", "")),
                            "source_group": str(row.get("source_group", "")),
                            "source_key": str(row.get("source_key", "")),
                            "source_name": str(row.get("source_name", "")),
                            "collection_method": str(row.get("collection_method", "")),
                            "published_raw": str(row.get("published_raw", "")),
                            "published_display": str(row.get("published_display", row.get("published_raw", ""))),
                            "published_at": str(row.get("published_at", "")),
                            "first_seen_at": str(row.get("first_seen_at", "")),
                            "last_seen_at": str(row.get("last_seen_at", "")),
                            "signal_strength": float_value(row.get("signal_strength")),
                            "coverage_count": int(row.get("coverage_count", 0) or 0),
                            "matched_tickers": set(),
                            "buckets": set(),
                            "event_types": set(),
                        },
                    )
                    article["signal_strength"] = max(article["signal_strength"], float_value(row.get("signal_strength")))
                    article["coverage_count"] = max(article["coverage_count"], int(row.get("coverage_count", 0) or 0))
                    article["is_new"] = article.get("is_new", False) or bool(row.get("is_new"))
                    if ticker:
                        article["matched_tickers"].add(ticker)
                    article["buckets"].add(bucket)
                    event_type = str(row.get("event_type", "")).strip()
                    if event_type:
                        article["event_types"].add(event_type)

        articles = []
        for article in articles_by_story.values():
            matched_tickers = sorted(article["matched_tickers"])
            buckets = sorted(article["buckets"])
            event_types = sorted(article["event_types"])
            articles.append(
                {
                    **{key: value for key, value in article.items() if key not in {"matched_tickers", "buckets", "event_types"}},
                    "matched_tickers": matched_tickers,
                    "matched_ticker_count": len(matched_tickers),
                    "buckets": buckets,
                    "event_types": event_types,
                    "is_new": bool(article.get("is_new")),
                }
            )

        articles.sort(
            key=lambda row: (
                -float_value(row.get("signal_strength")),
                -int(row.get("matched_ticker_count", 0) or 0),
                str(row.get("last_seen_at", "")),
            ),
            reverse=False,
        )
        return {
            "generated_at": snapshot.get("generated_at", ""),
            "pipeline_mode": snapshot.get("pipeline_mode", ""),
            "collection_elapsed_seconds": snapshot.get("collection_elapsed_seconds", 0.0),
            "article_count": len(articles),
            "articles": [enrich_article_source_metadata(article) for article in articles],
        }
