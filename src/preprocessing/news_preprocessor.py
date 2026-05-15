from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re
from urllib.parse import urlsplit, urlunsplit


GENERIC_CONTEXT_TERMS = {
    "ai",
    "gpu",
    "chip",
    "chips",
    "stock",
    "stocks",
    "market",
    "markets",
    "earnings",
    "shares",
    "price",
    "target",
}

COMPANY_SUFFIXES = {
    "inc",
    "inc.",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
    "group",
    "holdings",
    "ltd",
    "ltd.",
    "plc",
}

ROUNDUP_PATTERNS = (
    re.compile(r"\btop stories\b", re.IGNORECASE),
    re.compile(r"\btop midday stories\b", re.IGNORECASE),
    re.compile(r"\bstock market today\b", re.IGNORECASE),
    re.compile(r"\blive coverage\b", re.IGNORECASE),
    re.compile(r"\bmost active stocks\b", re.IGNORECASE),
    re.compile(r"\banalyst blog\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+out of\s+\d+\b", re.IGNORECASE),
    re.compile(r"\btop\s+\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+(stocks|companies|etfs|funds)\b", re.IGNORECASE),
)

RELATED_CONTEXT_PATTERNS = (
    re.compile(r"\bvs\b", re.IGNORECASE),
    re.compile(r"\bshare gains lead dow\b", re.IGNORECASE),
    re.compile(r"\bposts biggest gain\b", re.IGNORECASE),
    re.compile(r"\bmagnificent seven\b", re.IGNORECASE),
    re.compile(r"\bbig tech etf\b", re.IGNORECASE),
    re.compile(r"\binvestor backs startup\b", re.IGNORECASE),
    re.compile(r"\bstock portfolio\b", re.IGNORECASE),
    re.compile(r"\brally powers tech gains\b", re.IGNORECASE),
    re.compile(r"\brecord highs\b", re.IGNORECASE),
    re.compile(r"\bdow jones hits\b", re.IGNORECASE),
    re.compile(r"\bpower market indexes higher\b", re.IGNORECASE),
)

SOURCE_PRIORITY = {
    "yahoo_finance": 90,
    "prnewswire": 85,
    "globenewswire": 85,
    "accessnewswire": 85,
    "marketwatch_marketpulse": 80,
    "marketwatch_topstories": 70,
    "mtnewswires": 70,
    "finviz": 55,
    "sec_press_releases": 45,
}

EVENT_TYPE_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...], float], ...] = (
    (
        "executive_change",
        (
            re.compile(r"\bnew ceo\b", re.IGNORECASE),
            re.compile(r"\bnames new ceo\b", re.IGNORECASE),
            re.compile(r"\bappoints\b", re.IGNORECASE),
            re.compile(r"\bsteps? down\b", re.IGNORECASE),
            re.compile(r"\bchief executive\b", re.IGNORECASE),
            re.compile(r"\bchairman\b", re.IGNORECASE),
        ),
        1.0,
    ),
    (
        "analyst_rating_or_target",
        (
            re.compile(r"\bprice target\b", re.IGNORECASE),
            re.compile(r"\bhikes? .*target\b", re.IGNORECASE),
            re.compile(r"\btarget to\b", re.IGNORECASE),
            re.compile(r"\bupgrade\b", re.IGNORECASE),
            re.compile(r"\bdowngrade\b", re.IGNORECASE),
            re.compile(r"\bmore cautious\b", re.IGNORECASE),
            re.compile(r"\boutperform\b", re.IGNORECASE),
            re.compile(r"\boverweight\b", re.IGNORECASE),
            re.compile(r"\banalyst\b", re.IGNORECASE),
        ),
        0.9,
    ),
    (
        "earnings_or_guidance",
        (
            re.compile(r"\bearnings\b", re.IGNORECASE),
            re.compile(r"\bguidance\b", re.IGNORECASE),
            re.compile(r"\bquarter\b", re.IGNORECASE),
            re.compile(r"\bq[1-4]\b", re.IGNORECASE),
            re.compile(r"\bbeat-and-raise\b", re.IGNORECASE),
            re.compile(r"\boutlook\b", re.IGNORECASE),
            re.compile(r"\brevenue\b", re.IGNORECASE),
        ),
        1.0,
    ),
    (
        "regulatory_or_geopolitical",
        (
            re.compile(r"\bapproved?\b", re.IGNORECASE),
            re.compile(r"\bapproval\b", re.IGNORECASE),
            re.compile(r"\bchina\b", re.IGNORECASE),
            re.compile(r"\btariffs?\b", re.IGNORECASE),
            re.compile(r"\bexport controls?\b", re.IGNORECASE),
            re.compile(r"\btrump\b", re.IGNORECASE),
            re.compile(r"\bxi\b", re.IGNORECASE),
            re.compile(r"\bgovernment\b", re.IGNORECASE),
            re.compile(r"\bgeopolitical\b", re.IGNORECASE),
        ),
        0.95,
    ),
    (
        "product_or_strategy",
        (
            re.compile(r"\blaunch(?:es|ed)?\b", re.IGNORECASE),
            re.compile(r"\badopt\b", re.IGNORECASE),
            re.compile(r"\bcollaboration\b", re.IGNORECASE),
            re.compile(r"\bpartner(?:s|ship)?\b", re.IGNORECASE),
            re.compile(r"\bexpand collaboration\b", re.IGNORECASE),
            re.compile(r"\bchip production\b", re.IGNORECASE),
            re.compile(r"\bdeal\b", re.IGNORECASE),
            re.compile(r"\bproduction on track\b", re.IGNORECASE),
            re.compile(r"\bstreaming standard\b", re.IGNORECASE),
            re.compile(r"\bdemand stays strong\b", re.IGNORECASE),
        ),
        0.85,
    ),
    (
        "market_reaction",
        (
            re.compile(r"\bstock hits\b", re.IGNORECASE),
            re.compile(r"\bstock extends\b", re.IGNORECASE),
            re.compile(r"\bvaluation\b", re.IGNORECASE),
            re.compile(r"\bshare gains\b", re.IGNORECASE),
            re.compile(r"\bstock surges\b", re.IGNORECASE),
            re.compile(r"\bwinning streak\b", re.IGNORECASE),
            re.compile(r"\brecord peak\b", re.IGNORECASE),
            re.compile(r"\brecord\b", re.IGNORECASE),
        ),
        0.7,
    ),
    (
        "comparison_or_context",
        (
            re.compile(r"\bvs\b", re.IGNORECASE),
            re.compile(r"\brevenge on\b", re.IGNORECASE),
            re.compile(r"\bdominated market\b", re.IGNORECASE),
            re.compile(r"\bweek ahead\b", re.IGNORECASE),
            re.compile(r"\bin focus\b", re.IGNORECASE),
            re.compile(r"\bother .* names\b", re.IGNORECASE),
        ),
        0.5,
    ),
)


