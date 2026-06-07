from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any

from src.analysis.sentiment import prepare_finbert_payload
from src.dashboard.translation_utils import likely_non_english, translate_text_to_english
from src.ingestion.fetch_source_url import fetch_url_with_fallback


MIN_ENRICHED_SUMMARY_CHARS = 40
MAX_ENRICHED_SUMMARY_CHARS = 900
BOILERPLATE_MARKERS = (
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "cookie",
    "subscribe",
    "sign up",
    "read more",
)


@dataclass(slots=True)
class ArticleTextEnrichment:
    ok: bool
    status: str
    title: str = ""
    summary: str = ""
    source_language: str = ""
    translated_title: str = ""
    translated_summary: str = ""
    fetch_url: str = ""
    final_url: str = ""
    fetch_method: str = ""
    text_chars: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _strip_html(value: Any) -> str:
    return _clean_text(re.sub(r"<[^>]+>", " ", str(value or "")))


def _first_meta_content(soup: Any, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = _clean_text(node.get("content", "") or node.get_text(" ", strip=True))
            if value:
                return _strip_html(value)
    return ""


def _jsonld_objects(soup: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                objects.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
    return objects


def _jsonld_article_text(soup: Any) -> tuple[str, str]:
    for item in _jsonld_objects(soup):
        raw_type = item.get("@type", "")
        types = {str(raw_type).lower()} if not isinstance(raw_type, list) else {str(value).lower() for value in raw_type}
        if not types.intersection({"article", "newsarticle", "blogposting", "report"}):
            continue
        title = _clean_text(item.get("headline") or item.get("name"))
        body = _strip_html(item.get("articleBody") or "")
        description = _strip_html(item.get("description") or "")
        summary = body or description
        if title or summary:
            return title, summary
    return "", ""


def _paragraph_summary(soup: Any) -> str:
    containers = soup.select("article, main, .article-body, .article-content, .html-content, .story-body")
    nodes = []
    for container in containers:
        nodes.extend(container.select("p, blockquote"))
    if not nodes:
        nodes = soup.select("p")

    parts: list[str] = []
    for node in nodes:
        text = _clean_text(node.get_text(" ", strip=True))
        lowered = text.lower()
        if len(text) < 35:
            continue
        if any(marker in lowered for marker in BOILERPLATE_MARKERS):
            continue
        parts.append(text)
        if sum(len(part) for part in parts) >= MAX_ENRICHED_SUMMARY_CHARS:
            break
    return _clean_text(" ".join(parts))[:MAX_ENRICHED_SUMMARY_CHARS].strip()


def extract_article_text_from_html(html: str) -> tuple[str, str]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:
        raise RuntimeError("beautifulsoup4 is required for article text enrichment.") from exc

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    jsonld_title, jsonld_summary = _jsonld_article_text(soup)
    meta_title = _first_meta_content(
        soup,
        (
            "meta[property='og:title'][content]",
            "meta[name='twitter:title'][content]",
            "meta[itemprop='headline'][content]",
            "meta[itemprop='name'][content]",
        ),
    )
    meta_summary = _first_meta_content(
        soup,
        (
            "meta[property='og:description'][content]",
            "meta[name='twitter:description'][content]",
            "meta[name='description'][content]",
            "meta[itemprop='description'][content]",
        ),
    )
    h1_title = _clean_text(soup.select_one("h1").get_text(" ", strip=True)) if soup.select_one("h1") else ""
    paragraph_summary = _paragraph_summary(soup)

    title = jsonld_title or meta_title or h1_title
    summary = jsonld_summary or meta_summary or paragraph_summary
    return _clean_text(title), _clean_text(summary)


def fetch_article_text(url: str) -> ArticleTextEnrichment:
    clean_url = _clean_text(url)
    if not clean_url:
        return ArticleTextEnrichment(ok=False, status="missing_url")
    try:
        page = fetch_url_with_fallback(clean_url, timeout=20)
        title, summary = extract_article_text_from_html(page.html)
    except Exception as exc:
        return ArticleTextEnrichment(ok=False, status="fetch_or_parse_failed", fetch_url=clean_url, error=str(exc)[:300])

    text_chars = len(_clean_text(f"{title} {summary}"))
    if not title and len(summary) < MIN_ENRICHED_SUMMARY_CHARS:
        return ArticleTextEnrichment(
            ok=False,
            status="no_article_text_found",
            fetch_url=clean_url,
            final_url=page.final_url,
            fetch_method=page.fetch_method,
            text_chars=text_chars,
        )
    return ArticleTextEnrichment(
        ok=True,
        status="parsed",
        title=title,
        summary=summary,
        fetch_url=clean_url,
        final_url=page.final_url,
        fetch_method=page.fetch_method,
        text_chars=text_chars,
    )


def enrich_article_for_finbert(
    article: dict[str, Any],
    *,
    fetch_missing_text: bool = False,
    translate_non_english: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    enriched = dict(article)
    initial_payload = prepare_finbert_payload(enriched)
    report: dict[str, Any] = {
        "initial_ready": bool(initial_payload.get("finbert_ready")),
        "initial_reason": str(initial_payload.get("finbert_readiness_reason", "")),
        "fetched_article_text": False,
        "translated_text": False,
        "final_ready": bool(initial_payload.get("finbert_ready")),
        "final_reason": str(initial_payload.get("finbert_readiness_reason", "")),
    }

    should_fetch = fetch_missing_text and str(initial_payload.get("finbert_readiness_reason", "")) in {
        "missing_text",
        "insufficient_text",
    }
    if should_fetch:
        fetch_url = str(enriched.get("link", "") or enriched.get("canonical_link", "") or "")
        extraction = fetch_article_text(fetch_url)
        report["article_text_enrichment"] = extraction.to_dict()
        if extraction.ok:
            if extraction.title and not _clean_text(enriched.get("title")):
                enriched["title"] = extraction.title
            if extraction.summary and len(_clean_text(enriched.get("summary"))) < MIN_ENRICHED_SUMMARY_CHARS:
                enriched["summary"] = extraction.summary
            enriched["article_text_enriched"] = True
            enriched["article_text_enrichment_status"] = extraction.status
            enriched["article_text_fetch_method"] = extraction.fetch_method
            report["fetched_article_text"] = True

    combined_text = _clean_text(f"{enriched.get('title', '')} {enriched.get('summary', '')}")
    should_translate = translate_non_english and combined_text and (
        bool(enriched.get("needs_translation")) or likely_non_english(combined_text)
    )
    if should_translate:
        title = _clean_text(enriched.get("title"))
        summary = _clean_text(enriched.get("summary"))
        try:
            title_result = translate_text_to_english(title) if title else {"source_language": "", "translated_text": ""}
            summary_result = translate_text_to_english(summary) if summary else {"source_language": "", "translated_text": ""}
            enriched["title_translated"] = title_result.get("translated_text", "") or title
            enriched["summary_translated"] = summary_result.get("translated_text", "") or summary
            enriched["needs_translation"] = True
            report["translated_text"] = True
            report["source_language"] = (
                title_result.get("source_language", "") or summary_result.get("source_language", "")
            )
        except Exception as exc:
            report["translation_error"] = str(exc)[:300]

    final_payload = prepare_finbert_payload(enriched)
    report["final_ready"] = bool(final_payload.get("finbert_ready"))
    report["final_reason"] = str(final_payload.get("finbert_readiness_reason", ""))
    return enriched, report
