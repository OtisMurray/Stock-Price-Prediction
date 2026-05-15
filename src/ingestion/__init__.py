"""Helpers for collecting raw market and news data."""

from .fetch_source_url import FetchedPage, fetch_url_with_fallback
from .models import Article
from .structured_collectors import StructuredHeadline, collect_structured_headlines
from .structured_sources import PUBLIC_STRUCTURED_SOURCE_KEYS, get_structured_source

try:
    from .rss_collectors import build_keywords, collect_baseline_articles, filter_articles
    from .rss_sources import DEFAULT_BASELINE_SOURCE_KEYS, get_sources
except ImportError:
    DEFAULT_BASELINE_SOURCE_KEYS = []  # type: ignore[assignment]

    def build_keywords(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ImportError("RSS dependencies are not installed. Install requirements.txt first.")

    def collect_baseline_articles(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ImportError("RSS dependencies are not installed. Install requirements.txt first.")

    def filter_articles(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ImportError("RSS dependencies are not installed. Install requirements.txt first.")

    def get_sources(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ImportError("RSS dependencies are not installed. Install requirements.txt first.")

__all__ = [
    "FetchedPage",
    "Article",
    "DEFAULT_BASELINE_SOURCE_KEYS",
    "PUBLIC_STRUCTURED_SOURCE_KEYS",
    "StructuredHeadline",
    "build_keywords",
    "collect_baseline_articles",
    "collect_structured_headlines",
    "fetch_url_with_fallback",
    "filter_articles",
    "get_sources",
    "get_structured_source",
]
