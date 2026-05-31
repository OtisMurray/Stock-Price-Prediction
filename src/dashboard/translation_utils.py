from __future__ import annotations

from functools import lru_cache
import json
import re
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
COMMON_FOREIGN_HINTS = {
    "les",
    "des",
    "pour",
    "avec",
    "dans",
    "ouvre",
    "portes",
    "compte",
    "jour",
    "jours",
    "und",
    "die",
    "der",
    "las",
    "los",
    "para",
    "con",
    "una",
    "uno",
}


def _contains_non_ascii_letter(text: str) -> bool:
    for char in text:
        if ord(char) <= 127:
            continue
        if unicodedata.category(char).startswith("L"):
            return True
    return False


def likely_non_english(text: str) -> bool:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return False
    if _contains_non_ascii_letter(cleaned):
        return True
    words = [word.lower() for word in LATIN_WORD_RE.findall(cleaned)]
    if len(words) < 4:
        return False
    return sum(1 for word in words if word in COMMON_FOREIGN_HINTS) >= 2


@lru_cache(maxsize=512)
def translate_text_to_english(text: str) -> dict[str, str]:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return {
            "source_language": "",
            "target_language": "en",
            "translated_text": "",
        }

    query = urlencode(
        {
            "client": "gtx",
            "sl": "auto",
            "tl": "en",
            "dt": "t",
            "dj": "1",
            "q": cleaned,
        }
    )
    url = f"https://translate.googleapis.com/translate_a/single?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))

    translated_text = " ".join(
        sentence.get("trans", "").strip()
        for sentence in payload.get("sentences", [])
        if sentence.get("trans")
    ).strip()

    return {
        "source_language": str(payload.get("src", "") or ""),
        "target_language": "en",
        "translated_text": translated_text or cleaned,
    }
