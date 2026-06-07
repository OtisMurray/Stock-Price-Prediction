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
    is_ticker_specific: bool = False
    collection_url_template: str = ""
    source_family: str = "structured"
    quality_tier: str = "secondary_structured"

    def build_collection_url(self, *, ticker: str = "", exchange: str = "", symbol: str = "") -> str:
        template = self.collection_url_template or self.collection_url
        if any(token in template for token in ("{ticker}", "{exchange}", "{symbol}")):
            if not ticker and not symbol:
                raise ValueError(f"{self.name} requires a ticker symbol.")
            resolved_symbol = symbol or f"{exchange}:{ticker.upper()}".strip(":")
            return template.format(
                ticker=ticker.upper(),
                exchange=exchange.upper(),
                symbol=resolved_symbol,
            )
        return template


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
        quality_tier="secondary_structured",
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
        quality_tier="secondary_structured",
    ),
    "accessnewswire": StructuredSource(
        key="accessnewswire",
        name="ACCESS Newswire",
        homepage_url="https://www.accessnewswire.com/",
        collection_url="https://www.accessnewswire.com/newsroom",
        access_type="Monitor only: direct ACCESS collection is Cloudflare-blocked; ACCESS-origin stories still surface through aggregators.",
        first_method="Excluded from active dashboard collection",
        parser_type="Monitor-only source definition",
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
        notes=(
            "Direct ACCESS collection is excluded from the active dashboard because the public API is blocked "
            "by Cloudflare. ACCESS-origin stories are still captured indirectly through broader sources such "
            "as TradingView and Finviz when they syndicate them."
        ),
        quality_tier="monitor_only",
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
        quality_tier="monitor_only",
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
        quality_tier="monitor_only",
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
        quality_tier="secondary_structured",
    ),
    "tradingview": StructuredSource(
        key="tradingview",
        name="TradingView News Flow",
        homepage_url="https://www.tradingview.com/",
        collection_url="https://www.tradingview.com/news-flow/",
        collection_url_template="https://www.tradingview.com/news-flow/?symbol={symbol}",
        access_type="Public symbol news pages and public news-mediator endpoint",
        first_method="TradingView symbol news mediator API",
        parser_type="JSON API with impersonated page fallback",
        is_ticker_specific=True,
        notes=(
            "Primary ticker-specific source. Uses TradingView's public symbol-news flow through "
            "the news-mediator endpoint so articles surface earlier than Yahoo Finance RSS."
        ),
        quality_tier="primary_structured",
    ),
    "stocktwits": StructuredSource(
        key="stocktwits",
        name="Stocktwits News",
        homepage_url="https://stocktwits.com/",
        collection_url="https://stocktwits.com/",
        collection_url_template="https://stocktwits.com/symbol/{ticker}",
        access_type="Public symbol page with embedded article JSON",
        first_method="Embedded __NEXT_DATA__ article payload",
        parser_type="Embedded JSON",
        is_ticker_specific=True,
        notes="Supplementary ticker-specific source pulled from public Stocktwits symbol pages.",
        source_family="unstructured",
        quality_tier="supplemental_unstructured",
    ),
}


PUBLIC_STRUCTURED_SOURCE_KEYS = [
    "tradingview",
    "stocktwits",
    "prnewswire",
    "globenewswire",
    "mtnewswires",
    "finviz",
]


def get_structured_source(key: str) -> StructuredSource:
    if key not in STRUCTURED_SOURCES:
        raise KeyError(f"Unknown structured source key: {key}")
    return STRUCTURED_SOURCES[key]
