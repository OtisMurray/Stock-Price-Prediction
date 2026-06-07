from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from src.analysis import score_article_sentiment, sentiment_runtime_status
from src.ingestion.rss_sources import RSS_SOURCES
from src.ingestion.structured_sources import STRUCTURED_SOURCES
from src.ingestion.timestamp_utils import normalize_published_fields


LIVE_FEED_RECENT_HOURS = 48
LIVE_FEED_FALLBACK_HOURS = 72
SENTIMENT_CACHE_VERSION = "v1"
SENTIMENT_BUCKET_PRIORITY = {
    "stories": 0,
    "related_context": 1,
    "review_candidates": 2,
    "rejections": 3,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _story_key(row: dict[str, Any]) -> str:
    candidates = (
        str(row.get("canonical_link", "")),
        str(row.get("normalized_title_key", "")),
        str(row.get("link", "")),
        str(row.get("title", "")),
    )
    return next((candidate for candidate in candidates if candidate), "")


def build_translation_lookup_key(
    *,
    story_key: str = "",
    title: str = "",
    summary: str = "",
    target_language: str = "en",
) -> str:
    base = str(story_key or "").strip()
    if base:
        seed = f"story::{base}::{target_language.lower()}"
    else:
        normalized_title = " ".join(str(title or "").split()).strip().lower()
        normalized_summary = " ".join(str(summary or "").split()).strip().lower()
        digest = hashlib.sha1(
            f"{normalized_title}\n{normalized_summary}\n{target_language.lower()}".encode("utf-8")
        ).hexdigest()
        seed = f"text::{digest}"
    return seed


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _sentiment_cache_key(*, story_key: str, ticker: str, bucket: str) -> str:
    return f"{story_key}::{ticker.upper()}::{bucket}"


def _source_descriptor(source_key: str, source_group: str) -> tuple[str, str]:
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


def _sentiment_fields_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sentiment_cache_version": str(row["sentiment_cache_version"] or ""),
        "sentiment_label": str(row["sentiment_label"] or ""),
        "sentiment_score": float(row["sentiment_score"] or 0.0),
        "sentiment_confidence": float(row["sentiment_confidence"] or 0.0),
        "raw_sentiment_confidence": float(row["raw_sentiment_confidence"] or 0.0),
        "signal_confidence": float(row["signal_confidence"] or 0.0),
        "ticker_relevance_confidence": float(row["ticker_relevance_confidence"] or 0.0),
        "ticker_relevance_markers": _json_loads(row["ticker_relevance_markers_json"], []),
        "sentiment_source_weight": float(row["sentiment_source_weight"] or 0.0),
        "market_impact_bias": str(row["market_impact_bias"] or ""),
        "sentiment_positive_markers": _json_loads(row["sentiment_positive_markers_json"], []),
        "sentiment_negative_markers": _json_loads(row["sentiment_negative_markers_json"], []),
        "sentiment_pipeline_stage": str(row["sentiment_pipeline_stage"] or ""),
        "sentiment_model_used": str(row["sentiment_model_used"] or ""),
        "future_model_target": str(row["future_model_target"] or ""),
        "finbert_ready": bool(row["finbert_ready"]),
        "finbert_readiness_reason": str(row["finbert_readiness_reason"] or ""),
        "finbert_input_length": int(row["finbert_input_length"] or 0),
        "finbert_model_available": bool(row["finbert_model_available"]),
        "finbert_label": str(row["finbert_label"] or ""),
        "finbert_score": float(row["finbert_score"] or 0.0),
        "finbert_confidence": float(row["finbert_confidence"] or 0.0),
        "finbert_positive_probability": float(row["finbert_positive_probability"] or 0.0),
        "finbert_negative_probability": float(row["finbert_negative_probability"] or 0.0),
        "finbert_neutral_probability": float(row["finbert_neutral_probability"] or 0.0),
    }


def _compute_story_exposure_metrics(*, coverage_count: Any, coverage_sources: Any) -> dict[str, Any]:
    observation_count = max(int(coverage_count or 0), 1)
    normalized_sources = sorted(
        {
            str(source).strip()
            for source in (coverage_sources or [])
            if str(source).strip()
        }
    )
    source_count = len(normalized_sources)
    exposure_weight = min(
        4.0,
        1.0
        + (0.55 * math.log1p(max(observation_count - 1, 0)))
        + (0.7 * math.log1p(max(source_count - 1, 0))),
    )
    return {
        "exposure_observation_count": observation_count,
        "exposure_source_count": source_count,
        "exposure_sources": normalized_sources,
        "exposure_weight": round(exposure_weight, 3),
    }


def _sentiment_snapshot_payload(row: dict[str, Any], *, force_finbert_ready: bool = False) -> dict[str, Any]:
    sentiment = score_article_sentiment(row, force_finbert_ready=force_finbert_ready)
    exposure = _compute_story_exposure_metrics(
        coverage_count=row.get("coverage_count"),
        coverage_sources=row.get("coverage_sources", []),
    )
    return {
        "sentiment_label": str(sentiment.get("sentiment_label", "")),
        "sentiment_score": float(sentiment.get("sentiment_score", 0.0) or 0.0),
        "sentiment_confidence": float(sentiment.get("sentiment_confidence", 0.0) or 0.0),
        "raw_sentiment_confidence": float(sentiment.get("raw_sentiment_confidence", 0.0) or 0.0),
        "signal_confidence": float(sentiment.get("signal_confidence", 0.0) or 0.0),
        "ticker_relevance_confidence": float(sentiment.get("ticker_relevance_confidence", 0.0) or 0.0),
        "ticker_relevance_markers": list(sentiment.get("ticker_relevance_markers", []) or []),
        "sentiment_source_weight": float(sentiment.get("sentiment_source_weight", 0.0) or 0.0),
        "market_impact_bias": str(sentiment.get("market_impact_bias", "")),
        "sentiment_positive_markers": list(sentiment.get("sentiment_positive_markers", []) or []),
        "sentiment_negative_markers": list(sentiment.get("sentiment_negative_markers", []) or []),
        "sentiment_pipeline_stage": str(sentiment.get("sentiment_pipeline_stage", "")),
        "sentiment_model_used": str(sentiment.get("sentiment_model_used", "")),
        "future_model_target": str(sentiment.get("future_model_target", "")),
        "finbert_ready": bool(sentiment.get("finbert_ready")),
        "finbert_readiness_reason": str(sentiment.get("finbert_readiness_reason", "")),
        "finbert_input_length": int(sentiment.get("finbert_input_length", 0) or 0),
        "finbert_model_available": bool(sentiment.get("finbert_model_available")),
        "finbert_label": str(sentiment.get("finbert_label", "")),
        "finbert_score": float(sentiment.get("finbert_score", 0.0) or 0.0),
        "finbert_confidence": float(sentiment.get("finbert_confidence", 0.0) or 0.0),
        "finbert_positive_probability": float(sentiment.get("finbert_positive_probability", 0.0) or 0.0),
        "finbert_negative_probability": float(sentiment.get("finbert_negative_probability", 0.0) or 0.0),
        "finbert_neutral_probability": float(sentiment.get("finbert_neutral_probability", 0.0) or 0.0),
        "exposure_observation_count": int(exposure["exposure_observation_count"]),
        "exposure_source_count": int(exposure["exposure_source_count"]),
        "exposure_weight": float(exposure["exposure_weight"]),
    }


def _fetch_story_sentiment_field_map(
    conn: sqlite3.Connection,
    story_keys: list[str],
) -> dict[str, dict[str, Any]]:
    normalized_story_keys = sorted({str(story_key).strip() for story_key in story_keys if str(story_key).strip()})
    if not normalized_story_keys:
        return {}

    placeholders = ", ".join("?" for _ in normalized_story_keys)
    rows = conn.execute(
        f"""
        WITH ranked_sentiment AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY story_key
                    ORDER BY
                        finbert_model_available DESC,
                        CASE bucket
                            WHEN 'stories' THEN 0
                            WHEN 'related_context' THEN 1
                            WHEN 'review_candidates' THEN 2
                            ELSE 3
                        END ASC,
                        signal_confidence DESC,
                        ticker_relevance_confidence DESC,
                        updated_at DESC,
                        id DESC
                ) AS story_rank
            FROM story_sentiment_snapshots
            WHERE story_key IN ({placeholders})
              AND sentiment_cache_version = ?
        )
        SELECT
            story_key,
            sentiment_cache_version,
            sentiment_label,
            sentiment_score,
            sentiment_confidence,
            raw_sentiment_confidence,
            signal_confidence,
            ticker_relevance_confidence,
            ticker_relevance_markers_json,
            sentiment_source_weight,
            market_impact_bias,
            sentiment_positive_markers_json,
            sentiment_negative_markers_json,
            sentiment_pipeline_stage,
            sentiment_model_used,
            future_model_target,
            finbert_ready,
            finbert_readiness_reason,
            finbert_input_length,
            finbert_model_available,
            finbert_label,
            finbert_score,
            finbert_confidence,
            finbert_positive_probability,
            finbert_negative_probability,
            finbert_neutral_probability
        FROM ranked_sentiment
        WHERE story_rank = 1
        """,
        (*normalized_story_keys, SENTIMENT_CACHE_VERSION),
    ).fetchall()

    return {
        str(row["story_key"] or ""): _sentiment_fields_from_row(row)
        for row in rows
        if str(row["story_key"] or "")
    }