@dataclass(slots=True)
class TickerProfile:
    ticker: str
    company_name: str
    identity_terms: tuple[str, ...]
    specific_context_terms: tuple[str, ...]
    generic_context_terms: tuple[str, ...]


def _normalize_phrase(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if " " in term:
        escaped = escaped.replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def _tokenize_company(company_name: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", company_name.lower())
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in COMPANY_SUFFIXES
    ]


def build_ticker_profile(
    ticker: str,
    company_name: str = "",
    extra_keywords: list[str] | None = None,
) -> TickerProfile:
    identity_terms: set[str] = {_normalize_phrase(ticker)}
    normalized_company = _normalize_phrase(company_name)
    if normalized_company:
        identity_terms.add(normalized_company)
        identity_terms.update(_tokenize_company(normalized_company))

    specific_context_terms: set[str] = set()
    generic_context_terms: set[str] = set()
    for keyword in extra_keywords or []:
        normalized = _normalize_phrase(keyword)
        if not normalized:
            continue
        if normalized in identity_terms:
            continue
        if normalized in GENERIC_CONTEXT_TERMS:
            generic_context_terms.add(normalized)
        else:
            specific_context_terms.add(normalized)

    return TickerProfile(
        ticker=ticker.upper(),
        company_name=company_name,
        identity_terms=tuple(sorted(identity_terms)),
        specific_context_terms=tuple(sorted(specific_context_terms)),
        generic_context_terms=tuple(sorted(generic_context_terms)),
    )


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    haystack = text or ""
    matched: list[str] = []
    for term in terms:
        if term and _term_pattern(term).search(haystack):
            matched.append(term)
    return matched


def _canonicalize_link(link: str) -> str:
    if not link:
        return ""
    parsed = urlsplit(link)
    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), normalized_path, "", ""))


