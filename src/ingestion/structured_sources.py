from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StructuredSource:
    key: str
    name: str
    homepage_url: str
    collection_url: str
    access_type: str
    first_method: str
    parser_type: str
    is_premium: bool = False
    selector_candidates: tuple[str, ...] = ()
    article_href_contains: tuple[str, ...] = ()
    notes: str = ""
    link_prefix: str = ""
    rss_url: str = ""
    use_rss_first: bool = False
    json_url: str = ""
    json_query: dict[str, str] = field(default_factory=dict)


STRUCTURED_SOURCES: dict[str, StructuredSource] = {
    "prnewswire": StructuredSource(
        key="prnewswire",
        name="PR Newswire",
        homepage_url="https://www.prnewswire.com/",
        collection_url="https://www.prnewswire.com/rss/news-releases-list.rss",
        access_type="Public RSS and public website",
        first_method="RSS first",
        parser_type="feedparser, BeautifulSoup fallback",
        rss_url="https://www.prnewswire.com/rss/news-releases-list.rss",
        use_rss_first=True,
        notes="Strong first implementation source because official RSS support is public.",
    ),
    "globenewswire": StructuredSource(
        key="globenewswire",
        name="GlobeNewswire",
        homepage_url="https://www.globenewswire.com/en",
        collection_url=(
            "https://www.globenewswire.com/RssFeed/subjectcode/"
            "39-Stock%20Market%20News/feedTitle/GlobeNewswire%20-%20Stock%20Market%20News"
        ),
        access_type="Public RSS feed and public newsroom pages",
        first_method="RSS first",
        parser_type="feedparser, BeautifulSoup fallback",
        rss_url=(
            "https://www.globenewswire.com/RssFeed/subjectcode/"
            "39-Stock%20Market%20News/feedTitle/GlobeNewswire%20-%20Stock%20Market%20News"
        ),
        use_rss_first=True,
        notes="Structured press-release source. Stock Market News RSS feed is public and better than HTML scraping here.",
    ),
    "accessnewswire": StructuredSource(
        key="accessnewswire",
        name="ACCESS Newswire",
        homepage_url="https://www.accessnewswire.com/",
        collection_url="https://www.accessnewswire.com/newsroom",
        access_type="Public newsroom pages",
        first_method="JSON feed first",
        parser_type="JSON API, BeautifulSoup fallback",
        selector_candidates=(
            "a[href*='/news-release/']",
            "a[href*='/news/']",
            "section a[href*='/news-release/']",
            "section a[href*='/news/']",
            "article a[href*='/news-release/']",
            "article a[href*='/news/']",
            "h3 a",
            "h2 a",
        ),
        article_href_contains=("/news-release/", "/news/"),
        json_url="https://www.accessnewswire.com/newsroom/api",
        json_query={},
        notes="Public structured source. ACCESS all-news cards are powered by a CSRF-protected JSON endpoint behind the public newsroom page.",
    ),
    "mtnewswires": StructuredSource(
        key="mtnewswires",
        name="MT Newswires",
        homepage_url="https://www.mtnewswires.com/",
        collection_url="https://www.mtnewswires.com/news/",
        access_type="Public company news page, premium core feed",
        first_method="HTML listing for public site only",
        parser_type="BeautifulSoup",
        selector_candidates=("a[href*='/news/']",),
        article_href_contains=("/news/",),
        notes="Public site can expose company news posts, but the real market feed is premium.",
    ),
    "dowjones": StructuredSource(
        key="dowjones",
        name="Dow Jones Newswires",
        homepage_url="https://www.dowjones.com/professional/newswires/",
        collection_url="https://www.dowjones.com/professional/newswires/",
        access_type="Professional/licensed distribution",
        first_method="Treat as premium unless access is granted",
        parser_type="Premium feed or licensed integration",
        is_premium=True,
        notes="High-value source, but likely not suitable for free/public collection.",
    ),
    "finviz": StructuredSource(
        key="finviz",
        name="Finviz",
        homepage_url="https://finviz.com/",
        collection_url="https://finviz.com/news.ashx?v=3",
        access_type="Public aggregator page",
        first_method="HTML page collection",
        parser_type="BeautifulSoup",
        selector_candidates=(
            "a[href*='news.ashx']",
            "table a",
            "a",
        ),
        notes="Useful aggregator and cross-check source, but not the only source because of timing lag.",
    ),
}


PUBLIC_STRUCTURED_SOURCE_KEYS = [
    "prnewswire",
    "globenewswire",
    "accessnewswire",
    "mtnewswires",
    "finviz",
]


def get_structured_source(key: str) -> StructuredSource:
    if key not in STRUCTURED_SOURCES:
        raise KeyError(f"Unknown structured source key: {key}")
    return STRUCTURED_SOURCES[key]
