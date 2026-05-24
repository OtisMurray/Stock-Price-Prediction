from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
import time

from .rss_collectors import build_keywords, fetch_rss_source
from .rss_sources import DEFAULT_BASELINE_SOURCE_KEYS, RSS_SOURCES
from .source_cache import get_cached_rows, load_source_cache, save_source_cache, set_cached_rows
from .seen_cache import load_seen_links, save_seen_links
from .structured_collectors import collect_structured_headlines
from .structured_sources import PUBLIC_STRUCTURED_SOURCE_KEYS, STRUCTURED_SOURCES
from .timestamp_utils import normalize_published_fields


@dataclass(slots=True)
class SourceCollectionResult:
    rows: list[dict[str, Any]]
    failures: list[str]


@dataclass(slots=True)
class SourceHealthRecord:
    source_group: str
    source_key: str
    source_name: str
    ok: bool
    elapsed_seconds: float
    fetched_count: int
    matched_count: int
    ticker: str = ""
    error: str = ""
    collected_at: str = ""
    cache_hit: bool = False
    cache_age_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_group": self.source_group,
            "source_key": self.source_key,
            "source_name": self.source_name,
            "ok": self.ok,
            "elapsed_seconds": self.elapsed_seconds,
            "fetched_count": self.fetched_count,
            "matched_count": self.matched_count,
            "ticker": self.ticker,
            "error": self.error,
            "collected_at": self.collected_at,
            "cache_hit": self.cache_hit,
            "cache_age_seconds": self.cache_age_seconds,
        }


@dataclass(slots=True)
class WatchlistSourceCollectionResult:
    rows_by_ticker: dict[str, list[dict[str, Any]]]
    failures: list[str]
    source_health: list[dict[str, Any]]


DEFAULT_SOURCE_CACHE_TTLS = {
    "baseline_rss:marketwatch_topstories": 45,
    "baseline_rss:marketwatch_marketpulse": 45,
    "baseline_rss:sec_press_releases": 180,
    "baseline_rss:prnewswire_all_news": 120,
    "baseline_rss:yahoo_finance": 45,
    "structured_news:tradingview": 45,
    "structured_news:stocktwits": 90,
    "structured_news:prnewswire": 120,
    "structured_news:globenewswire": 300,
    "structured_news:accessnewswire": 240,
    "structured_news:mtnewswires": 240,
    "structured_news:finviz": 180,
}

SOURCE_REFRESH_TIERS = {
    "baseline_rss:marketwatch_topstories": "fast",
    "baseline_rss:marketwatch_marketpulse": "fast",
    "baseline_rss:sec_press_releases": "slow",
    "baseline_rss:prnewswire_all_news": "medium",
    "baseline_rss:yahoo_finance": "fast",
    "structured_news:tradingview": "fast",
    "structured_news:stocktwits": "medium",
    "structured_news:prnewswire": "medium",
    "structured_news:globenewswire": "slow",
    "structured_news:accessnewswire": "slow",
    "structured_news:mtnewswires": "slow",
    "structured_news:finviz": "slow",
}


def _cache_ttl_for_key(cache_key: str) -> int:
    if cache_key.startswith("baseline_rss:yahoo_finance:"):
        return DEFAULT_SOURCE_CACHE_TTLS["baseline_rss:yahoo_finance"]
    if cache_key.startswith("structured_news:tradingview:"):
        return DEFAULT_SOURCE_CACHE_TTLS["structured_news:tradingview"]
    if cache_key.startswith("structured_news:stocktwits:"):
        return DEFAULT_SOURCE_CACHE_TTLS["structured_news:stocktwits"]
    return DEFAULT_SOURCE_CACHE_TTLS.get(cache_key, 0)


