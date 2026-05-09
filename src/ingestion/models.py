from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Article:
    """Normalized article record shared across collectors and sentiment scripts."""

    source_key: str
    source_name: str
    title: str
    summary: str
    link: str
    published: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def matches_keywords(self, keywords: list[str]) -> bool:
        if not keywords:
            return True
        haystack = self.text.lower()
        return any(keyword.lower() in haystack for keyword in keywords)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
