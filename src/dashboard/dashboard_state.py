from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
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
from src.storage import (
    fetch_latest_market_article_pool,
    fetch_latest_ticker_snapshot,
    fetch_latest_ticker_universe,
    fetch_latest_watchlist_snapshot,
    fetch_ticker_source_history,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Serve a local dashboard for the watchlist news pipeline with a manual "
            "update button and a server-side cooldown."
        )
    )
    parser.add_argument(
        "--watchlist-file",
        default="data/watchlists/stress_watchlist_50.json",
        help="JSON watchlist file containing ticker, company, and keyword entries.",
    )
    parser.add_argument(
        "--snapshot-file",
        default="data/cache/watchlist_snapshot_50_latest.json",
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
        default=120,
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
                rows.append(
                    {
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
                        "source_name": str(row.get("source_name", "")),
                        "event_type": str(row.get("event_type", "")),
                        "signal_strength": signal_strength,
                        "signal_display": str(row.get("signal_strength", "")) or "0",
                        "published": str(row.get("published", "")),
                        "published_raw": str(row.get("published_raw", "")),
                        "published_at": str(row.get("published_at", "")),
                        "first_seen_at": str(row.get("first_seen_at", "")),
                        "last_seen_at": str(row.get("last_seen_at", "")),
                        "collected_at": str(row.get("collected_at", "")),
                        "coverage_count": int(row.get("coverage_count", 0) or 0),
                        "summary": str(row.get("summary", "")),
                        "is_new": bool(row.get("is_new")),
                    }
                )

    rows.sort(
        key=lambda row: (
            0 if row["is_new"] else 1,
            row["bucket_priority"],
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

    source_counts: dict[str, int] = {}
    for row in all_rows:
        source_name = str(row.get("source_name", "")).strip()
        if source_name:
            source_counts[source_name] = source_counts.get(source_name, 0) + 1

    first_seen_values = [str(row.get("first_seen_at", "")).strip() for row in all_rows if str(row.get("first_seen_at", "")).strip()]
    last_seen_values = [str(row.get("last_seen_at", "")).strip() for row in all_rows if str(row.get("last_seen_at", "")).strip()]

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
        self.update_in_progress = False

        self.snapshot: dict[str, Any] = self._load_primary_snapshot()
        persisted = load_json(
            self.dashboard_state_path,
            {
                "last_refresh_epoch": 0.0,
                "last_refresh_iso": "",
                "seen_story_ids": [],
                "last_status": "Dashboard initialized.",
            },
        )
        self.last_refresh_epoch = float(persisted.get("last_refresh_epoch", 0.0) or 0.0)
        self.last_refresh_iso = str(persisted.get("last_refresh_iso", ""))
        self.seen_story_ids = set(str(item) for item in persisted.get("seen_story_ids", []))
        self.last_status = str(persisted.get("last_status", "Dashboard initialized."))

        if not self.snapshot:
            self.snapshot = self._run_snapshot_update(mark_all_seen=False)
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
        }
        self.dashboard_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _enrich_ticker_metadata(self, ticker_payload: dict[str, Any]) -> dict[str, Any]:
        ticker_copy = dict(ticker_payload)
        ticker = str(ticker_copy.get("ticker", ""))
        metadata = self.watchlist_metadata.get(ticker, {})
        if metadata:
            ticker_copy["company"] = str(ticker_copy.get("company", "") or metadata.get("company", ""))
            ticker_copy["sector"] = str(ticker_copy.get("sector", "") or metadata.get("sector", ""))
            ticker_copy["industry"] = str(ticker_copy.get("industry", "") or metadata.get("industry", ""))
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

    def _run_snapshot_update(self, *, mark_all_seen: bool) -> dict[str, Any]:
        snapshot = build_watchlist_snapshot(
            watchlist_file=self.watchlist_file,
            rss_limit=self.rss_limit,
            structured_limit=self.structured_limit,
            state_file=self.state_file,
            include_seen=False,
            skip_rss=self.skip_rss,
            skip_structured=self.skip_structured,
            sqlite_db=self.sqlite_db,
        )
        annotated = self._annotate_snapshot(snapshot, mark_all_seen=mark_all_seen)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(annotated, indent=2), encoding="utf-8")
        return annotated

    def cooldown_remaining(self) -> int:
        elapsed = time.time() - self.last_refresh_epoch
        remaining = self.cooldown_seconds - int(elapsed)
        return max(0, remaining)

    def state_payload(self) -> dict[str, Any]:
        snapshot = self.snapshot or self._load_primary_snapshot() or {"tickers": []}
        tickers = [self._enrich_ticker_metadata(item) for item in snapshot.get("tickers", [])]
        feed_rows = flatten_feed_rows(tickers)
        source_health = list(snapshot.get("source_health", []))
        unique_sources = sorted({row["source_name"] for row in feed_rows if row["source_name"]})
        unique_event_types = sorted({row["event_type"] for row in feed_rows if row["event_type"]})
        unique_sectors = sorted({str(item.get("sector", "")) for item in tickers if str(item.get("sector", ""))})
        unique_industries = sorted({str(item.get("industry", "")) for item in tickers if str(item.get("industry", ""))})
        new_rows = sum(1 for row in feed_rows if row["is_new"])
        ok_source_count = sum(1 for row in source_health if row.get("ok"))
        return {
            "generated_at": snapshot.get("generated_at", ""),
            "generated_at_display": format_eastern_time(str(snapshot.get("generated_at", ""))),
            "pipeline_mode": str(snapshot.get("pipeline_mode", "")),
            "collection_elapsed_seconds": float(snapshot.get("collection_elapsed_seconds", 0.0) or 0.0),
            "last_refresh_iso": self.last_refresh_iso,
            "last_refresh_display": format_eastern_time(self.last_refresh_iso),
            "last_status": self.last_status,
            "cooldown_seconds": self.cooldown_seconds,
            "cooldown_remaining": self.cooldown_remaining(),
            "update_in_progress": self.update_in_progress,
            "tickers": tickers,
            "feed_rows": feed_rows,
            "source_health": source_health,
            "summary": {
                "total_rows": len(feed_rows),
                "new_rows": new_rows,
                "ticker_count": len(tickers),
                "source_count": len(unique_sources),
                "source_health_total": len(source_health),
                "source_health_ok": ok_source_count,
            },
            "filters": {
                "tickers": [str(item.get("ticker", "")) for item in tickers if str(item.get("ticker", ""))],
                "sectors": unique_sectors,
                "industries": unique_industries,
                "sources": unique_sources,
                "event_types": unique_event_types,
                "buckets": ["Primary", "Related", "Review"],
            },
        }

    def trigger_update(self) -> tuple[bool, str]:
        with self.lock:
            if self.update_in_progress:
                return False, "Update already in progress."
            remaining = self.cooldown_remaining()
            if remaining > 0:
                return False, f"Update locked for {remaining} more seconds."
            self.update_in_progress = True

        try:
            updated = self._run_snapshot_update(mark_all_seen=False)
            self.snapshot = updated
            self.last_refresh_epoch = time.time()
            self.last_refresh_iso = _iso_now()
            new_primary_total = sum(int(item.get("new_primary_count", 0)) for item in updated.get("tickers", []))
            self.last_status = (
                f"Watchlist refreshed successfully at {format_eastern_time(self.last_refresh_iso)}. "
                f"New primary stories found: {new_primary_total}."
            )
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
                return {
                    "generated_at": snapshot.get("generated_at", ""),
                    "pipeline_mode": snapshot.get("pipeline_mode", ""),
                    "collection_elapsed_seconds": snapshot.get("collection_elapsed_seconds", 0.0),
                    "source_health": snapshot.get("source_health", []),
                    "source_history": [],
                    "ticker": self._enrich_ticker_metadata(ticker_payload),
                    "summary": summarize_ticker_payload(self._enrich_ticker_metadata(ticker_payload)),
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
                return db_payload

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
            "articles": articles,
        }