def _source_tier_for_key(cache_key: str) -> str:
    if cache_key.startswith("baseline_rss:yahoo_finance:"):
        return SOURCE_REFRESH_TIERS["baseline_rss:yahoo_finance"]
    if cache_key.startswith("structured_news:tradingview:"):
        return SOURCE_REFRESH_TIERS["structured_news:tradingview"]
    if cache_key.startswith("structured_news:stocktwits:"):
        return SOURCE_REFRESH_TIERS["structured_news:stocktwits"]
    return SOURCE_REFRESH_TIERS.get(cache_key, "medium")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _article_to_row(article) -> dict[str, Any]:
    collected_at = _utc_now_iso()
    published_fields = normalize_published_fields(article.published, collected_at=collected_at)
    return {
        "source_group": "baseline_rss",
        "source_key": article.source_key,
        "source_name": article.source_name,
        "title": article.title,
        "link": article.link,
        "published": published_fields["published_display"],
        "published_raw": published_fields["published_raw"],
        "published_at": published_fields["published_at"],
        "summary": article.summary,
        "collection_method": "rss",
        "collected_at": collected_at,
    }


def _headline_to_row(headline) -> dict[str, Any]:
    collected_at = _utc_now_iso()
    published_fields = normalize_published_fields(headline.published, collected_at=collected_at)
    return {
        "source_group": "structured_news",
        "source_key": headline.source_key,
        "source_name": headline.source_name,
        "title": headline.title,
        "link": headline.link,
        "published": published_fields["published_display"],
        "published_raw": published_fields["published_raw"],
        "published_at": published_fields["published_at"],
        "summary": headline.summary,
        "collection_method": headline.collection_method,
        "collected_at": collected_at,
    }


def collect_candidate_rows(
    *,
    ticker: str,
    company: str = "",
    extra_keywords: list[str] | None = None,
    rss_limit: int = 10,
    structured_limit: int = 10,
    skip_rss: bool = False,
    skip_structured: bool = False,
    state_file: str = "tmp/seen_structured_headlines_today.json",
    include_seen: bool = False,
    baseline_source_keys: list[str] | None = None,
    structured_source_keys: list[str] | None = None,
    matcher: Callable[[list[str], list[str]], bool] | None = None,
) -> SourceCollectionResult:
    keywords = build_keywords(
        ticker=ticker,
        company_name=company or None,
        extra_keywords=extra_keywords,
    )
    row_matcher = matcher or (lambda text_parts, row_keywords: True)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    seen_links = set() if include_seen else load_seen_links(state_file)
    newly_seen = set(seen_links)

    if not skip_rss:
        try:
            rss_articles = collect_baseline_articles(
                ticker=ticker,
                source_keys=baseline_source_keys,
                limit_per_source=None if rss_limit == 0 else rss_limit,
            )
            for article in rss_articles:
                if row_matcher([article.title, article.summary, article.text], keywords):
                    rows.append(_article_to_row(article))
        except Exception as exc:
            failures.append(f"baseline_rss: {exc}")

    if not skip_structured:
        active_structured_keys = structured_source_keys or list(PUBLIC_STRUCTURED_SOURCE_KEYS)
        for source_key in active_structured_keys:
            source = STRUCTURED_SOURCES[source_key]
            try:
                headlines = collect_structured_headlines(
                    source_key,
                    limit=None if structured_limit == 0 else structured_limit,
                    ticker=ticker if source.is_ticker_specific else "",
                )
                for headline in headlines:
                    if not include_seen and headline.link in seen_links:
                        continue
                    if row_matcher([headline.title, headline.summary], keywords):
                        rows.append(_headline_to_row(headline))
                        newly_seen.add(headline.link)
            except Exception as exc:
                failures.append(f"{STRUCTURED_SOURCES[source_key].name}: {exc}")

    if not include_seen:
        save_seen_links(state_file, newly_seen)

    return SourceCollectionResult(rows=rows, failures=failures)


