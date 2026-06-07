from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from html import unescape
import json
import re
from typing import Iterable
from urllib.parse import urljoin, urlencode
from urllib.request import Request, urlopen

from .fetch_source_url import fetch_url_with_fallback
from .structured_sources import StructuredSource, get_structured_source
from .timestamp_utils import is_us_equity_market_open, parse_published_datetime


@dataclass(slots=True)
class StructuredHeadline:
    source_key: str
    source_name: str
    title: str
    link: str
    published: str
    summary: str
    collection_method: str
    notes: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


NOISE_EXACT = {
    "",
    "search",
    "log in",
    "login",
    "contact",
    "learn more",
    "view all",
    "read more",
    "request a demo",
    "get started",
    "newsroom",
    "home",
}

DATEISH_RE = re.compile(
    r"(" 
    r"\b\d{1,2}\s*(?:min|mins|minute|minutes|hour|hours|day|days)\b"
    r"|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|"
    r"\b\d{4}-\d{2}-\d{2}(?:[t\s]\d{2}:\d{2}(?::\d{2})?)?(?:z)?\b"
    r")",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")

NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(?P<payload>\{.*?\})\s*</script>',
    re.IGNORECASE | re.DOTALL,
)

KNOWN_NYSE_TICKERS = {
    "ABBV",
    "BA",
    "BAC",
    "CAT",
    "COST",
    "CRM",
    "CVX",
    "DE",
    "DIS",
    "GE",
    "GS",
    "HD",
    "JPM",
    "KO",
    "LLY",
    "LOW",
    "MA",
    "MCD",
    "MRK",
    "MS",
    "ORCL",
    "PEP",
    "PFE",
    "PG",
    "SBUX",
    "SHOP",
    "T",
    "TGT",
    "TSM",
    "UBER",
    "UNH",
    "V",
    "WMT",
    "XOM",
}

KNOWN_AMEX_TICKERS = {
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
}

TRADINGVIEW_EXCHANGE_OVERRIDES = {
    "GPW": ("GPW", "NASDAQ", "NYSE", "AMEX"),
}

FINVIZ_MARKET_WINDOW_MINUTES = 30
TRADINGVIEW_MARKET_WINDOW_MINUTES = 30
FAST_TICKER_FETCH_TIMEOUT_SECONDS = 4
TRADINGVIEW_SYMBOL_EXCHANGE_CACHE: dict[str, str] = {}
STOCKTWITS_UNSUPPORTED_TICKERS: set[str] = set()


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text or "").split())


def _normalize_html_text(text: str) -> str:
    return _normalize_text(HTML_TAG_RE.sub(" ", text or ""))


def _exchange_candidates_for_ticker(ticker: str) -> tuple[str, ...]:
    normalized = ticker.upper()
    cached_exchange = TRADINGVIEW_SYMBOL_EXCHANGE_CACHE.get(normalized)
    if cached_exchange:
        fallback = TRADINGVIEW_EXCHANGE_OVERRIDES.get(normalized)
        if fallback:
            ordered = [cached_exchange]
            ordered.extend(exchange for exchange in fallback if exchange != cached_exchange)
            return tuple(ordered)
        if normalized in KNOWN_NYSE_TICKERS:
            return (cached_exchange, "NYSE", "NASDAQ", "AMEX")
        if normalized in KNOWN_AMEX_TICKERS:
            return (cached_exchange, "AMEX", "NASDAQ", "NYSE")
        return (cached_exchange, "NASDAQ", "NYSE", "AMEX")
    if normalized in TRADINGVIEW_EXCHANGE_OVERRIDES:
        return TRADINGVIEW_EXCHANGE_OVERRIDES[normalized]
    if normalized in KNOWN_NYSE_TICKERS:
        return ("NYSE", "NASDAQ", "AMEX")
    if normalized in KNOWN_AMEX_TICKERS:
        return ("AMEX", "NASDAQ", "NYSE")
    return ("NASDAQ", "NYSE", "AMEX")


def _is_probable_headline(text: str) -> bool:
    clean = _normalize_text(text)
    if len(clean) < 18 or len(clean) > 260:
        return False
    if clean.lower() in NOISE_EXACT:
        return False
    if clean.count(" ") < 2:
        return False
    return True


def _extract_dateish_text(text: str) -> str:
    clean = _normalize_text(text)
    match = DATEISH_RE.search(clean)
    return match.group(1) if match else ""