def _resolve_published_fields(raw_value: Any, display_value: Any, published_at_value: Any, *, collected_at: str) -> dict[str, str]:
    display_text = str(display_value or "").strip()
    raw_text = str(raw_value or "").strip()
    published_at = str(published_at_value or "").strip()
    if display_text and published_at:
        return {
            "published": display_text,
            "published_raw": raw_text,
            "published_at": published_at,
        }

    normalized = normalize_published_fields(raw_text or display_text, collected_at=collected_at)
    return {
        "published": display_text or normalized["published_display"],
        "published_raw": raw_text or normalized["published_raw"],
        "published_at": published_at or normalized["published_at"],
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _article_reference_datetime(article: dict[str, Any]) -> datetime | None:
    for field in ("published_at", "last_seen_at", "collected_at", "first_seen_at"):
        dt = _parse_iso_datetime(article.get(field))
        if dt is not None:
            return dt
    return None


def _filter_articles_by_age(
    articles: list[dict[str, Any]],
    *,
    generated_at: str,
    max_age_hours: int,
) -> list[dict[str, Any]]:
    reference_dt = _parse_iso_datetime(generated_at) or datetime.now(timezone.utc)
    cutoff_seconds = max_age_hours * 3600
    filtered: list[dict[str, Any]] = []
    for article in articles:
        article_dt = _article_reference_datetime(article)
        if article_dt is None:
            continue
        age_seconds = (reference_dt - article_dt).total_seconds()
        if age_seconds < 0:
            age_seconds = 0
        if age_seconds <= cutoff_seconds:
            filtered.append(article)
    return filtered


def initialize_database(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS refresh_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                watchlist_file TEXT NOT NULL,
                rss_limit INTEGER NOT NULL,
                structured_limit INTEGER NOT NULL,
                include_seen INTEGER NOT NULL,
                pipeline_mode TEXT NOT NULL DEFAULT '',
                collection_elapsed_seconds REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ticker_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_run_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                company TEXT,
                sector TEXT,
                industry TEXT,
                raw_match_count INTEGER NOT NULL DEFAULT 0,
                keywords_json TEXT NOT NULL,
                failures_json TEXT NOT NULL,
                stats_json TEXT NOT NULL,
                source_usage_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS stories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                story_key TEXT NOT NULL UNIQUE,
                canonical_link TEXT,
                normalized_title_key TEXT,
                title TEXT NOT NULL,
                link TEXT,
                source_group TEXT,
                source_key TEXT,
                source_name TEXT,
                collection_method TEXT,
                published_raw TEXT,
                published_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS story_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_run_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                bucket TEXT NOT NULL,
                story_key TEXT NOT NULL,
                story_rank INTEGER,
                is_new INTEGER NOT NULL DEFAULT 0,
                relevance_score REAL,
                signal_strength REAL,
                event_type TEXT,
                event_importance_weight REAL,
                coverage_count INTEGER,
                coverage_sources_json TEXT NOT NULL,
                matched_identity_terms_json TEXT NOT NULL,
                matched_specific_terms_json TEXT NOT NULL,
                matched_generic_terms_json TEXT NOT NULL,
                summary TEXT,
                published_display TEXT,
                related_context_reason TEXT,
                review_candidate_reason TEXT,
                rejection_reasons_json TEXT NOT NULL,
                collected_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs(id) ON DELETE CASCADE,
                FOREIGN KEY (story_key) REFERENCES stories(story_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS source_health_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_run_id INTEGER NOT NULL,
                source_group TEXT NOT NULL,
                source_key TEXT NOT NULL,
                source_name TEXT NOT NULL,
                ok INTEGER NOT NULL,
                elapsed_seconds REAL NOT NULL,
                fetched_count INTEGER NOT NULL,
                matched_count INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                error TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS story_translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                translation_key TEXT NOT NULL UNIQUE,
                story_key TEXT NOT NULL DEFAULT '',
                source_language TEXT NOT NULL DEFAULT '',
                target_language TEXT NOT NULL DEFAULT 'en',
                original_title TEXT NOT NULL DEFAULT '',
                original_summary TEXT NOT NULL DEFAULT '',
                translated_title TEXT NOT NULL DEFAULT '',
                translated_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS story_sentiment_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT NOT NULL UNIQUE,
                story_key TEXT NOT NULL,
                ticker TEXT NOT NULL,
                bucket TEXT NOT NULL,
                refresh_run_id INTEGER NOT NULL,
                sentiment_cache_version TEXT NOT NULL DEFAULT '',
                sentiment_label TEXT NOT NULL DEFAULT '',
                sentiment_score REAL NOT NULL DEFAULT 0.0,
                sentiment_confidence REAL NOT NULL DEFAULT 0.0,
                raw_sentiment_confidence REAL NOT NULL DEFAULT 0.0,
                signal_confidence REAL NOT NULL DEFAULT 0.0,
                ticker_relevance_confidence REAL NOT NULL DEFAULT 0.0,
                ticker_relevance_markers_json TEXT NOT NULL DEFAULT '[]',
                sentiment_source_weight REAL NOT NULL DEFAULT 0.0,
                market_impact_bias TEXT NOT NULL DEFAULT '',
                sentiment_positive_markers_json TEXT NOT NULL DEFAULT '[]',
                sentiment_negative_markers_json TEXT NOT NULL DEFAULT '[]',
                sentiment_pipeline_stage TEXT NOT NULL DEFAULT '',
                sentiment_model_used TEXT NOT NULL DEFAULT '',
                future_model_target TEXT NOT NULL DEFAULT '',
                finbert_ready INTEGER NOT NULL DEFAULT 0,
                finbert_readiness_reason TEXT NOT NULL DEFAULT '',
                finbert_input_length INTEGER NOT NULL DEFAULT 0,
                finbert_model_available INTEGER NOT NULL DEFAULT 0,
                finbert_label TEXT NOT NULL DEFAULT '',
                finbert_score REAL NOT NULL DEFAULT 0.0,
                finbert_confidence REAL NOT NULL DEFAULT 0.0,
                finbert_positive_probability REAL NOT NULL DEFAULT 0.0,
                finbert_negative_probability REAL NOT NULL DEFAULT 0.0,
                finbert_neutral_probability REAL NOT NULL DEFAULT 0.0,
                exposure_observation_count INTEGER NOT NULL DEFAULT 0,
                exposure_source_count INTEGER NOT NULL DEFAULT 0,
                exposure_weight REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs(id) ON DELETE CASCADE,
                FOREIGN KEY (story_key) REFERENCES stories(story_key) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_ticker_runs_refresh_run_id
                ON ticker_runs(refresh_run_id);
            CREATE INDEX IF NOT EXISTS idx_story_observations_refresh_run_id
                ON story_observations(refresh_run_id);
            CREATE INDEX IF NOT EXISTS idx_story_observations_ticker_bucket
                ON story_observations(ticker, bucket);
            CREATE INDEX IF NOT EXISTS idx_stories_last_seen_at
                ON stories(last_seen_at);
            CREATE INDEX IF NOT EXISTS idx_source_health_refresh_run_id
                ON source_health_observations(refresh_run_id);
            CREATE INDEX IF NOT EXISTS idx_source_health_source_key
                ON source_health_observations(source_key);
            CREATE INDEX IF NOT EXISTS idx_story_translations_story_key
                ON story_translations(story_key);
            CREATE INDEX IF NOT EXISTS idx_story_sentiment_story_key
                ON story_sentiment_snapshots(story_key);
            CREATE INDEX IF NOT EXISTS idx_story_sentiment_ticker_bucket
                ON story_sentiment_snapshots(ticker, bucket);
            """
        )
        _ensure_column(conn, "refresh_runs", "pipeline_mode", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "refresh_runs", "collection_elapsed_seconds", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "ticker_runs", "sector", "TEXT")
        _ensure_column(conn, "ticker_runs", "industry", "TEXT")
        _ensure_column(conn, "ticker_runs", "raw_match_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "stories", "published_at", "TEXT")
        _ensure_column(conn, "story_observations", "collected_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_translations", "story_key", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_translations", "source_language", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_translations", "target_language", "TEXT NOT NULL DEFAULT 'en'")
        _ensure_column(conn, "story_translations", "original_title", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_translations", "original_summary", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_translations", "translated_title", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_sentiment_snapshots", "exposure_observation_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "story_sentiment_snapshots", "exposure_source_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "story_sentiment_snapshots", "exposure_weight", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "story_translations", "translated_summary", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_translations", "updated_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_sentiment_snapshots", "signal_confidence", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "story_sentiment_snapshots", "market_impact_bias", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_sentiment_snapshots", "future_model_target", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_ready", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_readiness_reason", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_input_length", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_model_available", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_label", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_score", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_confidence", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_positive_probability", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_negative_probability", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "story_sentiment_snapshots", "finbert_neutral_probability", "REAL NOT NULL DEFAULT 0.0")


def _upsert_story(conn: sqlite3.Connection, row: dict[str, Any], *, seen_at: str) -> str:
    story_key = _story_key(row)
    if not story_key:
        story_key = f"untitled::{row.get('title', '')}"

    existing = conn.execute(
        "SELECT story_key, first_seen_at FROM stories WHERE story_key = ?",
        (story_key,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO stories (
                story_key,
                canonical_link,
                normalized_title_key,
                title,
                link,
                source_group,
                source_key,
                source_name,
                collection_method,
                published_raw,
                published_at,
                first_seen_at,
                last_seen_at,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                story_key,
                row.get("canonical_link", ""),
                row.get("normalized_title_key", ""),
                row.get("title", ""),
                row.get("link", ""),
                row.get("source_group", ""),
                row.get("source_key", ""),
                row.get("source_name", ""),
                row.get("collection_method", ""),
                row.get("published_raw", row.get("published", "")),
                row.get("published_at", ""),
                seen_at,
                seen_at,
                _utc_now_iso(),
            ),
        )
    else:
        conn.execute(
            """
            UPDATE stories
            SET canonical_link = COALESCE(NULLIF(?, ''), canonical_link),
                normalized_title_key = COALESCE(NULLIF(?, ''), normalized_title_key),
                title = COALESCE(NULLIF(?, ''), title),
                link = COALESCE(NULLIF(?, ''), link),
                source_group = COALESCE(NULLIF(?, ''), source_group),
                source_key = COALESCE(NULLIF(?, ''), source_key),
                source_name = COALESCE(NULLIF(?, ''), source_name),
                collection_method = COALESCE(NULLIF(?, ''), collection_method),
                published_raw = COALESCE(NULLIF(?, ''), published_raw),
                published_at = COALESCE(NULLIF(?, ''), published_at),
                last_seen_at = ?
            WHERE story_key = ?
            """,
            (
                row.get("canonical_link", ""),
                row.get("normalized_title_key", ""),
                row.get("title", ""),
                row.get("link", ""),
                row.get("source_group", ""),
                row.get("source_key", ""),
                row.get("source_name", ""),
                row.get("collection_method", ""),
                row.get("published_raw", row.get("published", "")),
                row.get("published_at", ""),
                seen_at,
                story_key,
            ),
        )
    return story_key


def _upsert_story_sentiment_snapshot(
    conn: sqlite3.Connection,
    *,
    refresh_run_id: int,
    story_key: str,
    ticker: str,
    bucket: str,
    row: dict[str, Any],
    created_at: str,
    snapshot: dict[str, Any] | None = None,
) -> None:
    cache_key = _sentiment_cache_key(story_key=story_key, ticker=ticker, bucket=bucket)
    snapshot = snapshot or _sentiment_snapshot_payload(row)
    conn.execute(
        """
        INSERT INTO story_sentiment_snapshots (
            cache_key,
            story_key,
            ticker,
            bucket,
            refresh_run_id,
            sentiment_cache_version,
            sentiment_label,
            sentiment_score,
            sentiment_confidence,
            raw_sentiment_confidence,
            signal_confidence,
            ticker_relevance_confidence,
            ticker_relevance_markers_json,
            sentiment_source_weight,
            market_impact_bias,
            sentiment_positive_markers_json,
            sentiment_negative_markers_json,
            sentiment_pipeline_stage,
            sentiment_model_used,
            future_model_target,
            finbert_ready,
            finbert_readiness_reason,
            finbert_input_length,
            finbert_model_available,
            finbert_label,
            finbert_score,
            finbert_confidence,
            finbert_positive_probability,
            finbert_negative_probability,
            finbert_neutral_probability,
            exposure_observation_count,
            exposure_source_count,
            exposure_weight,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            refresh_run_id = excluded.refresh_run_id,
            sentiment_cache_version = excluded.sentiment_cache_version,
            sentiment_label = excluded.sentiment_label,
            sentiment_score = excluded.sentiment_score,
            sentiment_confidence = excluded.sentiment_confidence,
            raw_sentiment_confidence = excluded.raw_sentiment_confidence,
            signal_confidence = excluded.signal_confidence,
            ticker_relevance_confidence = excluded.ticker_relevance_confidence,
            ticker_relevance_markers_json = excluded.ticker_relevance_markers_json,
            sentiment_source_weight = excluded.sentiment_source_weight,
            market_impact_bias = excluded.market_impact_bias,
            sentiment_positive_markers_json = excluded.sentiment_positive_markers_json,
            sentiment_negative_markers_json = excluded.sentiment_negative_markers_json,
            sentiment_pipeline_stage = excluded.sentiment_pipeline_stage,
            sentiment_model_used = excluded.sentiment_model_used,
            future_model_target = excluded.future_model_target,
            finbert_ready = excluded.finbert_ready,
            finbert_readiness_reason = excluded.finbert_readiness_reason,
            finbert_input_length = excluded.finbert_input_length,
            finbert_model_available = excluded.finbert_model_available,
            finbert_label = excluded.finbert_label,
            finbert_score = excluded.finbert_score,
            finbert_confidence = excluded.finbert_confidence,
            finbert_positive_probability = excluded.finbert_positive_probability,
            finbert_negative_probability = excluded.finbert_negative_probability,
            finbert_neutral_probability = excluded.finbert_neutral_probability,
            exposure_observation_count = excluded.exposure_observation_count,
            exposure_source_count = excluded.exposure_source_count,
            exposure_weight = excluded.exposure_weight,
            updated_at = excluded.updated_at
        """,
        (
            cache_key,
            story_key,
            ticker,
            bucket,
            refresh_run_id,
            SENTIMENT_CACHE_VERSION,
            snapshot["sentiment_label"],
            snapshot["sentiment_score"],
            snapshot["sentiment_confidence"],
            snapshot["raw_sentiment_confidence"],
            snapshot["signal_confidence"],
            snapshot["ticker_relevance_confidence"],
            _json_dumps(snapshot["ticker_relevance_markers"]),
            snapshot["sentiment_source_weight"],
            snapshot["market_impact_bias"],
            _json_dumps(snapshot["sentiment_positive_markers"]),
            _json_dumps(snapshot["sentiment_negative_markers"]),
            snapshot["sentiment_pipeline_stage"],
            snapshot["sentiment_model_used"],
            snapshot["future_model_target"],
            1 if snapshot["finbert_ready"] else 0,
            snapshot["finbert_readiness_reason"],
            snapshot["finbert_input_length"],
            1 if snapshot["finbert_model_available"] else 0,
            snapshot["finbert_label"],
            snapshot["finbert_score"],
            snapshot["finbert_confidence"],
            snapshot["finbert_positive_probability"],
            snapshot["finbert_negative_probability"],
            snapshot["finbert_neutral_probability"],
            snapshot["exposure_observation_count"],
            snapshot["exposure_source_count"],
            snapshot["exposure_weight"],
            created_at,
            created_at,
        ),
    )


def persist_watchlist_snapshot(db_path: str, snapshot: dict[str, Any]) -> int:
    initialize_database(db_path)
    generated_at = str(snapshot.get("generated_at", "")) or _utc_now_iso()
    created_at = _utc_now_iso()

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO refresh_runs (
                generated_at,
                watchlist_file,
                rss_limit,
                structured_limit,
                include_seen,
                pipeline_mode,
                collection_elapsed_seconds,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generated_at,
                str(snapshot.get("watchlist_file", "")),
                int(snapshot.get("rss_limit", 0) or 0),
                int(snapshot.get("structured_limit", 0) or 0),
                1 if snapshot.get("include_seen") else 0,
                str(snapshot.get("pipeline_mode", "")),
                float(snapshot.get("collection_elapsed_seconds", 0.0) or 0.0),
                created_at,
            ),
        )
        refresh_run_id = int(cursor.lastrowid)

        for ticker_payload in snapshot.get("tickers", []):
            conn.execute(
                """
                INSERT INTO ticker_runs (
                    refresh_run_id,
                    ticker,
                    company,
                    sector,
                    industry,
                    raw_match_count,
                    keywords_json,
                    failures_json,
                    stats_json,
                    source_usage_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refresh_run_id,
                    str(ticker_payload.get("ticker", "")),
                    str(ticker_payload.get("company", "")),
                    str(ticker_payload.get("sector", "")),
                    str(ticker_payload.get("industry", "")),
                    int(ticker_payload.get("raw_match_count", 0) or 0),
                    json.dumps(ticker_payload.get("keywords", [])),
                    json.dumps(ticker_payload.get("failures", [])),
                    json.dumps(ticker_payload.get("stats", {})),
                    json.dumps(ticker_payload.get("source_usage", {})),
                    created_at,
                ),
            )

            ticker = str(ticker_payload.get("ticker", ""))
            for bucket in ("stories", "related_context", "review_candidates", "rejections"):
                for rank, row in enumerate(ticker_payload.get(bucket, []), start=1):
                    story_key = _upsert_story(conn, row, seen_at=generated_at)
                    conn.execute(
                        """
                        INSERT INTO story_observations (
                            refresh_run_id,
                            ticker,
                            bucket,
                            story_key,
                            story_rank,
                            is_new,
                            relevance_score,
                            signal_strength,
                            event_type,
                            event_importance_weight,
                            coverage_count,
                            coverage_sources_json,
                            matched_identity_terms_json,
                            matched_specific_terms_json,
                            matched_generic_terms_json,
                            summary,
                            published_display,
                            related_context_reason,
                            review_candidate_reason,
                            rejection_reasons_json,
                            collected_at,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            refresh_run_id,
                            ticker,
                            bucket,
                            story_key,
                            rank,
                            1 if row.get("is_new") else 0,
                            row.get("relevance_score"),
                            row.get("signal_strength"),
                            row.get("event_type", ""),
                            row.get("event_importance_weight"),
                            row.get("coverage_count"),
                            json.dumps(row.get("coverage_sources", [])),
                            json.dumps(row.get("matched_identity_terms", [])),
                            json.dumps(row.get("matched_specific_terms", [])),
                            json.dumps(row.get("matched_generic_terms", [])),
                            row.get("summary", ""),
                            row.get("published_display", row.get("published", "")),
                            row.get("related_context_reason", ""),
                            row.get("review_candidate_reason", ""),
                            json.dumps(row.get("rejection_reasons", [])),
                            row.get("collected_at", generated_at),
                            created_at,
                        ),
                    )
                    _upsert_story_sentiment_snapshot(
                        conn,
                        refresh_run_id=refresh_run_id,
                        story_key=story_key,
                        ticker=ticker,
                        bucket=bucket,
                        row={
                            **row,
                            "ticker": ticker,
                            "company": str(ticker_payload.get("company", "")),
                            "sector": str(ticker_payload.get("sector", "")),
                            "industry": str(ticker_payload.get("industry", "")),
                        },
                        created_at=created_at,
                    )

        for row in snapshot.get("source_health", []):
            conn.execute(
                """
                INSERT INTO source_health_observations (
                    refresh_run_id,
                    source_group,
                    source_key,
                    source_name,
                    ok,
                    elapsed_seconds,
                    fetched_count,
                    matched_count,
                    ticker,
                    error,
                    collected_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refresh_run_id,
                    str(row.get("source_group", "")),
                    str(row.get("source_key", "")),
                    str(row.get("source_name", "")),
                    1 if row.get("ok") else 0,
                    float(row.get("elapsed_seconds", 0.0) or 0.0),
                    int(row.get("fetched_count", 0) or 0),
                    int(row.get("matched_count", 0) or 0),
                    str(row.get("ticker", "")),
                    str(row.get("error", "")),
                    str(row.get("collected_at", "")),
                    created_at,
                ),
            )

        conn.commit()
    return refresh_run_id


def fetch_latest_watchlist_snapshot(db_path: str) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {}

    initialize_database(db_path)
    with _connect(db_path) as conn:
        refresh_run = conn.execute(
            """
            SELECT id, generated_at, watchlist_file, rss_limit, structured_limit,
                   include_seen, pipeline_mode, collection_elapsed_seconds
            FROM refresh_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if refresh_run is None:
            return {}

        refresh_run_id = int(refresh_run["id"])
        ticker_rows = conn.execute(
            """
            SELECT ticker, company, sector, industry, raw_match_count, keywords_json,
                   failures_json, stats_json, source_usage_json
            FROM ticker_runs
            WHERE refresh_run_id = ?
            ORDER BY id ASC
            """,
            (refresh_run_id,),
        ).fetchall()

        tickers: dict[str, dict[str, Any]] = {}
        ticker_order: list[str] = []
        for row in ticker_rows:
            ticker = str(row["ticker"])
            ticker_order.append(ticker)
            tickers[ticker] = {
                "ticker": ticker,
                "company": str(row["company"] or ""),
                "sector": str(row["sector"] or ""),
                "industry": str(row["industry"] or ""),
                "raw_match_count": int(row["raw_match_count"] or 0),
                "keywords": _json_loads(row["keywords_json"], []),
                "failures": _json_loads(row["failures_json"], []),
                "stats": _json_loads(row["stats_json"], {}),
                "source_usage": _json_loads(row["source_usage_json"], {}),
                "stories": [],
                "related_context": [],
                "review_candidates": [],
                "rejections": [],
            }

        observation_rows = conn.execute(
            """
            SELECT so.ticker, so.bucket, so.story_rank, so.is_new, so.relevance_score,
                   so.signal_strength, so.event_type, so.event_importance_weight,
                   so.coverage_count, so.coverage_sources_json,
                   so.matched_identity_terms_json, so.matched_specific_terms_json,
                   so.matched_generic_terms_json, so.summary, so.published_display,
                   so.related_context_reason, so.review_candidate_reason,
                   so.rejection_reasons_json, so.collected_at,
                   s.story_key, s.canonical_link, s.normalized_title_key, s.title,
                   s.link, s.source_group, s.source_key, s.source_name,
                   s.collection_method, s.published_raw, s.published_at, s.first_seen_at, s.last_seen_at
            FROM story_observations AS so
            JOIN stories AS s
              ON s.story_key = so.story_key
            WHERE so.refresh_run_id = ?
            ORDER BY so.ticker ASC, so.bucket ASC, so.story_rank ASC, so.id ASC
            """,
            (refresh_run_id,),
        ).fetchall()

        for row in observation_rows:
            collected_at = str(row["collected_at"] or refresh_run["generated_at"] or "")
            published_fields = _resolve_published_fields(
                row["published_raw"],
                row["published_display"],
                row["published_at"],
                collected_at=collected_at,
            )
            ticker = str(row["ticker"])
            if ticker not in tickers:
                continue
            story_row = {
                "story_key": str(row["story_key"] or ""),
                "canonical_link": str(row["canonical_link"] or ""),
                "normalized_title_key": str(row["normalized_title_key"] or ""),
                "title": str(row["title"] or ""),
                "link": str(row["link"] or ""),
                "source_group": str(row["source_group"] or ""),
                "source_key": str(row["source_key"] or ""),
                "source_name": str(row["source_name"] or ""),
                "collection_method": str(row["collection_method"] or ""),
                "published": published_fields["published"],
                "published_raw": published_fields["published_raw"],
                "published_at": published_fields["published_at"],
                "first_seen_at": str(row["first_seen_at"] or ""),
                "last_seen_at": str(row["last_seen_at"] or ""),
                "is_new": bool(row["is_new"]),
                "relevance_score": row["relevance_score"],
                "signal_strength": row["signal_strength"],
                "event_type": str(row["event_type"] or ""),
                "event_importance_weight": row["event_importance_weight"],
                "coverage_count": int(row["coverage_count"] or 0),
                "coverage_sources": _json_loads(row["coverage_sources_json"], []),
                "matched_identity_terms": _json_loads(row["matched_identity_terms_json"], []),
                "matched_specific_terms": _json_loads(row["matched_specific_terms_json"], []),
                "matched_generic_terms": _json_loads(row["matched_generic_terms_json"], []),
                "summary": str(row["summary"] or ""),
                "related_context_reason": str(row["related_context_reason"] or ""),
                "review_candidate_reason": str(row["review_candidate_reason"] or ""),
                "rejection_reasons": _json_loads(row["rejection_reasons_json"], []),
                "collected_at": collected_at,
            }
            tickers[ticker].setdefault(str(row["bucket"]), []).append(story_row)

        source_health = [
            {
                "source_group": str(row["source_group"] or ""),
                "source_key": str(row["source_key"] or ""),
                "source_name": str(row["source_name"] or ""),
                "ok": bool(row["ok"]),
                "elapsed_seconds": float(row["elapsed_seconds"] or 0.0),
                "fetched_count": int(row["fetched_count"] or 0),
                "matched_count": int(row["matched_count"] or 0),
                "ticker": str(row["ticker"] or ""),
                "error": str(row["error"] or ""),
                "collected_at": str(row["collected_at"] or refresh_run["generated_at"] or ""),
            }
            for row in conn.execute(
                """
                SELECT source_group, source_key, source_name, ok, elapsed_seconds,
                       fetched_count, matched_count, ticker, error, collected_at
                FROM source_health_observations
                WHERE refresh_run_id = ?
                ORDER BY id ASC
                """,
                (refresh_run_id,),
            ).fetchall()
        ]

        return {
            "generated_at": str(refresh_run["generated_at"] or ""),
            "watchlist_file": str(refresh_run["watchlist_file"] or ""),
            "rss_limit": int(refresh_run["rss_limit"] or 0),
            "structured_limit": int(refresh_run["structured_limit"] or 0),
            "include_seen": bool(refresh_run["include_seen"]),
            "pipeline_mode": str(refresh_run["pipeline_mode"] or ""),
            "collection_elapsed_seconds": float(refresh_run["collection_elapsed_seconds"] or 0.0),
            "source_health": source_health,
            "failures": [],
            "tickers": [tickers[ticker] for ticker in ticker_order],
        }


def fetch_latest_ticker_snapshot(db_path: str, ticker: str) -> dict[str, Any]:
    path = Path(db_path)
    normalized_ticker = str(ticker).strip().upper()
    if not path.exists() or not normalized_ticker:
        return {}

    initialize_database(db_path)
    with _connect(db_path) as conn:
        refresh_run = conn.execute(
            """
            SELECT id, generated_at, watchlist_file, rss_limit, structured_limit,
                   include_seen, pipeline_mode, collection_elapsed_seconds
            FROM refresh_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if refresh_run is None:
            return {}

        ticker_row = conn.execute(
            """
            SELECT ticker, company, sector, industry, raw_match_count, keywords_json,
                   failures_json, stats_json, source_usage_json
            FROM ticker_runs
            WHERE refresh_run_id = ? AND ticker = ?
            LIMIT 1
            """,
            (int(refresh_run["id"]), normalized_ticker),
        ).fetchone()
        if ticker_row is None:
            return {}

        ticker_payload: dict[str, Any] = {
            "ticker": str(ticker_row["ticker"]),
            "company": str(ticker_row["company"] or ""),
            "sector": str(ticker_row["sector"] or ""),
            "industry": str(ticker_row["industry"] or ""),
            "raw_match_count": int(ticker_row["raw_match_count"] or 0),
            "keywords": _json_loads(ticker_row["keywords_json"], []),
            "failures": _json_loads(ticker_row["failures_json"], []),
            "stats": _json_loads(ticker_row["stats_json"], {}),
            "source_usage": _json_loads(ticker_row["source_usage_json"], {}),
            "stories": [],
            "related_context": [],
            "review_candidates": [],
            "rejections": [],
        }

        observation_rows = conn.execute(
            """
            SELECT so.bucket, so.story_rank, so.is_new, so.relevance_score,
                   so.signal_strength, so.event_type, so.event_importance_weight,
                   so.coverage_count, so.coverage_sources_json,
                   so.matched_identity_terms_json, so.matched_specific_terms_json,
                   so.matched_generic_terms_json, so.summary, so.published_display,
                   so.related_context_reason, so.review_candidate_reason,
                   so.rejection_reasons_json, so.collected_at,
                   s.story_key, s.canonical_link, s.normalized_title_key, s.title,
                   s.link, s.source_group, s.source_key, s.source_name,
                   s.collection_method, s.published_raw, s.published_at, s.first_seen_at, s.last_seen_at
            FROM story_observations AS so
            JOIN stories AS s
              ON s.story_key = so.story_key
            WHERE so.refresh_run_id = ? AND so.ticker = ?
            ORDER BY so.bucket ASC, so.story_rank ASC, so.id ASC
            """,
            (int(refresh_run["id"]), normalized_ticker),
        ).fetchall()

        for row in observation_rows:
            collected_at = str(row["collected_at"] or refresh_run["generated_at"] or "")
            published_fields = _resolve_published_fields(
                row["published_raw"],
                row["published_display"],
                row["published_at"],
                collected_at=collected_at,
            )
            story_row = {
                "story_key": str(row["story_key"] or ""),
                "canonical_link": str(row["canonical_link"] or ""),
                "normalized_title_key": str(row["normalized_title_key"] or ""),
                "title": str(row["title"] or ""),
                "link": str(row["link"] or ""),
                "source_group": str(row["source_group"] or ""),
                "source_key": str(row["source_key"] or ""),
                "source_name": str(row["source_name"] or ""),
                "collection_method": str(row["collection_method"] or ""),
                "published": published_fields["published"],
                "published_raw": published_fields["published_raw"],
                "published_at": published_fields["published_at"],
                "first_seen_at": str(row["first_seen_at"] or ""),
                "last_seen_at": str(row["last_seen_at"] or ""),
                "is_new": bool(row["is_new"]),
                "relevance_score": row["relevance_score"],
                "signal_strength": row["signal_strength"],
                "event_type": str(row["event_type"] or ""),
                "event_importance_weight": row["event_importance_weight"],
                "coverage_count": int(row["coverage_count"] or 0),
                "coverage_sources": _json_loads(row["coverage_sources_json"], []),
                "matched_identity_terms": _json_loads(row["matched_identity_terms_json"], []),
                "matched_specific_terms": _json_loads(row["matched_specific_terms_json"], []),
                "matched_generic_terms": _json_loads(row["matched_generic_terms_json"], []),
                "summary": str(row["summary"] or ""),
                "related_context_reason": str(row["related_context_reason"] or ""),
                "review_candidate_reason": str(row["review_candidate_reason"] or ""),
                "rejection_reasons": _json_loads(row["rejection_reasons_json"], []),
                "collected_at": collected_at,
            }
            ticker_payload.setdefault(str(row["bucket"]), []).append(story_row)

        source_health = [
            {
                "source_group": str(row["source_group"] or ""),
                "source_key": str(row["source_key"] or ""),
                "source_name": str(row["source_name"] or ""),
                "ok": bool(row["ok"]),
                "elapsed_seconds": float(row["elapsed_seconds"] or 0.0),
                "fetched_count": int(row["fetched_count"] or 0),
                "matched_count": int(row["matched_count"] or 0),
                "ticker": str(row["ticker"] or ""),
                "error": str(row["error"] or ""),
                "collected_at": str(row["collected_at"] or ""),
            }
            for row in conn.execute(
                """
                SELECT source_group, source_key, source_name, ok, elapsed_seconds,
                       fetched_count, matched_count, ticker, error, collected_at
                FROM source_health_observations
                WHERE refresh_run_id = ? AND (ticker = ? OR ticker = '')
                ORDER BY id ASC
                """,
                (int(refresh_run["id"]), normalized_ticker),
            ).fetchall()
        ]

        return {
            "generated_at": str(refresh_run["generated_at"] or ""),
            "watchlist_file": str(refresh_run["watchlist_file"] or ""),
            "rss_limit": int(refresh_run["rss_limit"] or 0),
            "structured_limit": int(refresh_run["structured_limit"] or 0),
            "include_seen": bool(refresh_run["include_seen"]),
            "pipeline_mode": str(refresh_run["pipeline_mode"] or ""),
            "collection_elapsed_seconds": float(refresh_run["collection_elapsed_seconds"] or 0.0),
            "source_health": source_health,
            "ticker": ticker_payload,
        }


def fetch_latest_ticker_universe(db_path: str) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {}

    initialize_database(db_path)
    with _connect(db_path) as conn:
        refresh_run = conn.execute(
            """
            SELECT id, generated_at, pipeline_mode, collection_elapsed_seconds
            FROM refresh_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if refresh_run is None:
            return {}

        rows = conn.execute(
            """
            SELECT ticker, company, sector, industry, raw_match_count, stats_json
            FROM ticker_runs
            WHERE refresh_run_id = ?
            ORDER BY ticker ASC
            """,
            (int(refresh_run["id"]),),
        ).fetchall()

        tickers = []
        for row in rows:
            stats = _json_loads(row["stats_json"], {})
            tickers.append(
                {
                    "ticker": str(row["ticker"] or ""),
                    "company": str(row["company"] or ""),
                    "sector": str(row["sector"] or ""),
                    "industry": str(row["industry"] or ""),
                    "raw_match_count": int(row["raw_match_count"] or 0),
                    "primary_count": int(stats.get("clustered_story_count", 0) or 0),
                    "related_count": int(stats.get("related_context_rows", 0) or 0),
                    "review_count": int(stats.get("review_candidate_rows", 0) or 0),
                    "rejected_count": int(stats.get("rejected_rows", 0) or 0),
                }
            )

        return {
            "generated_at": str(refresh_run["generated_at"] or ""),
            "pipeline_mode": str(refresh_run["pipeline_mode"] or ""),
            "collection_elapsed_seconds": float(refresh_run["collection_elapsed_seconds"] or 0.0),
            "tickers": tickers,
        }


def fetch_cached_story_sentiment(
    db_path: str,
    *,
    story_key: str = "",
    ticker: str = "",
    bucket: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []

    initialize_database(db_path)
    where_clauses: list[str] = []
    params: list[Any] = []

    normalized_story_key = str(story_key).strip()
    normalized_ticker = str(ticker).strip().upper()
    normalized_bucket = str(bucket).strip()

    if normalized_story_key:
        where_clauses.append("story_key = ?")
        params.append(normalized_story_key)
    if normalized_ticker:
        where_clauses.append("ticker = ?")
        params.append(normalized_ticker)
    if normalized_bucket:
        where_clauses.append("bucket = ?")
        params.append(normalized_bucket)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(max(1, int(limit or 100)))

    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT
                story_key,
                ticker,
                bucket,
                refresh_run_id,
                sentiment_cache_version,
                sentiment_label,
                sentiment_score,
                sentiment_confidence,
                raw_sentiment_confidence,
                signal_confidence,
                ticker_relevance_confidence,
                ticker_relevance_markers_json,
                sentiment_source_weight,
                market_impact_bias,
                sentiment_positive_markers_json,
                sentiment_negative_markers_json,
                sentiment_pipeline_stage,
                sentiment_model_used,
                future_model_target,
                finbert_ready,
                finbert_readiness_reason,
                finbert_input_length,
                finbert_model_available,
                finbert_label,
                finbert_score,
                finbert_confidence,
                finbert_positive_probability,
                finbert_negative_probability,
                finbert_neutral_probability,
                exposure_observation_count,
                exposure_source_count,
                exposure_weight,
                created_at,
                updated_at
            FROM story_sentiment_snapshots
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    return [
        {
            "story_key": str(row["story_key"] or ""),
            "ticker": str(row["ticker"] or ""),
            "bucket": str(row["bucket"] or ""),
            "refresh_run_id": int(row["refresh_run_id"] or 0),
            **_sentiment_fields_from_row(row),
            "exposure_observation_count": int(row["exposure_observation_count"] or 0),
            "exposure_source_count": int(row["exposure_source_count"] or 0),
            "exposure_weight": float(row["exposure_weight"] or 0.0),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]


def refresh_story_sentiment_snapshots(
    db_path: str,
    *,
    refresh_run_id: int | None = None,
    limit: int = 250,
    dry_run: bool = False,
    enrich_missing_text: bool = False,
    translate_non_english: bool = False,
    force_finbert_ready: bool = False,
) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {
            "ok": False,
            "message": f"SQLite database not found: {db_path}",
            "selected_refresh_run_id": 0,
            "candidate_count": 0,
            "rescored_count": 0,
            "written_count": 0,
            "dry_run": dry_run,
            "enriched_text_count": 0,
            "translated_text_count": 0,
            "remaining_blockers": {},
            "sentiment_runtime": sentiment_runtime_status(),
        }

    initialize_database(db_path)
    max_rows = max(1, int(limit or 250))
    now_iso = _utc_now_iso()
    with _connect(db_path) as conn:
        selected_refresh_run_id = int(refresh_run_id or 0)
        if selected_refresh_run_id <= 0:
            latest = conn.execute(
                """
                SELECT id
                FROM refresh_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            selected_refresh_run_id = int(latest["id"] or 0) if latest else 0
        if selected_refresh_run_id <= 0:
            return {
                "ok": False,
                "message": "No refresh run is available to rescore.",
                "selected_refresh_run_id": 0,
                "candidate_count": 0,
                "rescored_count": 0,
                "written_count": 0,
                "dry_run": dry_run,
                "enriched_text_count": 0,
                "translated_text_count": 0,
                "remaining_blockers": {},
                "sentiment_runtime": sentiment_runtime_status(),
            }

        rows = conn.execute(
            """
            SELECT
                so.refresh_run_id,
                so.ticker,
                so.bucket,
                so.story_key,
                so.signal_strength,
                so.event_type,
                so.coverage_count,
                so.coverage_sources_json,
                so.summary,
                so.collected_at,
                so.published_display,
                tr.company,
                tr.sector,
                tr.industry,
                s.title,
                s.link,
                s.canonical_link,
                s.normalized_title_key,
                s.source_group,
                s.source_key,
                s.source_name,
                s.collection_method,
                s.published_raw,
                s.published_at,
                s.first_seen_at,
                s.last_seen_at,
                ss.finbert_model_available AS cached_finbert_model_available,
                ss.finbert_label AS cached_finbert_label,
                ss.finbert_score AS cached_finbert_score,
                ss.finbert_confidence AS cached_finbert_confidence,
                ss.finbert_positive_probability AS cached_finbert_positive_probability,
                ss.finbert_negative_probability AS cached_finbert_negative_probability,
                ss.finbert_neutral_probability AS cached_finbert_neutral_probability
            FROM story_observations AS so
            JOIN stories AS s
              ON s.story_key = so.story_key
            LEFT JOIN ticker_runs AS tr
              ON tr.refresh_run_id = so.refresh_run_id
             AND tr.ticker = so.ticker
            LEFT JOIN story_sentiment_snapshots AS ss
              ON ss.story_key = so.story_key
             AND ss.ticker = so.ticker
             AND ss.bucket = so.bucket
             AND ss.sentiment_cache_version = ?
            WHERE so.refresh_run_id = ?
              AND so.bucket IN ('stories', 'related_context', 'review_candidates')
            ORDER BY
                COALESCE(so.signal_strength, 0) DESC,
                s.last_seen_at DESC,
                so.story_rank ASC,
                so.id ASC
            LIMIT ?
            """,
            (SENTIMENT_CACHE_VERSION, selected_refresh_run_id, max_rows),
        ).fetchall()

        rescored_count = 0
        finbert_applied_count = 0
        finbert_reused_count = 0
        finbert_inference_count = 0
        finbert_ready_count = 0
        enriched_text_count = 0
        translated_text_count = 0
        remaining_blockers: dict[str, int] = {}
        label_counts: dict[str, int] = {}
        for row in rows:
            source_family, source_quality_tier = _source_descriptor(row["source_key"], row["source_group"])
            ticker = str(row["ticker"] or "").strip().upper()
            bucket = str(row["bucket"] or "")
            story_key_text = str(row["story_key"] or "")
            scoring_row = {
                "story_key": story_key_text,
                "ticker": ticker,
                "company": str(row["company"] or ""),
                "sector": str(row["sector"] or ""),
                "industry": str(row["industry"] or ""),
                "bucket": bucket,
                "title": str(row["title"] or ""),
                "summary": str(row["summary"] or ""),
                "link": str(row["link"] or ""),
                "canonical_link": str(row["canonical_link"] or ""),
                "normalized_title_key": str(row["normalized_title_key"] or ""),
                "source_group": str(row["source_group"] or ""),
                "source_key": str(row["source_key"] or ""),
                "source_name": str(row["source_name"] or ""),
                "source_family": source_family,
                "source_quality_tier": source_quality_tier,
                "collection_method": str(row["collection_method"] or ""),
                "published_raw": str(row["published_raw"] or row["published_display"] or ""),
                "published_at": str(row["published_at"] or ""),
                "first_seen_at": str(row["first_seen_at"] or ""),
                "last_seen_at": str(row["last_seen_at"] or ""),
                "collected_at": str(row["collected_at"] or ""),
                "event_type": str(row["event_type"] or ""),
                "signal_strength": float(row["signal_strength"] or 0.0),
                "coverage_count": int(row["coverage_count"] or 0),
                "coverage_sources": _json_loads(row["coverage_sources_json"], []),
                "matched_tickers": [ticker] if ticker else [],
                "matched_ticker_count": 1 if ticker else 0,
                "cached_finbert_model_available": bool(row["cached_finbert_model_available"]),
                "cached_finbert_label": str(row["cached_finbert_label"] or ""),
                "cached_finbert_score": float(row["cached_finbert_score"] or 0.0),
                "cached_finbert_confidence": float(row["cached_finbert_confidence"] or 0.0),
                "cached_finbert_positive_probability": float(
                    row["cached_finbert_positive_probability"] or 0.0
                ),
                "cached_finbert_negative_probability": float(
                    row["cached_finbert_negative_probability"] or 0.0
                ),
                "cached_finbert_neutral_probability": float(
                    row["cached_finbert_neutral_probability"] or 0.0
                ),
            }
            if enrich_missing_text or translate_non_english:
                from src.ingestion.article_enrichment import enrich_article_for_finbert

                scoring_row, enrichment_report = enrich_article_for_finbert(
                    scoring_row,
                    fetch_missing_text=enrich_missing_text,
                    translate_non_english=translate_non_english,
                )
                if enrichment_report.get("fetched_article_text"):
                    enriched_text_count += 1
                if enrichment_report.get("translated_text"):
                    translated_text_count += 1
            sentiment = _sentiment_snapshot_payload(scoring_row, force_finbert_ready=force_finbert_ready)
            label = str(sentiment.get("sentiment_label", "neutral") or "neutral")
            label_counts[label] = label_counts.get(label, 0) + 1
            if sentiment.get("finbert_ready"):
                finbert_ready_count += 1
            else:
                reason = str(sentiment.get("finbert_readiness_reason", "") or "not_ready")
                remaining_blockers[reason] = remaining_blockers.get(reason, 0) + 1
            if sentiment.get("finbert_model_available"):
                finbert_applied_count += 1
                if bool(scoring_row.get("cached_finbert_model_available")):
                    finbert_reused_count += 1
                else:
                    finbert_inference_count += 1
            rescored_count += 1

            if not dry_run:
                _upsert_story_sentiment_snapshot(
                    conn,
                    refresh_run_id=selected_refresh_run_id,
                    story_key=story_key_text,
                    ticker=ticker,
                    bucket=bucket,
                    row=scoring_row,
                    created_at=now_iso,
                    snapshot=sentiment,
                )

        return {
            "ok": True,
            "message": "Sentiment snapshots rescored.",
            "selected_refresh_run_id": selected_refresh_run_id,
            "candidate_count": len(rows),
            "rescored_count": rescored_count,
            "written_count": 0 if dry_run else rescored_count,
            "dry_run": dry_run,
            "label_counts": label_counts,
            "finbert_ready_count": finbert_ready_count,
            "finbert_applied_count": finbert_applied_count,
            "finbert_reused_count": finbert_reused_count,
            "finbert_inference_count": finbert_inference_count,
            "enriched_text_count": enriched_text_count,
            "translated_text_count": translated_text_count,
            "remaining_blockers": remaining_blockers,
            "sentiment_runtime": sentiment_runtime_status(),
        }


def fetch_latest_market_article_pool(db_path: str) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {}

    initialize_database(db_path)
    with _connect(db_path) as conn:
        refresh_runs = conn.execute(
            """
            SELECT id, generated_at, pipeline_mode, collection_elapsed_seconds
            FROM refresh_runs
            ORDER BY id DESC
            LIMIT 25
            """
        ).fetchall()
        if not refresh_runs:
            return {}

        latest_payload: dict[str, Any] = {}
        latest_fallback_payload: dict[str, Any] = {}
        for index, refresh_run in enumerate(refresh_runs):
            rows = conn.execute(
                """
                SELECT
                    s.story_key,
                    s.canonical_link,
                    s.normalized_title_key,
                    s.title,
                    s.link,
                    s.source_group,
                    s.source_key,
                    s.source_name,
                    s.collection_method,
                    s.published_raw,
                    s.published_at,
                    s.first_seen_at,
                    s.last_seen_at,
                    MAX(COALESCE(so.published_display, '')) AS published_display,
                    MAX(COALESCE(so.collected_at, '')) AS last_collected_at,
                    MAX(COALESCE(so.is_new, 0)) AS any_is_new,
                    MAX(COALESCE(so.signal_strength, 0)) AS max_signal_strength,
                    MAX(COALESCE(so.coverage_count, 0)) AS max_coverage_count,
                    MAX(COALESCE(so.coverage_sources_json, '[]')) AS coverage_sources_json,
                    GROUP_CONCAT(DISTINCT so.ticker) AS matched_tickers_csv,
                    GROUP_CONCAT(DISTINCT so.bucket) AS buckets_csv,
                    GROUP_CONCAT(DISTINCT so.event_type) AS event_types_csv
                FROM story_observations AS so
                JOIN stories AS s
                  ON s.story_key = so.story_key
                WHERE so.refresh_run_id = ?
                GROUP BY
                    s.story_key, s.canonical_link, s.normalized_title_key, s.title, s.link,
                    s.source_group, s.source_key, s.source_name, s.collection_method,
                    s.published_raw, s.published_at, s.first_seen_at, s.last_seen_at
                ORDER BY max_signal_strength DESC, s.last_seen_at DESC, s.title ASC
                """,
                (int(refresh_run["id"]),),
            ).fetchall()

            sentiment_by_story_key = _fetch_story_sentiment_field_map(
                conn,
                [str(row["story_key"] or "") for row in rows],
            )
            articles = []
            for row in rows:
                collected_at = str(row["last_collected_at"] or refresh_run["generated_at"] or "")
                published_fields = _resolve_published_fields(
                    row["published_raw"],
                    row["published_display"],
                    row["published_at"],
                    collected_at=collected_at,
                )
                exposure = _compute_story_exposure_metrics(
                    coverage_count=row["max_coverage_count"],
                    coverage_sources=_json_loads(row["coverage_sources_json"], []),
                )
                matched_tickers = sorted(
                    [value for value in str(row["matched_tickers_csv"] or "").split(",") if value]
                )
                buckets = sorted([value for value in str(row["buckets_csv"] or "").split(",") if value])
                event_types = sorted([value for value in str(row["event_types_csv"] or "").split(",") if value])
                story_key = str(row["story_key"] or "")
                articles.append(
                    {
                        "story_key": story_key,
                        "canonical_link": str(row["canonical_link"] or ""),
                        "normalized_title_key": str(row["normalized_title_key"] or ""),
                        "title": str(row["title"] or ""),
                        "link": str(row["link"] or ""),
                        "source_group": str(row["source_group"] or ""),
                        "source_key": str(row["source_key"] or ""),
                        "source_name": str(row["source_name"] or ""),
                        "collection_method": str(row["collection_method"] or ""),
                        "published_raw": published_fields["published_raw"],
                        "published_display": published_fields["published"],
                        "published_at": published_fields["published_at"],
                        "first_seen_at": str(row["first_seen_at"] or ""),
                        "last_seen_at": str(row["last_seen_at"] or ""),
                        "collected_at": collected_at,
                        "is_new": bool(row["any_is_new"]),
                        "signal_strength": float(row["max_signal_strength"] or 0.0),
                        "coverage_count": int(row["max_coverage_count"] or 0),
                        "coverage_sources": exposure["exposure_sources"],
                        "exposure_observation_count": int(exposure["exposure_observation_count"]),
                        "exposure_source_count": int(exposure["exposure_source_count"]),
                        "exposure_weight": float(exposure["exposure_weight"]),
                        "matched_tickers": matched_tickers,
                        "matched_ticker_count": len(matched_tickers),
                        "buckets": buckets,
                        "event_types": event_types,
                        **sentiment_by_story_key.get(story_key, {}),
                    }
                )

            recent_articles = _filter_articles_by_age(
                articles,
                generated_at=str(refresh_run["generated_at"] or ""),
                max_age_hours=LIVE_FEED_RECENT_HOURS,
            )
            fallback_articles = _filter_articles_by_age(
                articles,
                generated_at=str(refresh_run["generated_at"] or ""),
                max_age_hours=LIVE_FEED_FALLBACK_HOURS,
            )

            payload = {
                "generated_at": str(refresh_run["generated_at"] or ""),
                "pipeline_mode": str(refresh_run["pipeline_mode"] or ""),
                "collection_elapsed_seconds": float(refresh_run["collection_elapsed_seconds"] or 0.0),
                "article_count": len(recent_articles),
                "articles": recent_articles,
                "is_fallback": False,
                "fallback_from_generated_at": "",
                "recency_window_hours": LIVE_FEED_RECENT_HOURS,
            }
            if not latest_payload:
                latest_payload = payload

            if recent_articles:
                if latest_payload.get("generated_at") != payload.get("generated_at"):
                    payload["is_fallback"] = True
                    payload["fallback_from_generated_at"] = str(payload.get("generated_at", ""))
                    payload["generated_at"] = str(latest_payload.get("generated_at", ""))
                    payload["pipeline_mode"] = str(latest_payload.get("pipeline_mode", ""))
                    payload["collection_elapsed_seconds"] = float(
                        latest_payload.get("collection_elapsed_seconds", 0.0) or 0.0
                    )
                return payload

            if fallback_articles and not latest_fallback_payload:
                latest_fallback_payload = {
                    "generated_at": str(latest_payload.get("generated_at", "")),
                    "pipeline_mode": str(latest_payload.get("pipeline_mode", "")),
                    "collection_elapsed_seconds": float(
                        latest_payload.get("collection_elapsed_seconds", 0.0) or 0.0
                    ),
                    "article_count": len(fallback_articles),
                    "articles": fallback_articles,
                    "is_fallback": True,
                    "fallback_from_generated_at": str(refresh_run["generated_at"] or ""),
                    "recency_window_hours": LIVE_FEED_FALLBACK_HOURS,
                }
                if index == 0:
                    return latest_fallback_payload

        if latest_fallback_payload:
            return latest_fallback_payload
        return latest_payload


def fetch_saved_translation(
    db_path: str,
    *,
    story_key: str = "",
    title: str = "",
    summary: str = "",
    target_language: str = "en",
) -> dict[str, Any] | None:
    path = Path(db_path)
    if not path.exists():
        return None

    initialize_database(db_path)
    translation_key = build_translation_lookup_key(
        story_key=story_key,
        title=title,
        summary=summary,
        target_language=target_language,
    )
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT translation_key, story_key, source_language, target_language,
                   original_title, original_summary, translated_title,
                   translated_summary, created_at, updated_at
            FROM story_translations
            WHERE translation_key = ?
            LIMIT 1
            """,
            (translation_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "translation_key": str(row["translation_key"] or ""),
            "story_key": str(row["story_key"] or ""),
            "source_language": str(row["source_language"] or ""),
            "target_language": str(row["target_language"] or "en"),
            "original_title": str(row["original_title"] or ""),
            "original_summary": str(row["original_summary"] or ""),
            "translated_title": str(row["translated_title"] or ""),
            "translated_summary": str(row["translated_summary"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }


def save_story_translation(
    db_path: str,
    *,
    story_key: str = "",
    title: str = "",
    summary: str = "",
    source_language: str = "",
    target_language: str = "en",
    translated_title: str = "",
    translated_summary: str = "",
) -> dict[str, Any]:
    initialize_database(db_path)
    translation_key = build_translation_lookup_key(
        story_key=story_key,
        title=title,
        summary=summary,
        target_language=target_language,
    )
    now_iso = _utc_now_iso()
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT created_at FROM story_translations WHERE translation_key = ? LIMIT 1",
            (translation_key,),
        ).fetchone()
        created_at = str(existing["created_at"] or now_iso) if existing is not None else now_iso
        conn.execute(
            """
            INSERT INTO story_translations (
                translation_key,
                story_key,
                source_language,
                target_language,
                original_title,
                original_summary,
                translated_title,
                translated_summary,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(translation_key) DO UPDATE SET
                story_key = excluded.story_key,
                source_language = excluded.source_language,
                target_language = excluded.target_language,
                original_title = excluded.original_title,
                original_summary = excluded.original_summary,
                translated_title = excluded.translated_title,
                translated_summary = excluded.translated_summary,
                updated_at = excluded.updated_at
            """,
            (
                translation_key,
                str(story_key or ""),
                str(source_language or ""),
                str(target_language or "en"),
                str(title or ""),
                str(summary or ""),
                str(translated_title or ""),
                str(translated_summary or ""),
                created_at,
                now_iso,
            ),
        )
        conn.commit()
    return {
        "translation_key": translation_key,
        "story_key": str(story_key or ""),
        "source_language": str(source_language or ""),
        "target_language": str(target_language or "en"),
        "original_title": str(title or ""),
        "original_summary": str(summary or ""),
        "translated_title": str(translated_title or ""),
        "translated_summary": str(translated_summary or ""),
        "created_at": created_at,
        "updated_at": now_iso,
    }


def prune_pipeline_history(
    db_path: str,
    *,
    keep_refresh_runs: int = 250,
    observation_retention_days: int = 60,
    source_health_retention_days: int = 30,
    translation_retention_days: int = 180,
) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {
            "refresh_runs_deleted": 0,
            "story_observations_deleted": 0,
            "orphan_stories_deleted": 0,
            "source_health_deleted": 0,
            "story_translations_deleted": 0,
        }

    initialize_database(db_path)
    keep_refresh_runs = max(int(keep_refresh_runs), 1)
    observation_retention_days = max(int(observation_retention_days), 1)
    source_health_retention_days = max(int(source_health_retention_days), 1)
    translation_retention_days = max(int(translation_retention_days), 1)

    now_dt = datetime.now(timezone.utc)
    observation_cutoff = (now_dt - timedelta(days=observation_retention_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    source_health_cutoff = (now_dt - timedelta(days=source_health_retention_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    translation_cutoff = (now_dt - timedelta(days=translation_retention_days)).isoformat(timespec="seconds").replace("+00:00", "Z")

    with _connect(db_path) as conn:
        cutoff_row = conn.execute(
            """
            SELECT id
            FROM refresh_runs
            ORDER BY id DESC
            LIMIT 1 OFFSET ?
            """,
            (keep_refresh_runs - 1,),
        ).fetchone()
        refresh_runs_deleted = 0
        if cutoff_row is not None:
            cutoff_id = int(cutoff_row["id"] or 0)
            refresh_runs_deleted = int(
                conn.execute("DELETE FROM refresh_runs WHERE id < ?", (cutoff_id,)).rowcount or 0
            )

        story_observations_deleted = int(
            conn.execute(
                """
                DELETE FROM story_observations
                WHERE collected_at != ''
                  AND collected_at < ?
                """,
                (observation_cutoff,),
            ).rowcount
            or 0
        )
        source_health_deleted = int(
            conn.execute(
                """
                DELETE FROM source_health_observations
                WHERE collected_at != ''
                  AND collected_at < ?
                """,
                (source_health_cutoff,),
            ).rowcount
            or 0
        )
        orphan_stories_deleted = int(
            conn.execute(
                """
                DELETE FROM stories
                WHERE story_key NOT IN (
                    SELECT DISTINCT story_key
                    FROM story_observations
                )
                """,
            ).rowcount
            or 0
        )
        story_translations_deleted = int(
            conn.execute(
                """
                DELETE FROM story_translations
                WHERE updated_at != ''
                  AND updated_at < ?
                """,
                (translation_cutoff,),
            ).rowcount
            or 0
        )
        conn.commit()

    return {
        "refresh_runs_deleted": refresh_runs_deleted,
        "story_observations_deleted": story_observations_deleted,
        "orphan_stories_deleted": orphan_stories_deleted,
        "source_health_deleted": source_health_deleted,
        "story_translations_deleted": story_translations_deleted,
        "keep_refresh_runs": keep_refresh_runs,
        "observation_retention_days": observation_retention_days,
        "source_health_retention_days": source_health_retention_days,
        "translation_retention_days": translation_retention_days,
    }


def fetch_ticker_source_history(db_path: str, ticker: str) -> list[dict[str, Any]]:
    path = Path(db_path)
    normalized_ticker = str(ticker).strip().upper()
    if not path.exists() or not normalized_ticker:
        return []

    initialize_database(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                sho.source_group,
                sho.source_key,
                sho.source_name,
                COUNT(*) AS runs_seen,
                SUM(CASE WHEN sho.ok = 1 THEN 1 ELSE 0 END) AS ok_runs,
                MAX(sho.collected_at) AS last_collected_at,
                AVG(sho.elapsed_seconds) AS avg_elapsed_seconds,
                MAX(sho.elapsed_seconds) AS max_elapsed_seconds,
                SUM(sho.fetched_count) AS total_fetched_count,
                SUM(sho.matched_count) AS total_matched_count
            FROM source_health_observations AS sho
            WHERE sho.ticker = ? OR sho.ticker = ''
            GROUP BY sho.source_group, sho.source_key, sho.source_name
            ORDER BY total_matched_count DESC, total_fetched_count DESC, sho.source_name ASC
            """,
            (normalized_ticker,),
        ).fetchall()

        return [
            {
                "source_group": str(row["source_group"] or ""),
                "source_key": str(row["source_key"] or ""),
                "source_name": str(row["source_name"] or ""),
                "runs_seen": int(row["runs_seen"] or 0),
                "ok_runs": int(row["ok_runs"] or 0),
                "last_collected_at": str(row["last_collected_at"] or ""),
                "avg_elapsed_seconds": float(row["avg_elapsed_seconds"] or 0.0),
                "max_elapsed_seconds": float(row["max_elapsed_seconds"] or 0.0),
                "total_fetched_count": int(row["total_fetched_count"] or 0),
                "total_matched_count": int(row["total_matched_count"] or 0),
            }
            for row in rows
        ]


def _source_mode_label(*, ticker_runs: int, collector_rows: int) -> str:
    if ticker_runs >= collector_rows and collector_rows > 0:
        return "ticker_specific"
    if ticker_runs > 0:
        return "mixed"
    return "shared_pool"


def _source_kind_for_key(source_key: str, mode_label: str) -> str:
    key = str(source_key or "").strip().lower()
    if key == "stocktwits":
        return "supplemental_unstructured"
    if mode_label == "ticker_specific":
        return "structured_ticker"
    return "structured_shared"


def _recommend_source_tier(
    *,
    source_key: str,
    source_kind: str,
    health_rate: float,
    avg_primary_story_count: float,
    primary_refresh_rate: float,
    avg_new_primary_story_count: float,
) -> str:
    key = str(source_key or "").strip().lower()
    if key == "stocktwits":
        return "supplemental_unstructured"
    if health_rate < 0.8:
        if source_kind == "structured_ticker" and avg_primary_story_count >= 10.0 and primary_refresh_rate >= 0.5:
            return "primary_structured"
        return "monitor_only"
    if source_kind == "structured_ticker" and avg_primary_story_count >= 10.0 and primary_refresh_rate >= 0.5:
        return "primary_structured"
    if avg_primary_story_count >= 5.0 and primary_refresh_rate >= 0.6:
        return "primary_structured"
    if avg_primary_story_count >= 1.0 or primary_refresh_rate >= 0.35 or avg_new_primary_story_count >= 0.5:
        return "secondary_structured"
    return "monitor_only"


def _recommend_source_cadence(
    *,
    tier: str,
    source_kind: str,
    avg_elapsed_seconds: float,
) -> str:
    if tier == "supplemental_unstructured":
        return "every_refresh_active_tickers_only"
    if tier == "primary_structured":
        if source_kind == "structured_ticker":
            return "every_refresh_active_tickers_only"
        return "every_refresh"
    if tier == "secondary_structured":
        if avg_elapsed_seconds <= 0.75:
            return "every_2_refreshes"
        return "every_3_refreshes"
    return "every_3_refreshes_or_on_demand"


def _build_source_rationale(
    *,
    tier: str,
    source_kind: str,
    health_rate: float,
    avg_primary_story_count: float,
    primary_refresh_rate: float,
    outlier_refreshes: int,
) -> str:
    parts: list[str] = []
    if source_kind == "supplemental_unstructured":
        parts.append("Useful as a supplemental unstructured signal, not a primary structured driver.")
    elif tier == "primary_structured":
        parts.append("Consistently contributes structured primary stories across refreshes.")
    elif tier == "secondary_structured":
        parts.append("Healthy structured source with moderate but useful primary-story contribution.")
    else:
        parts.append("Healthy source, but recent visible primary-story contribution is limited.")

    parts.append(f"Health rate {health_rate:.0%}.")
    parts.append(f"Avg primary stories/refresh {avg_primary_story_count:.1f}.")
    parts.append(f"Primary contribution rate {primary_refresh_rate:.0%}.")
    if outlier_refreshes:
        parts.append(f"Excluded {outlier_refreshes} outlier refreshes from scoring.")
    return " ".join(parts)


def build_source_consistency_report(db_path: str, *, recent_runs: int = 12) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {"recent_runs_requested": int(recent_runs), "runs_analyzed": 0, "sources": []}

    initialize_database(db_path)
    with _connect(db_path) as conn:
        refresh_runs = conn.execute(
            """
            SELECT id, generated_at, collection_elapsed_seconds
            FROM refresh_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(recent_runs)),),
        ).fetchall()
        if not refresh_runs:
            return {"recent_runs_requested": int(recent_runs), "runs_analyzed": 0, "sources": []}

        refresh_run_ids = [int(row["id"]) for row in refresh_runs]
        placeholders = ",".join("?" for _ in refresh_run_ids)

        health_rows = conn.execute(
            f"""
            SELECT
                sho.refresh_run_id,
                rr.generated_at,
                sho.source_group,
                sho.source_key,
                sho.source_name,
                COUNT(*) AS collector_rows,
                SUM(CASE WHEN sho.ok = 1 THEN 1 ELSE 0 END) AS ok_rows,
                AVG(sho.elapsed_seconds) AS avg_elapsed_seconds,
                MAX(sho.elapsed_seconds) AS max_elapsed_seconds,
                SUM(sho.fetched_count) AS fetched_count,
                SUM(sho.matched_count) AS matched_count,
                SUM(CASE WHEN sho.fetched_count > 0 THEN 1 ELSE 0 END) AS positive_fetch_rows,
                SUM(CASE WHEN sho.matched_count > 0 THEN 1 ELSE 0 END) AS positive_match_rows,
                SUM(CASE WHEN COALESCE(sho.ticker, '') <> '' THEN 1 ELSE 0 END) AS ticker_runs,
                MAX(sho.collected_at) AS collected_at
            FROM source_health_observations AS sho
            JOIN refresh_runs AS rr
              ON rr.id = sho.refresh_run_id
            WHERE sho.refresh_run_id IN ({placeholders})
            GROUP BY sho.refresh_run_id, rr.generated_at, sho.source_group, sho.source_key, sho.source_name
            """,
            refresh_run_ids,
        ).fetchall()

        contribution_rows = conn.execute(
            f"""
            SELECT
                so.refresh_run_id,
                s.source_group,
                s.source_key,
                s.source_name,
                COUNT(DISTINCT s.story_key) AS story_count,
                COUNT(DISTINCT CASE WHEN so.bucket = 'stories' THEN s.story_key END) AS primary_story_count,
                COUNT(DISTINCT CASE WHEN so.bucket = 'stories' AND COALESCE(so.is_new, 0) = 1 THEN s.story_key END) AS new_primary_story_count,
                COUNT(DISTINCT CASE WHEN so.bucket = 'related_context' THEN s.story_key END) AS related_story_count,
                COUNT(DISTINCT CASE WHEN so.bucket = 'review_candidates' THEN s.story_key END) AS review_story_count
            FROM story_observations AS so
            JOIN stories AS s
              ON s.story_key = so.story_key
            WHERE so.refresh_run_id IN ({placeholders})
            GROUP BY so.refresh_run_id, s.source_group, s.source_key, s.source_name
            """,
            refresh_run_ids,
        ).fetchall()

    source_runs: dict[tuple[str, str, str], dict[int, dict[str, Any]]] = {}
    for row in health_rows:
        source_id = (
            str(row["source_group"] or ""),
            str(row["source_key"] or ""),
            str(row["source_name"] or ""),
        )
        source_runs.setdefault(source_id, {})[int(row["refresh_run_id"])] = {
            "refresh_run_id": int(row["refresh_run_id"]),
            "generated_at": str(row["generated_at"] or ""),
            "collector_rows": int(row["collector_rows"] or 0),
            "ok_rows": int(row["ok_rows"] or 0),
            "avg_elapsed_seconds": float(row["avg_elapsed_seconds"] or 0.0),
            "max_elapsed_seconds": float(row["max_elapsed_seconds"] or 0.0),
            "fetched_count": int(row["fetched_count"] or 0),
            "matched_count": int(row["matched_count"] or 0),
            "positive_fetch_rows": int(row["positive_fetch_rows"] or 0),
            "positive_match_rows": int(row["positive_match_rows"] or 0),
            "ticker_runs": int(row["ticker_runs"] or 0),
            "collected_at": str(row["collected_at"] or ""),
            "story_count": 0,
            "primary_story_count": 0,
            "new_primary_story_count": 0,
            "related_story_count": 0,
            "review_story_count": 0,
        }

    for row in contribution_rows:
        source_id = (
            str(row["source_group"] or ""),
            str(row["source_key"] or ""),
            str(row["source_name"] or ""),
        )
        refresh_run_id = int(row["refresh_run_id"])
        item = source_runs.setdefault(source_id, {}).setdefault(
            refresh_run_id,
            {
                "refresh_run_id": refresh_run_id,
                "generated_at": "",
                "collector_rows": 0,
                "ok_rows": 0,
                "avg_elapsed_seconds": 0.0,
                "max_elapsed_seconds": 0.0,
                "fetched_count": 0,
                "matched_count": 0,
                "positive_fetch_rows": 0,
                "positive_match_rows": 0,
                "ticker_runs": 0,
                "collected_at": "",
                "story_count": 0,
                "primary_story_count": 0,
                "new_primary_story_count": 0,
                "related_story_count": 0,
                "review_story_count": 0,
            },
        )
        item["story_count"] = int(row["story_count"] or 0)
        item["primary_story_count"] = int(row["primary_story_count"] or 0)
        item["new_primary_story_count"] = int(row["new_primary_story_count"] or 0)
        item["related_story_count"] = int(row["related_story_count"] or 0)
        item["review_story_count"] = int(row["review_story_count"] or 0)

    source_reports: list[dict[str, Any]] = []
    for (source_group, source_key, source_name), run_map in source_runs.items():
        ordered_runs = sorted(
            run_map.values(),
            key=lambda row: row["refresh_run_id"],
            reverse=True,
        )
        primary_positive_series = [
            int(row["primary_story_count"])
            for row in ordered_runs
            if int(row["primary_story_count"] or 0) > 0
        ]
        story_positive_series = [
            int(row["story_count"])
            for row in ordered_runs
            if int(row["story_count"] or 0) > 0
        ]
        median_primary = float(median(primary_positive_series)) if primary_positive_series else 0.0
        median_story = float(median(story_positive_series)) if story_positive_series else 0.0
        primary_outlier_threshold = 1000.0
        story_outlier_threshold = 1500.0
        if median_primary and median_primary <= 200.0:
            primary_outlier_threshold = max(primary_outlier_threshold, median_primary * 5.0)
        if median_story and median_story <= 300.0:
            story_outlier_threshold = max(story_outlier_threshold, median_story * 5.0)

        effective_runs: list[dict[str, Any]] = []
        excluded_outliers: list[int] = []
        for row in ordered_runs:
            if (
                float(row["primary_story_count"] or 0.0) > primary_outlier_threshold
                or float(row["story_count"] or 0.0) > story_outlier_threshold
            ):
                excluded_outliers.append(int(row["refresh_run_id"]))
                continue
            effective_runs.append(row)
        if not effective_runs:
            effective_runs = ordered_runs

        refreshes_seen = len(effective_runs)
        collector_rows = sum(int(row["collector_rows"] or 0) for row in effective_runs)
        ok_rows = sum(int(row["ok_rows"] or 0) for row in effective_runs)
        fetched_total = sum(int(row["fetched_count"] or 0) for row in effective_runs)
        matched_total = sum(int(row["matched_count"] or 0) for row in effective_runs)
        ticker_runs = sum(int(row["ticker_runs"] or 0) for row in effective_runs)
        avg_elapsed_seconds = (
            sum(float(row["avg_elapsed_seconds"] or 0.0) for row in effective_runs) / refreshes_seen
            if refreshes_seen
            else 0.0
        )
        max_elapsed_seconds = max(float(row["max_elapsed_seconds"] or 0.0) for row in effective_runs)
        avg_story_count = (
            sum(float(row["story_count"] or 0.0) for row in effective_runs) / refreshes_seen
            if refreshes_seen
            else 0.0
        )
        avg_primary_story_count = (
            sum(float(row["primary_story_count"] or 0.0) for row in effective_runs) / refreshes_seen
            if refreshes_seen
            else 0.0
        )
        avg_new_primary_story_count = (
            sum(float(row["new_primary_story_count"] or 0.0) for row in effective_runs) / refreshes_seen
            if refreshes_seen
            else 0.0
        )
        refreshes_with_primary = sum(1 for row in effective_runs if int(row["primary_story_count"] or 0) > 0)
        refreshes_with_new_primary = sum(1 for row in effective_runs if int(row["new_primary_story_count"] or 0) > 0)
        refreshes_with_matches = sum(1 for row in effective_runs if int(row["matched_count"] or 0) > 0)
        health_rate = (ok_rows / collector_rows) if collector_rows else 0.0
        primary_refresh_rate = (refreshes_with_primary / refreshes_seen) if refreshes_seen else 0.0
        new_primary_refresh_rate = (refreshes_with_new_primary / refreshes_seen) if refreshes_seen else 0.0
        match_refresh_rate = (refreshes_with_matches / refreshes_seen) if refreshes_seen else 0.0

        mode_label = _source_mode_label(ticker_runs=ticker_runs, collector_rows=collector_rows)
        source_kind = _source_kind_for_key(source_key, mode_label)
        tier = _recommend_source_tier(
            source_key=source_key,
            source_kind=source_kind,
            health_rate=health_rate,
            avg_primary_story_count=avg_primary_story_count,
            primary_refresh_rate=primary_refresh_rate,
            avg_new_primary_story_count=avg_new_primary_story_count,
        )
        cadence = _recommend_source_cadence(
            tier=tier,
            source_kind=source_kind,
            avg_elapsed_seconds=avg_elapsed_seconds,
        )

        latest_run = effective_runs[0]
        source_reports.append(
            {
                "source_group": source_group,
                "source_key": source_key,
                "source_name": source_name,
                "mode_label": mode_label,
                "source_kind": source_kind,
                "recommended_tier": tier,
                "recommended_cadence": cadence,
                "refreshes_seen": refreshes_seen,
                "collector_rows": collector_rows,
                "health_rate": health_rate,
                "ok_rows": ok_rows,
                "fetched_total": fetched_total,
                "matched_total": matched_total,
                "avg_elapsed_seconds": avg_elapsed_seconds,
                "max_elapsed_seconds": max_elapsed_seconds,
                "avg_story_count": avg_story_count,
                "avg_primary_story_count": avg_primary_story_count,
                "avg_new_primary_story_count": avg_new_primary_story_count,
                "primary_refresh_rate": primary_refresh_rate,
                "new_primary_refresh_rate": new_primary_refresh_rate,
                "match_refresh_rate": match_refresh_rate,
                "latest_generated_at": str(latest_run.get("generated_at", "")),
                "latest_collected_at": str(latest_run.get("collected_at", "")),
                "latest_story_count": int(latest_run.get("story_count", 0) or 0),
                "latest_primary_story_count": int(latest_run.get("primary_story_count", 0) or 0),
                "latest_new_primary_story_count": int(latest_run.get("new_primary_story_count", 0) or 0),
                "outlier_refreshes_excluded": excluded_outliers,
                "rationale": _build_source_rationale(
                    tier=tier,
                    source_kind=source_kind,
                    health_rate=health_rate,
                    avg_primary_story_count=avg_primary_story_count,
                    primary_refresh_rate=primary_refresh_rate,
                    outlier_refreshes=len(excluded_outliers),
                ),
            }
        )

    source_reports.sort(
        key=lambda row: (
            {"primary_structured": 0, "secondary_structured": 1, "supplemental_unstructured": 2, "monitor_only": 3}.get(
                str(row["recommended_tier"] or ""),
                9,
            ),
            -float(row["avg_primary_story_count"] or 0.0),
            -float(row["primary_refresh_rate"] or 0.0),
            str(row["source_name"] or ""),
        )
    )

    return {
        "recent_runs_requested": int(recent_runs),
        "runs_analyzed": len(refresh_run_ids),
        "latest_generated_at": str(refresh_runs[0]["generated_at"] or ""),
        "sources": source_reports,
    }
