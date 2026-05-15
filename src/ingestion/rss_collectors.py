from __future__ import annotations

import html
import re

import feedparser

from .models import Article
from .rss_sources import get_sources

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def strip_markup(text: str) -> str:
    no_tags = TAG_RE.sub(" ", text or "")
    unescaped = html.unescape(no_tags)
    return SPACE_RE.sub(" ", unescaped).strip()


def build_keywords(
    ticker: str,
    company_name: str | None = None,
    extra_keywords: list[str] | None = None,
) -> list[str]:
    keywords = {ticker.lower()}
    if company_name:
        keywords.add(company_name.lower())
    if extra_keywords:
        keywords.update(keyword.lower() for keyword in extra_keywords if keyword)
    return sorted(keywords)


def fetch_rss_source(
    source_key: str,
    ticker: str | None = None,
    limit_per_source: int | None = None,
) -> list[Article]:
    source = get_sources([source_key])[0]
    feed = feedparser.parse(source.build_url(ticker=ticker))
    articles: list[Article] = []
    entries = feed.entries if limit_per_source in (None, 0) else feed.entries[:limit_per_source]

    for entry in entries:
        title = strip_markup(entry.get("title", ""))
        summary = strip_markup(entry.get("summary", "") or entry.get("description", ""))
        text = ". ".join(part for part in [title, summary] if part)
        articles.append(
            Article(
                source_key=source.key,
                source_name=source.name,
                title=title,
                summary=summary,
                link=entry.get("link", ""),
                published=entry.get("published", "") or entry.get("updated", ""),
                text=text,
                metadata={
                    "feed_url": source.build_url(ticker=ticker),
                    "notes": source.notes,
                },
            )
        )

    return articles


def collect_baseline_articles(
    ticker: str,
    source_keys: list[str] | None = None,
    limit_per_source: int | None = None,
) -> list[Article]:
    articles: list[Article] = []
    for source in get_sources(source_keys):
        articles.extend(
            fetch_rss_source(
                source_key=source.key,
                ticker=ticker if source.is_ticker_specific else None,
                limit_per_source=limit_per_source,
            )
        )
    return articles


def filter_articles(articles: list[Article], keywords: list[str]) -> list[Article]:
    return [article for article in articles if article.matches_keywords(keywords)]
