from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RSSSource:
    key: str
    name: str
    url_template: str
    is_ticker_specific: bool = False
    notes: str = ""

    def build_url(self, ticker: str | None = None) -> str:
        if "{ticker}" in self.url_template:
            if not ticker:
                raise ValueError(f"{self.name} requires a ticker symbol.")
            return self.url_template.format(ticker=ticker.upper())
        return self.url_template


RSS_SOURCES: dict[str, RSSSource] = {
    "yahoo_finance": RSSSource(
        key="yahoo_finance",
        name="Yahoo Finance Headlines",
        url_template="https://finance.yahoo.com/rss/headline?s={ticker}",
        is_ticker_specific=True,
        notes="Ticker-specific Yahoo Finance RSS feed.",
    ),
    "marketwatch_topstories": RSSSource(
        key="marketwatch_topstories",
        name="MarketWatch Top Stories",
        url_template="https://feeds.content.dowjones.io/public/rss/mw_topstories",
        notes="General MarketWatch feed; keyword filtering matters.",
    ),
    "marketwatch_marketpulse": RSSSource(
        key="marketwatch_marketpulse",
        name="MarketWatch MarketPulse",
        url_template="https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
        notes="Short market-moving updates from MarketWatch.",
    ),
    "sec_press_releases": RSSSource(
        key="sec_press_releases",
        name="SEC Press Releases",
        url_template="https://www.sec.gov/news/pressreleases.rss",
        notes="Official SEC press release RSS feed.",
    ),
    "prnewswire_all_news": RSSSource(
        key="prnewswire_all_news",
        name="PR Newswire All News Releases",
        url_template="https://www.prnewswire.com/rss/news-releases-list.rss",
        notes=(
            "General PR Newswire feed. If the endpoint changes, confirm the latest raw "
            "RSS URL from PR Newswire's RSS directory."
        ),
    ),
}

DEFAULT_BASELINE_SOURCE_KEYS = [
    "yahoo_finance",
    "marketwatch_topstories",
    "marketwatch_marketpulse",
    "sec_press_releases",
    "prnewswire_all_news",
]


def get_sources(keys: list[str] | None = None) -> list[RSSSource]:
    selected_keys = keys or DEFAULT_BASELINE_SOURCE_KEYS
    unknown_keys = [key for key in selected_keys if key not in RSS_SOURCES]
    if unknown_keys:
        raise KeyError(f"Unknown source keys: {', '.join(unknown_keys)}")
    return [RSS_SOURCES[key] for key in selected_keys]