def _extract_published_from_anchor(source: StructuredSource, anchor) -> str:
    # Finviz stores relative time in the left table cell on the same row.
    if source.key == "finviz":
        row = anchor.find_parent("tr")
        if row:
            date_cell = row.select_one("td.news_date-cell")
            if date_cell:
                return _normalize_text(date_cell.get_text(" ", strip=True))

    # MT Newswires exposes a card-level date block.
    if source.key == "mtnewswires":
        article = anchor.find_parent("article")
        if article:
            date_block = article.select_one(".dt-sec")
            if date_block:
                return _normalize_text(date_block.get_text(" ", strip=True))

    # Generic HTML fallback: try nearby <time> tags, datetime attrs, and date-like text blocks.
    current = anchor
    for _ in range(5):
        if not current:
            break
        time_tag = current.find("time")
        if time_tag:
            return _normalize_text(time_tag.get("datetime", "") or time_tag.get_text(" ", strip=True))

        descendants = current.find_all(True, limit=25)
        for node in descendants:
            datetime_attr = node.get("datetime") if hasattr(node, "get") else None
            if datetime_attr:
                return _normalize_text(datetime_attr)
            classes = " ".join(node.get("class", [])) if hasattr(node, "get") else ""
            if any(token in classes.lower() for token in ("date", "time", "meta")):
                extracted = _extract_dateish_text(node.get_text(" ", strip=True))
                if extracted:
                    return extracted
        current = current.parent

    return ""


def _should_keep_finviz_headline(published: str, *, collected_at: str) -> bool:
    if not is_us_equity_market_open():
        return True
    published_dt = parse_published_datetime(published, collected_at=collected_at)
    if published_dt is None:
        return True
    collected_dt = parse_published_datetime(collected_at, collected_at=collected_at) or datetime.now(timezone.utc)
    return published_dt >= collected_dt - timedelta(minutes=FINVIZ_MARKET_WINDOW_MINUTES)


def _should_keep_tradingview_headline(published: str, *, collected_at: str) -> bool:
    if not is_us_equity_market_open():
        return True
    published_dt = parse_published_datetime(published, collected_at=collected_at)
    if published_dt is None:
        return True
    collected_dt = parse_published_datetime(collected_at, collected_at=collected_at) or datetime.now(timezone.utc)
    return published_dt >= collected_dt - timedelta(minutes=TRADINGVIEW_MARKET_WINDOW_MINUTES)


def _is_transient_stocktwits_symbol_error(message: str) -> bool:
    normalized = (message or "").lower()
    transient_markers = (
        "http error 403",
        "403 client error",
        "timed out",
        "timeout",
        "read operation timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "remote end closed connection",
    )
    return any(marker in normalized for marker in transient_markers)


def _headline_from_feed(source: StructuredSource, limit: int | None) -> list[StructuredHeadline]:
    import feedparser

    feed = feedparser.parse(source.rss_url or source.collection_url)
    headlines: list[StructuredHeadline] = []
    entries = feed.entries if not limit else feed.entries[:limit]
    for entry in entries:
        title = _normalize_text(entry.get("title", ""))
        if not _is_probable_headline(title):
            continue
        headlines.append(
            StructuredHeadline(
                source_key=source.key,
                source_name=source.name,
                title=title,
                link=entry.get("link", ""),
                published=entry.get("published", "") or entry.get("updated", ""),
                summary=_normalize_text(entry.get("summary", "") or entry.get("description", "")),
                collection_method="rss",
                notes=source.notes,
            )
        )
    return headlines


def _headline_from_json(source: StructuredSource, limit: int | None) -> list[StructuredHeadline]:
    if not source.json_url:
        return []
    if source.key == "accessnewswire":
        return _headline_from_access_public_json(source, limit)

    query = dict(source.json_query)
    current_year = date.today().year
    query.setdefault("start", f"{current_year}-01-01")
    query.setdefault("end", f"{current_year}-12-31")
    url = f"{source.json_url}?{urlencode(query)}"
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    news_groups = payload.get("results", {}).get("news", [])
    headlines: list[StructuredHeadline] = []

    for group in news_groups:
        for item in group.get("newsitem", []):
            vendor = _normalize_text(item.get("source", ""))
            if source.key == "accessnewswire" and "access newswire" not in vendor.lower():
                continue

            title = _normalize_text(item.get("headline", ""))
            if not _is_probable_headline(title):
                continue

            headlines.append(
                StructuredHeadline(
                    source_key=source.key,
                    source_name=source.name,
                    title=title,
                    link=item.get("storyurl", ""),
                    published=item.get("datetime", ""),
                    summary=_normalize_text(item.get("qmsummary", "")),
                    collection_method="json",
                    notes=source.notes,
                )
            )
            if limit and len(headlines) >= limit:
                return headlines

    return headlines


