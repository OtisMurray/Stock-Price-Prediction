from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from html import unescape
import json
import re
from typing import Iterable
from urllib.parse import urljoin, urlencode
from urllib.request import Request, urlopen

from .fetch_source_url import fetch_url_with_fallback
from .structured_sources import StructuredSource, get_structured_source


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


def _normalize_text(text: str) -> str:
    return " ".join(unescape(text or "").split())


def _is_probable_headline(text: str) -> bool:
    clean = _normalize_text(text)
    if len(clean) < 18 or len(clean) > 260:
        return False
    if clean.lower() in NOISE_EXACT:
        return False
    if clean.count(" ") < 2:
        return False
    return True


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


def _headline_from_access_public_json(source: StructuredSource, limit: int | None) -> list[StructuredHeadline]:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:
        raise RuntimeError("curl_cffi is required for ACCESS Newswire public API collection.") from exc

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

    page_size = 20
    if limit:
        page_size = min(max(limit, 1), 100)

    headlines: list[StructuredHeadline] = []
    page_index = 0
    while True:
        response = session.post(
            f"{source.json_url}?pageindex={page_index}&pageSize={page_size}",
            headers=headers,
            timeout=20,
        )
        payload = response.json()
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

        results.append(
            StructuredHeadline(
                source_key=source.key,
                source_name=source.name,
                title=title,
                link=full_link,
                published="",
                summary=summary[:400],
                collection_method="html",
                notes=source.notes,
            )
        )
        if limit and len(results) >= limit:
            break

    return results


def collect_structured_headlines(source_key: str, limit: int | None = 15) -> list[StructuredHeadline]:
    source = get_structured_source(source_key)
    if source.is_premium:
        raise RuntimeError(
            f"{source.name} is marked as a premium source and is not configured for public headline collection."
        )

    if source.use_rss_first:
        return _headline_from_feed(source, limit)
    if source.json_url:
        return _headline_from_json(source, limit)
    return _headline_from_html(source, limit)
