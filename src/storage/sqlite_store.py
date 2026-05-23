from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.timestamp_utils import normalize_published_fields


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
            """
        )
        _ensure_column(conn, "refresh_runs", "pipeline_mode", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "refresh_runs", "collection_elapsed_seconds", "REAL NOT NULL DEFAULT 0.0")
        _ensure_column(conn, "ticker_runs", "sector", "TEXT")
        _ensure_column(conn, "ticker_runs", "industry", "TEXT")
        _ensure_column(conn, "ticker_runs", "raw_match_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "stories", "published_at", "TEXT")
        _ensure_column(conn, "story_observations", "collected_at", "TEXT NOT NULL DEFAULT ''")


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


def fetch_latest_market_article_pool(db_path: str) -> dict[str, Any]:
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

        articles = []
        for row in rows:
            collected_at = str(row["last_collected_at"] or refresh_run["generated_at"] or "")
            published_fields = _resolve_published_fields(
                row["published_raw"],
                row["published_display"],
                row["published_at"],
                collected_at=collected_at,
            )
            matched_tickers = sorted(
                [value for value in str(row["matched_tickers_csv"] or "").split(",") if value]
            )
            buckets = sorted([value for value in str(row["buckets_csv"] or "").split(",") if value])
            event_types = sorted([value for value in str(row["event_types_csv"] or "").split(",") if value])
            articles.append(
                {
                    "story_key": str(row["story_key"] or ""),
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
                    "matched_tickers": matched_tickers,
                    "matched_ticker_count": len(matched_tickers),
                    "buckets": buckets,
                    "event_types": event_types,
                }
            )

        return {
            "generated_at": str(refresh_run["generated_at"] or ""),
            "pipeline_mode": str(refresh_run["pipeline_mode"] or ""),
            "collection_elapsed_seconds": float(refresh_run["collection_elapsed_seconds"] or 0.0),
            "article_count": len(articles),
            "articles": articles,
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