def _headline_from_newswire_public_html(source: StructuredSource, limit: int | None) -> list[StructuredHeadline]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:
        raise RuntimeError("beautifulsoup4 is required for Newswire.com HTML fallback parsing.") from exc

    newsroom_urls = (
        "https://www.newswire.com/newsroom/business-finance",
        "https://www.newswire.com/newsroom/business",
        "https://www.newswire.com/newsroom",
    )
    requested_limit = max(limit or 20, 1)
    results: list[StructuredHeadline] = []
    seen_links: set[str] = set()
    errors: list[str] = []

    for newsroom_url in newsroom_urls:
        try:
            page = fetch_url_with_fallback(newsroom_url)
        except Exception as exc:
            errors.append(f"{newsroom_url}: {exc}")
            continue

        soup = BeautifulSoup(page.html, "html.parser")
        for card in soup.select(".news-item[itemscope], .news-item"):
            title_node = card.select_one(".content-link h3, h3")
            link_node = card.select_one("a.content-link[href], a.more-btn[href], a[href*='/news/']")
            title = _normalize_text(title_node.get_text(" ", strip=True) if title_node else "")
            href = str(link_node.get("href", "") if link_node else "")
            if not href or not _is_probable_headline(title):
                continue

            full_link = urljoin("https://www.newswire.com/", href)
            if full_link in seen_links:
                continue
            seen_links.add(full_link)

            published = ""
            time_node = card.select_one("time[datetime]")
            if time_node:
                published = _normalize_text(str(time_node.get("datetime", "")) or time_node.get_text(" ", strip=True))
            if not published:
                published_meta = card.select_one("meta[itemprop='datePublished'][content]")
                if published_meta:
                    published = _normalize_text(str(published_meta.get("content", "")))

            summary = ""
            description_meta = None
            for meta_node in card.select("meta[itemprop='description'][content]"):
                content = str(meta_node.get("content", "") or "")
                normalized_content = _normalize_html_text(content).lower()
                if "text with textual alternatives" in normalized_content:
                    continue
                if "<" in content or len(normalized_content) >= 40:
                    description_meta = meta_node
                    break
            if description_meta:
                summary = _normalize_html_text(str(description_meta.get("content", "")))

            results.append(
                StructuredHeadline(
                    source_key=source.key,
                    source_name=source.name,
                    title=title,
                    link=full_link,
                    published=published,
                    summary=summary,
                    collection_method="html_fallback",
                    notes=f"{source.notes} Newswire.com public newsroom fallback.",
                )
            )
            if len(results) >= requested_limit:
                return results

    if results:
        return results
    joined_errors = "; ".join(errors) if errors else "no public Newswire.com newsroom cards found"
    raise RuntimeError(f"ACCESS Newswire fallback failed: {joined_errors}")


def _headline_from_access_json_api(source: StructuredSource, limit: int | None) -> list[StructuredHeadline]:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError("curl_cffi is required for ACCESS Newswire public API collection.") from exc

    def build_session_headers() -> tuple[Any, dict[str, str]]:
        session = curl_requests.Session(impersonate="chrome124")
        page = session.get(source.collection_url, timeout=20)
        token_match = re.search(
            r'<input name="AntiforgeryFieldname" type="hidden" value="([^"]+)"',
            page.text,
        )
        if not token_match:
            raise RuntimeError("ACCESS Newswire anti-forgery token was not found on the newsroom page.")

        csrf_token = token_match.group(1)
        headers = {
            "Referer": source.collection_url,
            "Origin": source.homepage_url.rstrip("/"),
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN-HEADERNAME": csrf_token,
            "account": "1",
            "Accept": "application/json, text/plain, */*",
        }
        return session, headers

    session, headers = build_session_headers()

    page_size = 20
    if limit:
        page_size = min(max(limit, 1), 100)

    headlines: list[StructuredHeadline] = []
    page_index = 0
    while True:
        payload = None
        for attempt in range(2):
            response = session.post(
                f"{source.json_url}?pageindex={page_index}&pageSize={page_size}",
                headers=headers,
                timeout=20,
            )
            if response.status_code != 200:
                detail = _normalize_text(response.text)[:240]
                raise RuntimeError(
                    f"ACCESS Newswire public API returned HTTP {response.status_code}: {detail}"
                )
            try:
                payload = response.json()
                break
            except ValueError:
                # ACCESS occasionally returns an empty or malformed body for a
                # valid newsroom request. Refresh the anti-forgery token once,
                # then degrade to an empty result instead of marking the entire
                # source as failed for a transient API hiccup.
                if attempt == 0:
                    session, headers = build_session_headers()
                    continue
                return headlines

        if not isinstance(payload, dict):
            return headlines
        data = payload.get("data", {})
        articles = data.get("articles", [])
        for article in articles:
            title = _normalize_text(article.get("title", ""))
            if not _is_probable_headline(title):
                continue
            headlines.append(
                StructuredHeadline(
                    source_key=source.key,
                    source_name=source.name,
                    title=title,
                    link=article.get("releaseurl", ""),
                    published=article.get("adate", ""),
                    summary=_normalize_text(article.get("body", "")),
                    collection_method="json",
                    notes=source.notes,
                )
            )
            if limit and len(headlines) >= limit:
                return headlines

        page_count = payload.get("pageCount", 1) or 1
        page_index = payload.get("pageIndex", page_index) + 1
        if not articles or page_index >= page_count:
            break

    return headlines