def collect_watchlist_candidate_rows(
    *,
    entries: list[dict[str, Any]],
    rss_limit: int = 10,
    structured_limit: int = 10,
    skip_rss: bool = False,
    skip_structured: bool = False,
    state_file: str = "tmp/seen_structured_headlines_today.json",
    include_seen: bool = False,
    baseline_source_keys: list[str] | None = None,
    structured_source_keys: list[str] | None = None,
    matcher: Callable[[list[str], list[str]], bool] | None = None,
    max_workers: int | None = None,
    source_cache_file: str = "tmp/source_fetch_cache.json",
) -> WatchlistSourceCollectionResult:
    row_matcher = matcher or (lambda text_parts, row_keywords: True)
    source_keys = baseline_source_keys or list(DEFAULT_BASELINE_SOURCE_KEYS)
    struct_keys = structured_source_keys or list(PUBLIC_STRUCTURED_SOURCE_KEYS)
    rows_by_ticker: dict[str, list[dict[str, Any]]] = {
        str(entry.get("ticker", "")).upper(): [] for entry in entries
    }
    failures: list[str] = []
    source_health: list[dict[str, Any]] = []
    seen_links = set() if include_seen else load_seen_links(state_file)
    newly_seen = set(seen_links)
    cache_payload = load_source_cache(source_cache_file)
    cache_dirty = False
    keywords_by_ticker = {
        str(entry.get("ticker", "")).upper(): build_keywords(
            ticker=str(entry.get("ticker", "")),
            company_name=str(entry.get("company", "")) or None,
            extra_keywords=entry.get("keywords", []),
        )
        for entry in entries
    }
    worker_count = max_workers or max(8, min(24, len(entries) + len(source_keys) + len(struct_keys)))
    now_epoch = time.time()

    def distribute_row(row: dict[str, Any], *, text_parts: list[str]) -> int:
        matched = 0
        for ticker, keywords in keywords_by_ticker.items():
            if row_matcher(text_parts, keywords):
                rows_by_ticker[ticker].append(dict(row))
                matched += 1
        return matched

    def record_cached_general_rows(source_key: str, cached_rows: list[dict[str, Any]], cache_age_seconds: float) -> None:
        matched_total = 0
        for row in cached_rows:
            matched_total += distribute_row(dict(row), text_parts=[row.get("title", ""), row.get("summary", ""), row.get("summary", "")])
        source = RSS_SOURCES[source_key]
        source_health.append(
            SourceHealthRecord(
                source_group="baseline_rss",
                source_key=source_key,
                source_name=source.name,
                ok=True,
                elapsed_seconds=0.0,
                fetched_count=len(cached_rows),
                matched_count=matched_total,
                collected_at=_utc_now_iso(),
                cache_hit=True,
                cache_age_seconds=round(cache_age_seconds, 1),
            ).to_dict()
        )

    def record_cached_ticker_rows(source_key: str, ticker: str, cached_rows: list[dict[str, Any]], cache_age_seconds: float) -> None:
        matched_total = 0
        keywords = keywords_by_ticker[ticker]
        for row in cached_rows:
            if row_matcher([row.get("title", ""), row.get("summary", ""), row.get("summary", "")], keywords):
                rows_by_ticker[ticker].append(dict(row))
                matched_total += 1
        source = RSS_SOURCES[source_key]
        source_health.append(
            SourceHealthRecord(
                source_group="baseline_rss",
                source_key=source_key,
                source_name=source.name,
                ok=True,
                elapsed_seconds=0.0,
                fetched_count=len(cached_rows),
                matched_count=matched_total,
                ticker=ticker,
                collected_at=_utc_now_iso(),
                cache_hit=True,
                cache_age_seconds=round(cache_age_seconds, 1),
            ).to_dict()
        )

    def record_cached_structured_rows(source_key: str, cached_rows: list[dict[str, Any]], cache_age_seconds: float) -> None:
        matched_total = 0
        fetched_total = 0
        for row in cached_rows:
            link = str(row.get("link", ""))
            if not include_seen and link in seen_links:
                continue
            fetched_total += 1
            row_matches = distribute_row(dict(row), text_parts=[row.get("title", ""), row.get("summary", "")])
            matched_total += row_matches
            if row_matches > 0 and link:
                newly_seen.add(link)
        source = STRUCTURED_SOURCES[source_key]
        source_health.append(
            SourceHealthRecord(
                source_group="structured_news",
                source_key=source_key,
                source_name=source.name,
                ok=True,
                elapsed_seconds=0.0,
                fetched_count=fetched_total,
                matched_count=matched_total,
                collected_at=_utc_now_iso(),
                cache_hit=True,
                cache_age_seconds=round(cache_age_seconds, 1),
            ).to_dict()
        )

    def record_cached_ticker_structured_rows(
        source_key: str,
        ticker: str,
        cached_rows: list[dict[str, Any]],
        cache_age_seconds: float,
    ) -> None:
        matched_total = 0
        fetched_total = 0
        keywords = keywords_by_ticker[ticker]
        for row in cached_rows:
            link = str(row.get("link", ""))
            if not include_seen and link in seen_links:
                continue
            fetched_total += 1
            if row_matcher([row.get("title", ""), row.get("summary", "")], keywords):
                rows_by_ticker[ticker].append(dict(row))
                matched_total += 1
                if link:
                    newly_seen.add(link)
        source = STRUCTURED_SOURCES[source_key]
        source_health.append(
            SourceHealthRecord(
                source_group="structured_news",
                source_key=source_key,
                source_name=source.name,
                ok=True,
                elapsed_seconds=0.0,
                fetched_count=fetched_total,
                matched_count=matched_total,
                ticker=ticker,
                collected_at=_utc_now_iso(),
                cache_hit=True,
                cache_age_seconds=round(cache_age_seconds, 1),
            ).to_dict()
        )

    def fetch_general_rss(source_key: str) -> tuple[str, list[Any], float]:
        started = time.perf_counter()
        articles = fetch_rss_source(
            source_key=source_key,
            ticker=None,
            limit_per_source=None if rss_limit == 0 else rss_limit,
        )
        return source_key, articles, time.perf_counter() - started

    def fetch_ticker_rss(source_key: str, ticker: str) -> tuple[str, str, list[Any], float]:
        started = time.perf_counter()
        articles = fetch_rss_source(
            source_key=source_key,
            ticker=ticker,
            limit_per_source=None if rss_limit == 0 else rss_limit,
        )
        return source_key, ticker, articles, time.perf_counter() - started

    def fetch_structured(source_key: str) -> tuple[str, list[Any], float]:
        started = time.perf_counter()
        headlines = collect_structured_headlines(
            source_key,
            limit=None if structured_limit == 0 else structured_limit,
        )
        return source_key, headlines, time.perf_counter() - started

    def fetch_ticker_structured(source_key: str, ticker: str) -> tuple[str, str, list[Any], float]:
        started = time.perf_counter()
        headlines = collect_structured_headlines(
            source_key,
            limit=None if structured_limit == 0 else structured_limit,
            ticker=ticker,
        )
        return source_key, ticker, headlines, time.perf_counter() - started

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = []
        if not skip_rss:
            for source_key in source_keys:
                source = RSS_SOURCES[source_key]
                if source.is_ticker_specific:
                    for entry in entries:
                        ticker = str(entry.get("ticker", "")).upper()
                        cache_key = f"baseline_rss:{source_key}:{ticker}"
                        cached_rows, cache_age_seconds = get_cached_rows(
                            cache_payload,
                            cache_key=cache_key,
                            max_age_seconds=_cache_ttl_for_key(cache_key),
                            now_epoch=now_epoch,
                        )
                        if cached_rows is not None:
                            record_cached_ticker_rows(source_key, ticker, cached_rows, cache_age_seconds)
                            continue
                        futures.append(
                            {
                                "type": "ticker_rss",
                                "source_key": source_key,
                                "ticker": ticker,
                                "future": executor.submit(fetch_ticker_rss, source_key, ticker),
                            }
                        )
                else:
                    cache_key = f"baseline_rss:{source_key}"
                    cached_rows, cache_age_seconds = get_cached_rows(
                        cache_payload,
                        cache_key=cache_key,
                        max_age_seconds=_cache_ttl_for_key(cache_key),
                        now_epoch=now_epoch,
                    )
                    if cached_rows is not None:
                        record_cached_general_rows(source_key, cached_rows, cache_age_seconds)
                        continue
                    futures.append(
                        {
                            "type": "general_rss",
                            "source_key": source_key,
                            "ticker": "",
                            "future": executor.submit(fetch_general_rss, source_key),
                        }
                    )

        if not skip_structured:
            for source_key in struct_keys:
                source = STRUCTURED_SOURCES[source_key]
                if source.is_ticker_specific:
                    for entry in entries:
                        ticker = str(entry.get("ticker", "")).upper()
                        cache_key = f"structured_news:{source_key}:{ticker}"
                        cached_rows, cache_age_seconds = get_cached_rows(
                            cache_payload,
                            cache_key=cache_key,
                            max_age_seconds=_cache_ttl_for_key(cache_key),
                            now_epoch=now_epoch,
                        )
                        if cached_rows is not None:
                            record_cached_ticker_structured_rows(
                                source_key,
                                ticker,
                                cached_rows,
                                cache_age_seconds,
                            )
                            continue
                        futures.append(
                            {
                                "type": "ticker_structured",
                                "source_key": source_key,
                                "ticker": ticker,
                                "future": executor.submit(fetch_ticker_structured, source_key, ticker),
                            }
                        )
                else:
                    cache_key = f"structured_news:{source_key}"
                    cached_rows, cache_age_seconds = get_cached_rows(
                        cache_payload,
                        cache_key=cache_key,
                        max_age_seconds=_cache_ttl_for_key(cache_key),
                        now_epoch=now_epoch,
                    )
                    if cached_rows is not None:
                        record_cached_structured_rows(source_key, cached_rows, cache_age_seconds)
                        continue
                    futures.append(
                        {
                            "type": "structured",
                            "source_key": source_key,
                            "ticker": "",
                            "future": executor.submit(fetch_structured, source_key),
                        }
                    )

        for task in futures:
            future_type = task["type"]
            source_key = str(task["source_key"])
            ticker = str(task["ticker"])
            future = task["future"]
            if future_type == "general_rss":
                try:
                    source_key, articles, elapsed = future.result()
                    source = RSS_SOURCES[source_key]
                    cache_key = f"baseline_rss:{source_key}"
                    cached_source_rows: list[dict[str, Any]] = []
                    matched_total = 0
                    for article in articles:
                        row = _article_to_row(article)
                        cached_source_rows.append(dict(row))
                        matched_total += distribute_row(row, text_parts=[article.title, article.summary, article.text])
                    set_cached_rows(cache_payload, cache_key=cache_key, rows=cached_source_rows)
                    cache_dirty = True
                    source_health.append(
                        SourceHealthRecord(
                            source_group="baseline_rss",
                            source_key=source_key,
                            source_name=source.name,
                            ok=True,
                            elapsed_seconds=round(elapsed, 3),
                            fetched_count=len(articles),
                            matched_count=matched_total,
                            collected_at=_utc_now_iso(),
                            cache_hit=False,
                        ).to_dict()
                    )
                except Exception as exc:
                    source_name = RSS_SOURCES[source_key].name if source_key in RSS_SOURCES else "baseline_rss"
                    failures.append(f"{source_name}: {exc}")
                    source_health.append(
                        SourceHealthRecord(
                            source_group="baseline_rss",
                            source_key=source_key,
                            source_name=source_name,
                            ok=False,
                            elapsed_seconds=0.0,
                            fetched_count=0,
                            matched_count=0,
                            error=str(exc),
                            collected_at=_utc_now_iso(),
                        ).to_dict()
                    )
            elif future_type == "ticker_rss":
                try:
                    source_key, ticker, articles, elapsed = future.result()
                    source = RSS_SOURCES[source_key]
                    cache_key = f"baseline_rss:{source_key}:{ticker}"
                    cached_source_rows: list[dict[str, Any]] = []
                    matched_total = 0
                    for article in articles:
                        row = _article_to_row(article)
                        cached_source_rows.append(dict(row))
                        keywords = keywords_by_ticker[ticker]
                        if row_matcher([article.title, article.summary, article.text], keywords):
                            rows_by_ticker[ticker].append(row)
                            matched_total += 1
                    set_cached_rows(cache_payload, cache_key=cache_key, rows=cached_source_rows)
                    cache_dirty = True
                    source_health.append(
                        SourceHealthRecord(
                            source_group="baseline_rss",
                            source_key=source_key,
                            source_name=source.name,
                            ok=True,
                            elapsed_seconds=round(elapsed, 3),
                            fetched_count=len(articles),
                            matched_count=matched_total,
                            ticker=ticker,
                            collected_at=_utc_now_iso(),
                            cache_hit=False,
                        ).to_dict()
                    )
                except Exception as exc:
                    source_name = RSS_SOURCES[source_key].name if source_key in RSS_SOURCES else "baseline_rss"
                    label = f"{source_name} [{ticker}]" if ticker else source_name
                    failures.append(f"{label}: {exc}")
                    source_health.append(
                        SourceHealthRecord(
                            source_group="baseline_rss",
                            source_key=source_key,
                            source_name=source_name,
                            ok=False,
                            elapsed_seconds=0.0,
                            fetched_count=0,
                            matched_count=0,
                            ticker=ticker,
                            error=str(exc),
                            collected_at=_utc_now_iso(),
                        ).to_dict()
                    )
            elif future_type == "ticker_structured":
                try:
                    source_key, ticker, headlines, elapsed = future.result()
                    source = STRUCTURED_SOURCES[source_key]
                    cache_key = f"structured_news:{source_key}:{ticker}"
                    cached_source_rows: list[dict[str, Any]] = []
                    matched_total = 0
                    fetched_total = 0
                    keywords = keywords_by_ticker[ticker]
                    for headline in headlines:
                        row = _headline_to_row(headline)
                        cached_source_rows.append(dict(row))
                        if not include_seen and headline.link in seen_links:
                            continue
                        fetched_total += 1
                        if row_matcher([headline.title, headline.summary], keywords):
                            rows_by_ticker[ticker].append(row)
                            matched_total += 1
                            if headline.link:
                                newly_seen.add(headline.link)
                    set_cached_rows(cache_payload, cache_key=cache_key, rows=cached_source_rows)
                    cache_dirty = True
                    source_health.append(
                        SourceHealthRecord(
                            source_group="structured_news",
                            source_key=source_key,
                            source_name=source.name,
                            ok=True,
                            elapsed_seconds=round(elapsed, 3),
                            fetched_count=fetched_total,
                            matched_count=matched_total,
                            ticker=ticker,
                            collected_at=_utc_now_iso(),
                            cache_hit=False,
                        ).to_dict()
                    )
                except Exception as exc:
                    source_name = STRUCTURED_SOURCES[source_key].name if source_key in STRUCTURED_SOURCES else "structured_news"
                    label = f"{source_name} [{ticker}]" if ticker else source_name
                    failures.append(f"{label}: {exc}")
                    source_health.append(
                        SourceHealthRecord(
                            source_group="structured_news",
                            source_key=source_key,
                            source_name=source_name,
                            ok=False,
                            elapsed_seconds=0.0,
                            fetched_count=0,
                            matched_count=0,
                            ticker=ticker,
                            error=str(exc),
                            collected_at=_utc_now_iso(),
                        ).to_dict()
                    )
            else:
                try:
                    source_key, headlines, elapsed = future.result()
                    source = STRUCTURED_SOURCES[source_key]
                    cache_key = f"structured_news:{source_key}"
                    cached_source_rows: list[dict[str, Any]] = []
                    matched_total = 0
                    fetched_total = 0
                    for headline in headlines:
                        row = _headline_to_row(headline)
                        cached_source_rows.append(dict(row))
                        if not include_seen and headline.link in seen_links:
                            continue
                        fetched_total += 1
                        row_matches = distribute_row(row, text_parts=[headline.title, headline.summary])
                        matched_total += row_matches
                        if row_matches > 0:
                            newly_seen.add(headline.link)
                    set_cached_rows(cache_payload, cache_key=cache_key, rows=cached_source_rows)
                    cache_dirty = True
                    source_health.append(
                        SourceHealthRecord(
                            source_group="structured_news",
                            source_key=source_key,
                            source_name=source.name,
                            ok=True,
                            elapsed_seconds=round(elapsed, 3),
                            fetched_count=fetched_total,
                            matched_count=matched_total,
                            collected_at=_utc_now_iso(),
                            cache_hit=False,
                        ).to_dict()
                    )
                except Exception as exc:
                    source_name = STRUCTURED_SOURCES[source_key].name if source_key in STRUCTURED_SOURCES else "structured_news"
                    failures.append(f"{source_name}: {exc}")
                    source_health.append(
                        SourceHealthRecord(
                            source_group="structured_news",
                            source_key=source_key,
                            source_name=source_name,
                            ok=False,
                            elapsed_seconds=0.0,
                            fetched_count=0,
                            matched_count=0,
                            error=str(exc),
                            collected_at=_utc_now_iso(),
                        ).to_dict()
                    )

    if not include_seen:
        save_seen_links(state_file, newly_seen)
    if cache_dirty:
        save_source_cache(source_cache_file, cache_payload)

    return WatchlistSourceCollectionResult(
        rows_by_ticker=rows_by_ticker,
        failures=failures,
        source_health=source_health,
    )