def _normalize_title_key(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    return " ".join(cleaned.split())


def _source_priority(row: dict[str, Any]) -> int:
    return SOURCE_PRIORITY.get(str(row.get("source_key", "")), 50)


def _is_roundup_noise(title: str) -> bool:
    return any(pattern.search(title or "") for pattern in ROUNDUP_PATTERNS)


def _is_related_context_title(title: str) -> bool:
    return any(pattern.search(title or "") for pattern in RELATED_CONTEXT_PATTERNS)


def _classify_event(title: str, summary: str) -> tuple[str, float]:
    for event_type, patterns, weight in EVENT_TYPE_PATTERNS:
        if any(pattern.search(title or "") for pattern in patterns):
            return event_type, weight

    haystack = summary or ""
    for event_type, patterns, weight in EVENT_TYPE_PATTERNS:
        if any(pattern.search(haystack) for pattern in patterns):
            return event_type, weight
    return "general_company_focus", 0.75


def score_row_relevance(row: dict[str, Any], profile: TickerProfile) -> dict[str, Any]:
    title = str(row.get("title", ""))
    summary = str(row.get("summary", ""))

    matched_identity_title = _matched_terms(title, profile.identity_terms)
    matched_identity_summary = _matched_terms(summary, profile.identity_terms)
    matched_specific_title = _matched_terms(title, profile.specific_context_terms)
    matched_specific_summary = _matched_terms(summary, profile.specific_context_terms)
    matched_generic_title = _matched_terms(title, profile.generic_context_terms)
    matched_generic_summary = _matched_terms(summary, profile.generic_context_terms)

    identity_hits = set(matched_identity_title + matched_identity_summary)
    specific_hits = set(matched_specific_title + matched_specific_summary)
    generic_hits = set(matched_generic_title + matched_generic_summary)

    score = 0.0
    score += len(matched_identity_title) * 7.0
    score += len(matched_identity_summary) * 4.0
    score += len(matched_specific_title) * 4.0
    score += len(matched_specific_summary) * 2.0
    score += len(matched_generic_title) * 1.0
    score += len(matched_generic_summary) * 0.5

    if row.get("source_key") == "yahoo_finance":
        score += 2.0
    if row.get("source_key") in {"prnewswire", "globenewswire", "accessnewswire"}:
        score += 1.0
    if row.get("source_key") == "finviz":
        score -= 1.0

    reasons: list[str] = []
    is_roundup = _is_roundup_noise(title)
    if is_roundup:
        score -= 6.0
        reasons.append("broad_roundup_pattern")

    has_identity = bool(identity_hits)
    has_specific_context = bool(specific_hits)
    has_strong_title_signal = bool(matched_identity_title or matched_specific_title)

    accepted = True
    if score < 7.0:
        accepted = False
        reasons.append("low_relevance_score")
    if not has_identity and not has_specific_context:
        accepted = False
        reasons.append("no_company_specific_signal")
    if not has_strong_title_signal:
        accepted = False
        reasons.append("no_title_focus")
    if is_roundup and not has_strong_title_signal:
        accepted = False
        reasons.append("roundup_without_focus")
    if row.get("source_key") in {"finviz", "marketwatch_topstories", "marketwatch_marketpulse", "sec_press_releases"}:
        if not has_strong_title_signal and len(identity_hits) < 2:
            accepted = False
            reasons.append("weak_aggregator_match")

    event_type, event_importance_weight = _classify_event(title, summary)

    return {
        **row,
        "relevance_score": round(score, 2),
        "event_type": event_type,
        "event_importance_weight": event_importance_weight,
        "matched_identity_terms": sorted(identity_hits),
        "matched_specific_terms": sorted(specific_hits),
        "matched_generic_terms": sorted(generic_hits),
        "accepted": accepted,
        "rejection_reasons": reasons,
        "canonical_link": _canonicalize_link(str(row.get("link", ""))),
        "normalized_title_key": _normalize_title_key(title),
    }


def cluster_relevant_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    title_key_counts: dict[str, int] = {}
    for row in rows:
        title_key = str(row.get("normalized_title_key", ""))
        if title_key:
            title_key_counts[title_key] = title_key_counts.get(title_key, 0) + 1

    clusters: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []

    for row in rows:
        title_key = str(row.get("normalized_title_key", ""))
        canonical_link = str(row.get("canonical_link", ""))
        if title_key and title_key_counts.get(title_key, 0) > 1:
            cluster_key = f"title:{title_key}"
        else:
            cluster_key = canonical_link or title_key or row.get("link") or row.get("title")
        if cluster_key not in clusters:
            clusters[cluster_key] = []
            order.append(cluster_key)
        clusters[cluster_key].append(row)

    clustered_rows: list[dict[str, Any]] = []
    for cluster_key in order:
        members = clusters[cluster_key]
        canonical = max(
            members,
            key=lambda item: (
                float(item.get("relevance_score", 0.0)),
                _source_priority(item),
            ),
        )
        coverage_sources = sorted({str(item.get("source_name", "")) for item in members if item.get("source_name")})
        coverage_source_keys = sorted({str(item.get("source_key", "")) for item in members if item.get("source_key")})
        duplicate_links = sorted({str(item.get("canonical_link", "")) for item in members if item.get("canonical_link")})

        clustered_rows.append(
            {
                **canonical,
                "coverage_count": len(members),
                "coverage_sources": coverage_sources,
                "coverage_source_keys": coverage_source_keys,
                "cluster_members": len(members),
                "merged_titles": sorted({str(item.get("title", "")) for item in members if item.get("title")}),
                "duplicate_links": duplicate_links,
                "signal_strength": round(
                    float(canonical.get("relevance_score", 0.0))
                    * float(canonical.get("event_importance_weight", 0.75))
                    * (1 + 0.15 * (len(members) - 1)),
                    2,
                ),
            }
        )

    return clustered_rows


def _should_keep_as_related_context(row: dict[str, Any]) -> bool:
    reasons = set(row.get("rejection_reasons", []))
    if not row.get("matched_identity_terms"):
        return False
    if "broad_roundup_pattern" in reasons or "roundup_without_focus" in reasons:
        return False
    if "no_title_focus" not in reasons:
        return False
    if float(row.get("relevance_score", 0.0)) < 8.0:
        return False
    return "weak_aggregator_match" not in reasons


def _should_keep_as_review_candidate(row: dict[str, Any]) -> bool:
    reasons = set(row.get("rejection_reasons", []))
    score = float(row.get("relevance_score", 0.0))

    if "broad_roundup_pattern" in reasons or "roundup_without_focus" in reasons:
        return False
    if score < 4.0:
        return False
    if row.get("matched_identity_terms"):
        return True
    if row.get("matched_specific_terms"):
        return True
    return False


def preprocess_ticker_news(
    rows: list[dict[str, Any]],
    profile: TickerProfile,
) -> dict[str, Any]:
    scored_rows = [score_row_relevance(row, profile) for row in rows]
    primary_rows: list[dict[str, Any]] = []
    related_rows: list[dict[str, Any]] = []
    review_candidates: list[dict[str, Any]] = []

    for row in scored_rows:
        if not row["accepted"]:
            continue
        title = str(row.get("title", ""))
        if _is_related_context_title(title) or row.get("event_type") == "comparison_or_context":
            related_rows.append({**row, "related_context_reason": "title_pattern"})
        else:
            primary_rows.append(row)

    related_rows.extend(
        [
            {**row, "related_context_reason": "summary_only_identity_match"}
            for row in scored_rows
            if not row["accepted"] and _should_keep_as_related_context(row)
        ]
    )
    related_row_keys = {
        (
            str(row.get("canonical_link", "")),
            str(row.get("normalized_title_key", "")),
            str(row.get("title", "")),
        )
        for row in related_rows
    }
    for row in scored_rows:
        if row["accepted"]:
            continue
        row_key = (
            str(row.get("canonical_link", "")),
            str(row.get("normalized_title_key", "")),
            str(row.get("title", "")),
        )
        if row_key in related_row_keys:
            continue
        if _should_keep_as_review_candidate(row):
            review_candidates.append({**row, "review_candidate_reason": "borderline_but_relevant"})

    review_candidate_keys = {
        (
            str(row.get("canonical_link", "")),
            str(row.get("normalized_title_key", "")),
            str(row.get("title", "")),
        )
        for row in review_candidates
    }
    rejected_rows = [
        row
        for row in scored_rows
        if not row["accepted"]
        and (
            str(row.get("canonical_link", "")),
            str(row.get("normalized_title_key", "")),
            str(row.get("title", "")),
        )
        not in related_row_keys
        and (
            str(row.get("canonical_link", "")),
            str(row.get("normalized_title_key", "")),
            str(row.get("title", "")),
        )
        not in review_candidate_keys
    ]
    clustered_rows = cluster_relevant_rows(primary_rows)

    clustered_rows.sort(
        key=lambda row: (
            float(row.get("signal_strength", 0.0)),
            row.get("coverage_count", 1),
        ),
        reverse=True,
    )

    return {
        "profile": {
            "ticker": profile.ticker,
            "company_name": profile.company_name,
            "identity_terms": list(profile.identity_terms),
            "specific_context_terms": list(profile.specific_context_terms),
            "generic_context_terms": list(profile.generic_context_terms),
        },
        "stats": {
            "raw_rows": len(rows),
            "accepted_rows_before_dedupe": len(primary_rows),
            "clustered_story_count": len(clustered_rows),
            "related_context_rows": len(related_rows),
            "review_candidate_rows": len(review_candidates),
            "rejected_rows": len(rejected_rows),
            "duplicates_merged": max(len(primary_rows) - len(clustered_rows), 0),
        },
        "stories": clustered_rows,
        "related_context": related_rows,
        "review_candidates": review_candidates,
        "rejections": rejected_rows,
    }