def _headline_from_access_public_json(source: StructuredSource, limit: int | None) -> list[StructuredHeadline]:
    try:
        headlines = _headline_from_access_json_api(source, limit)
        if headlines:
            return headlines
    except Exception:
        return _headline_from_newswire_public_html(source, limit)
    return _headline_from_newswire_public_html(source, limit)


def _candidate_anchors(source: StructuredSource, soup) -> Iterable:
    seen = set()
    for selector in source.selector_candidates:
        for anchor in soup.select(selector):
            ident = id(anchor)
            if ident not in seen:
                seen.add(ident)
                yield anchor
    if not source.selector_candidates:
        yield from soup.find_all("a")


def _headline_from_html(source: StructuredSource, limit: int | None) -> list[StructuredHeadline]:
    page = fetch_url_with_fallback(source.collection_url)
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:
        raise RuntimeError("beautifulsoup4 is required for HTML source parsing.") from exc

    soup = BeautifulSoup(page.html, "html.parser")
    results: list[StructuredHeadline] = []
    seen_links: set[str] = set()
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    for anchor in _candidate_anchors(source, soup):
        href = anchor.get("href")
        title = _normalize_text(anchor.get_text(" ", strip=True))
        if not href or not _is_probable_headline(title):
            continue

        full_link = urljoin(source.homepage_url, href)
        if source.article_href_contains and not any(part in full_link for part in source.article_href_contains):
            if source.key != "finviz":
                continue

        if full_link in seen_links:
            continue
        seen_links.add(full_link)

        parent_text = _normalize_text(anchor.parent.get_text(" ", strip=True)) if anchor.parent else ""
        summary = parent_text if parent_text != title else ""
        published = _extract_published_from_anchor(source, anchor)
        if source.key == "finviz" and not _should_keep_finviz_headline(published, collected_at=collected_at):
            continue

        results.append(
            StructuredHeadline(
                source_key=source.key,
                source_name=source.name,
                title=title,
                link=full_link,
                published=published,
                summary=summary[:400],
                collection_method="html",
                notes=source.notes,
            )
        )
        if limit and len(results) >= limit:
            break

    return results


def _headline_from_stocktwits_symbol(
    source: StructuredSource,
    *,
    ticker: str,
    limit: int | None,
) -> list[StructuredHeadline]:
    normalized_ticker = ticker.upper()
    if normalized_ticker in STOCKTWITS_UNSUPPORTED_TICKERS:
        return []
    try:
        page = fetch_url_with_fallback(
            source.build_collection_url(ticker=ticker),
            timeout=FAST_TICKER_FETCH_TIMEOUT_SECONDS,
            methods=("curl_cffi", "urllib"),
        )
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP 404" in message or "404 Client Error" in message:
            STOCKTWITS_UNSUPPORTED_TICKERS.add(normalized_ticker)
            return []
        if _is_transient_stocktwits_symbol_error(message):
            return []
        raise
    match = NEXT_DATA_RE.search(page.html)
    if not match:
        return []

    payload = json.loads(match.group("payload"))
    articles = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("initialData", {})
        .get("articles", [])
    )
    headlines: list[StructuredHeadline] = []

    for article in articles:
        title = _normalize_text(article.get("headline", ""))
        if not _is_probable_headline(title):
            continue
        link = (
            article.get("canonical_url")
            or article.get("url")
            or (
                f"{source.homepage_url.rstrip('/')}/news-articles/{article.get('url_slug', '').lstrip('/')}"
                if article.get("url_slug")
                else ""
            )
        )
        headlines.append(
            StructuredHeadline(
                source_key=source.key,
                source_name=source.name,
                title=title,
                link=link,
                published=str(article.get("created_at", "")),
                summary=_normalize_text(article.get("summary", "")),
                collection_method="embedded_json",
                notes=source.notes,
            )
        )
        if limit and len(headlines) >= limit:
            break

    return headlines


