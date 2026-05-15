from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class FetchedPage:
    url: str
    final_url: str
    status_code: int
    fetch_method: str
    title: str
    text: str
    html: str


def _clean_text(text: str) -> str:
    no_tags = TAG_RE.sub(" ", text)
    unescaped = html.unescape(no_tags)
    return SPACE_RE.sub(" ", unescaped).strip()


def _extract_title(raw_html: str) -> str:
    match = TITLE_RE.search(raw_html)
    if not match:
        return ""
    return _clean_text(match.group(1))


def _extract_text(raw_html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return SPACE_RE.sub(" ", soup.get_text(" ", strip=True)).strip()
    except ImportError:
        return _clean_text(raw_html)


def _build_result(
    *,
    url: str,
    final_url: str,
    status_code: int,
    fetch_method: str,
    raw_html: str,
) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=final_url,
        status_code=status_code,
        fetch_method=fetch_method,
        title=_extract_title(raw_html),
        text=_extract_text(raw_html),
        html=raw_html,
    )


def _fetch_with_urllib(url: str, timeout: int) -> FetchedPage:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw_html = response.read().decode("utf-8", errors="replace")
        final_url = response.geturl()
        status_code = getattr(response, "status", 200)
    return _build_result(
        url=url,
        final_url=final_url,
        status_code=status_code,
        fetch_method="urllib",
        raw_html=raw_html,
    )


def _fetch_with_cloudscraper(url: str, timeout: int) -> FetchedPage:
    import cloudscraper  # type: ignore

    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "darwin"})
    response = scraper.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return _build_result(
        url=url,
        final_url=response.url,
        status_code=response.status_code,
        fetch_method="cloudscraper",
        raw_html=response.text,
    )


def _fetch_with_curl_impersonation(url: str, timeout: int) -> FetchedPage:
    from curl_cffi import requests as curl_requests  # type: ignore

    response = curl_requests.get(
        url,
        timeout=timeout,
        impersonate="chrome",
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return _build_result(
        url=url,
        final_url=response.url,
        status_code=response.status_code,
        fetch_method="curl_cffi",
        raw_html=response.text,
    )


def fetch_url_with_fallback(url: str, timeout: int = 20) -> FetchedPage:
    fetchers = [
        ("urllib", _fetch_with_urllib),
        ("cloudscraper", _fetch_with_cloudscraper),
        ("curl_cffi", _fetch_with_curl_impersonation),
    ]
    errors: list[str] = []

    for name, fetcher in fetchers:
        try:
            return fetcher(url, timeout)
        except ImportError:
            errors.append(f"{name}: optional dependency not installed")
        except HTTPError as exc:
            errors.append(f"{name}: HTTP {exc.code}")
        except URLError as exc:
            errors.append(f"{name}: URL error {exc.reason}")
        except Exception as exc:  # Broad on purpose for fallback flow.
            errors.append(f"{name}: {exc}")

    joined_errors = "; ".join(errors) if errors else "unknown fetch failure"
    raise RuntimeError(f"All fetch methods failed for {url}: {joined_errors}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a source page using normal requests first, then fallback methods such as "
            "CloudScraper and curl_cffi if available."
        )
    )
    parser.add_argument("--url", required=True, help="Target page URL to test.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=1200,
        help="How much extracted text to print.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = fetch_url_with_fallback(args.url, timeout=args.timeout)
    preview = result.text[: args.preview_chars].strip()

    print("Source page fetch result")
    print("=" * 60)
    print(f"Requested URL: {result.url}")
    print(f"Final URL: {result.final_url}")
    print(f"Status Code: {result.status_code}")
    print(f"Fetch Method: {result.fetch_method}")
    print(f"Title: {result.title or '[No title found]'}")
    print("-" * 60)
    print(preview or "[No text extracted]")


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    main()