def _tradingview_mediator_url(symbol: str) -> str:
    query = urlencode(
        [
            ("filter", "lang:en"),
            ("filter", f"symbol:{symbol}"),
            ("client", "web"),
            ("user_prostatus", "non_pro"),
        ],
        doseq=True,
    )
    return f"https://news-mediator.tradingview.com/public/view/v1/symbol?{query}"


def _headline_from_tradingview_symbol(
    source: StructuredSource,
    *,
    ticker: str,
    limit: int | None,
) -> list[StructuredHeadline]:
    errors: list[str] = []
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    for exchange in _exchange_candidates_for_ticker(ticker):
        symbol = f"{exchange}:{ticker.upper()}"
        try:
            payload = json.loads(
                fetch_url_with_fallback(
                    _tradingview_mediator_url(symbol),
                    timeout=FAST_TICKER_FETCH_TIMEOUT_SECONDS,
                    methods=("curl_cffi", "urllib"),
                ).html
            )
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            continue

        items = payload.get("items", [])
        fresh_headlines: list[StructuredHeadline] = []
        fallback_headlines: list[StructuredHeadline] = []
        for item in items:
            title = _normalize_text(item.get("title", ""))
            if not _is_probable_headline(title):
                continue
            published_text = str(item.get("published", ""))
            story_path = str(item.get("storyPath", ""))
            link = item.get("link") or urljoin(source.homepage_url, story_path)
            provider_name = _normalize_text(item.get("provider", {}).get("name", "TradingView"))
            headline = StructuredHeadline(
                source_key=source.key,
                source_name=source.name,
                title=title,
                link=link,
                published=published_text,
                summary=provider_name,
                collection_method="json_api",
                notes=f"{source.notes} Provider: {provider_name}.",
            )
            fallback_headlines.append(headline)
            if _should_keep_tradingview_headline(published_text, collected_at=collected_at):
                fresh_headlines.append(headline)
            if limit and len(fallback_headlines) >= limit:
                if len(fresh_headlines) >= limit:
                    break

        if fresh_headlines:
            TRADINGVIEW_SYMBOL_EXCHANGE_CACHE[ticker.upper()] = exchange
            return fresh_headlines[:limit] if limit else fresh_headlines

        if fallback_headlines:
            TRADINGVIEW_SYMBOL_EXCHANGE_CACHE[ticker.upper()] = exchange
            broader_rows: list[StructuredHeadline] = []
            for headline in fallback_headlines[:limit] if limit else fallback_headlines:
                broader_rows.append(
                    StructuredHeadline(
                        source_key=headline.source_key,
                        source_name=headline.source_name,
                        title=headline.title,
                        link=headline.link,
                        published=headline.published,
                        summary=headline.summary,
                        collection_method=headline.collection_method,
                        notes=f"{headline.notes} Outside preferred {TRADINGVIEW_MARKET_WINDOW_MINUTES}-minute market-hours window.",
                    )
                )
            return broader_rows

    joined = "; ".join(errors) if errors else "no TradingView headlines returned"
    raise RuntimeError(f"TradingView symbol flow failed for {ticker.upper()}: {joined}")


def collect_structured_headlines(
    source_key: str,
    limit: int | None = 15,
    *,
    ticker: str = "",
) -> list[StructuredHeadline]:
    source = get_structured_source(source_key)
    if source.is_premium:
        raise RuntimeError(
            f"{source.name} is marked as a premium source and is not configured for public headline collection."
        )

    if source.is_ticker_specific:
        if not ticker:
            raise RuntimeError(f"{source.name} requires a ticker symbol.")
        if source.key == "tradingview":
            return _headline_from_tradingview_symbol(source, ticker=ticker, limit=limit)
        if source.key == "stocktwits":
            return _headline_from_stocktwits_symbol(source, ticker=ticker, limit=limit)
        return _headline_from_html(source, limit)

    if source.use_rss_first:
        return _headline_from_feed(source, limit)
    if source.json_url:
        return _headline_from_json(source, limit)
    return _headline_from_html(source, limit)
