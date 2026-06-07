from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import os
import re
from typing import Any

FINBERT_MAX_INPUT_CHARS = 1500
MIN_FINBERT_INPUT_CHARS = 20
FINBERT_MODEL_NAME = "ProsusAI/finbert"
FINBERT_DEFAULT_DOWNLOAD_ENABLED = "0"


POSITIVE_PHRASES = {
    "beat estimates": 2.8,
    "beats estimates": 2.8,
    "beats on revenue": 2.3,
    "beats quarterly sales estimates": 2.5,
    "topped estimates": 2.4,
    "tops estimates": 2.4,
    "record revenue": 2.4,
    "record profit": 2.4,
    "strong revenue": 1.8,
    "solid q2 boost": 1.8,
    "solid boost": 1.4,
    "signals solid": 1.4,
    "signals solid q2 boost": 2.1,
    "profit growth": 1.8,
    "revenue growth": 1.8,
    "raises dividend": 2.0,
    "raises quarterly dividend": 2.3,
    "increase in quarterly cash dividend": 2.2,
    "announces increase in quarterly cash dividend": 2.3,
    "dividend increase": 1.8,
    "maintained at buy": 2.0,
    "initiated at buy": 2.0,
    "maintained at outperform": 2.0,
    "upgraded to buy": 2.1,
    "price target maintained": 1.2,
    "hikes s&p 500 target": 1.6,
    "raises guidance": 2.0,
    "boosts guidance": 2.0,
    "expands coverage": 1.7,
    "coverage expands": 1.7,
    "coverage for": 1.1,
    "wins contract": 1.7,
    "strong demand": 1.5,
    "surge in demand": 1.6,
    "rallies on": 1.8,
    "trading up today": 1.9,
    "stock is trading up today": 1.9,
    "stock soaring today": 2.0,
    "stock soars": 2.0,
    "jumps as": 1.8,
    "jumps on": 1.8,
    "rise on strong growth": 2.1,
    "expands partnership": 1.3,
    "approved by fda": 2.1,
    "fda approval": 2.0,
}

NEGATIVE_PHRASES = {
    "misses estimates": 2.8,
    "missed estimates": 2.8,
    "cuts guidance": 2.5,
    "cut guidance": 2.5,
    "cuts price target": 2.2,
    "cut price target": 2.2,
    "price target lowered": 1.8,
    "price target lower": 1.8,
    "sell rating": 2.0,
    "class action": 2.5,
    "default risk": 2.5,
    "shareholder proposals failed": 1.0,
    "under investigation": 2.1,
    "faces probe": 2.1,
    "supply constraints": 1.3,
    "production delay": 1.8,
    "cuts dividend": 2.2,
    "dividend cut": 2.2,
    "pull back": 1.0,
    "cut bank jobs": 2.0,
    "avoid spot": 1.2,
}

POSITIVE_TERMS = {
    "bullish": 1.2,
    "strong": 0.7,
    "upside": 0.8,
    "upgrade": 1.0,
    "buy": 0.6,
    "growth": 0.5,
    "record": 0.6,
    "profit": 0.6,
    "revenue": 0.3,
    "approved": 0.8,
    "outperform": 0.9,
    "surge": 0.8,
    "expands": 0.5,
    "boost": 0.8,
    "beats": 0.9,
    "rally": 0.8,
    "rallies": 0.8,
    "jump": 0.8,
    "jumps": 0.8,
    "soaring": 1.0,
    "raises": 0.8,
    "dividend": 0.6,
}

NEGATIVE_TERMS = {
    "bearish": 1.2,
    "weak": 0.8,
    "risk": 0.8,
    "lawsuit": 1.2,
    "investigation": 1.1,
    "probe": 1.0,
    "miss": 0.9,
    "downgrade": 1.0,
    "delay": 0.8,
    "warning": 0.8,
    "decline": 0.8,
    "fall": 0.7,
    "drop": 0.7,
    "slip": 0.7,
    "cut": 0.7,
    "cuts": 0.8,
    "warns": 0.8,
    "avoiding": 0.6,
}

COMPANY_TOKEN_STOPWORDS = {
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
    "limited",
    "ltd",
    "ltd.",
    "plc",
    "the",
    "and",
}

SOURCE_CONFIDENCE_MULTIPLIER = {
    "primary_structured": 1.16,
    "secondary_structured": 1.0,
    "supplemental_unstructured": 0.8,
    "monitor_only": 0.66,
}

SOURCE_WEIGHT = {
    "primary_structured": 1.0,
    "secondary_structured": 0.8,
    "supplemental_unstructured": 0.55,
    "monitor_only": 0.35,
}

EVENT_CONFIDENCE_MULTIPLIER = {
    "earnings_or_guidance": 1.16,
    "analyst_rating_or_target": 1.12,
    "executive_change": 1.03,
    "regulatory_or_geopolitical": 1.06,
    "product_or_strategy": 1.0,
    "market_reaction": 1.0,
    "general_company_focus": 0.9,
}

EVENT_RELEVANCE_MULTIPLIER = {
    "earnings_or_guidance": 1.08,
    "analyst_rating_or_target": 1.08,
    "executive_change": 1.05,
    "regulatory_or_geopolitical": 1.03,
    "general_company_focus": 0.95,
}

EVENT_SCORE_TILT = {
    "analyst_rating_or_target": 0.15,
    "earnings_or_guidance": 0.1,
}

TITLE_WEIGHT = 1.0
SUMMARY_WEIGHT = 0.55

POSITIVE_PHRASE_ITEMS = tuple(sorted(POSITIVE_PHRASES.items(), key=lambda item: len(item[0]), reverse=True))
NEGATIVE_PHRASE_ITEMS = tuple(sorted(NEGATIVE_PHRASES.items(), key=lambda item: len(item[0]), reverse=True))


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").lower().split())


@lru_cache(maxsize=512)
def _term_regex(term: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)


def _find_phrase_markers(text: str, weighted_markers: tuple[tuple[str, float], ...], *, weight_multiplier: float) -> tuple[list[str], float]:
    found: list[str] = []
    score = 0.0
    for marker, weight in weighted_markers:
        if marker in text:
            found.append(marker)
            score += float(weight) * weight_multiplier
    return found, score


def _find_term_markers(text: str, weighted_markers: dict[str, float], *, weight_multiplier: float) -> tuple[list[str], float]:
    found: list[str] = []
    score = 0.0
    for marker, weight in weighted_markers.items():
        if _term_regex(marker).search(text):
            found.append(marker)
            score += float(weight) * weight_multiplier
    return found, score


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _combined_article_text(article: dict[str, Any]) -> str:
    title = " ".join(str(article.get("title", "") or "").split())
    summary = " ".join(str(article.get("summary", "") or "").split())
    if summary and title and summary.lower().startswith(title.lower()):
        summary = summary[len(title) :].strip(" -:|")
    parts = [part for part in (title, summary) if part]
    return " [SEP] ".join(parts).strip()


def _finbert_metadata_context(article: dict[str, Any]) -> str:
    ticker = str(article.get("ticker", "") or "").strip().upper()
    matched_tickers = [
        str(value).strip().upper()
        for value in article.get("matched_tickers", []) or []
        if str(value).strip()
    ]
    company = " ".join(str(article.get("company", "") or "").split())
    source_name = " ".join(str(article.get("source_name", "") or "").split())
    event_type = str(
        article.get("event_type", "")
        or ((article.get("event_types") or [""])[0] if isinstance(article.get("event_types"), list) else "")
    ).strip()
    primary_category = str(article.get("primary_category", "") or "").strip()

    context_parts: list[str] = []
    if company and ticker:
        context_parts.append(f"Company context: {company} ({ticker}).")
    elif company:
        context_parts.append(f"Company context: {company}.")
    elif ticker:
        context_parts.append(f"Ticker context: {ticker}.")
    elif matched_tickers:
        context_parts.append(f"Matched ticker context: {', '.join(matched_tickers[:5])}.")

    if event_type:
        context_parts.append(f"Event context: {event_type.replace('_', ' ')}.")
    if primary_category:
        context_parts.append(f"Category context: {primary_category.replace('_', ' ')}.")
    if source_name:
        context_parts.append(f"Source context: {source_name}.")

    return " ".join(context_parts).strip()


def prepare_finbert_payload(article: dict[str, Any]) -> dict[str, Any]:
    combined_text = _combined_article_text(article)
    needs_translation = bool(article.get("needs_translation"))
    translated_title = " ".join(str(article.get("title_translated", "") or "").split())
    translated_summary = " ".join(str(article.get("summary_translated", "") or "").split())
    translated_parts = [part for part in (translated_title, translated_summary) if part]
    model_text = " [SEP] ".join(translated_parts).strip() if translated_parts else combined_text
    normalized_model_text = " ".join(model_text.split())
    metadata_context = _finbert_metadata_context(article)
    metadata_context_added = False
    if len(normalized_model_text) < MIN_FINBERT_INPUT_CHARS and metadata_context:
        normalized_model_text = " [SEP] ".join(
            part for part in (normalized_model_text, metadata_context) if part
        )
        metadata_context_added = True
    if len(normalized_model_text) > FINBERT_MAX_INPUT_CHARS:
        normalized_model_text = normalized_model_text[: FINBERT_MAX_INPUT_CHARS - 1].rstrip() + "…"

    translation_ready = not needs_translation or bool(translated_parts)
    has_article_text = bool(combined_text or translated_parts)
    finbert_ready = has_article_text and translation_ready and len(normalized_model_text) >= MIN_FINBERT_INPUT_CHARS

    if not combined_text and not translated_parts:
        readiness_reason = "missing_text"
    elif needs_translation and not translated_parts:
        readiness_reason = "translation_pending"
    elif len(normalized_model_text) < MIN_FINBERT_INPUT_CHARS:
        readiness_reason = "insufficient_text"
    else:
        readiness_reason = "ready"

    return {
        "sentiment_pipeline_stage": "rule_based_baseline",
        "sentiment_model_used": "rule_based",
        "future_model_target": "FinBERT",
        "finbert_ready": finbert_ready,
        "finbert_readiness_reason": readiness_reason,
        "finbert_input_text": normalized_model_text,
        "finbert_input_length": len(normalized_model_text),
        "finbert_uses_translation": bool(translated_parts),
        "finbert_uses_metadata_context": metadata_context_added,
    }


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.environ.get(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _running_on_railway() -> bool:
    return bool(
        os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
        or os.environ.get("RAILWAY_ENVIRONMENT_ID")
    )


def _finbert_inference_context() -> str:
    return str(os.environ.get("FINBERT_INFERENCE_CONTEXT", "") or "").strip().lower()


def _railway_finbert_permitted() -> bool:
    if not _running_on_railway():
        return True
    if not _env_flag("ALLOW_RAILWAY_FINBERT"):
        return False
    return _finbert_inference_context() == "backfill"


def sentiment_runtime_status() -> dict[str, Any]:
    disabled = _env_flag("DISABLE_LOCAL_FINBERT")
    hosted_blocked = _running_on_railway() and not _railway_finbert_permitted()
    download_enabled = _env_flag("FINBERT_ALLOW_DOWNLOAD", FINBERT_DEFAULT_DOWNLOAD_ENABLED)
    return {
        "baseline_model": "rule_based",
        "target_model": "FinBERT",
        "finbert_model_name": FINBERT_MODEL_NAME,
        "finbert_enabled": not disabled and not hosted_blocked,
        "finbert_download_enabled": download_enabled,
        "finbert_runtime_mode": (
            "disabled"
            if disabled
            else "hosted_backfill_only"
            if hosted_blocked and _running_on_railway()
            else "hosted_backfill_only"
            if _running_on_railway() and _finbert_inference_context() != "backfill"
            else "hosted_backfill_enabled"
            if _running_on_railway()
            else "cache_or_download"
            if download_enabled
            else "local_cache_only"
        ),
    }


@lru_cache(maxsize=8)
def _load_finbert_components_cached(allow_download: bool, cache_home: str, transformers_cache: str) -> tuple[Any, Any] | None:
    if not allow_download:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
    if cache_home:
        os.environ["HF_HOME"] = cache_home
    if transformers_cache:
        os.environ["TRANSFORMERS_CACHE"] = transformers_cache
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except Exception:
        return None

    load_attempts = [True] if not allow_download else [True, False]
    for local_only in load_attempts:
        try:
            os.environ["HF_HUB_OFFLINE"] = "1" if local_only else "0"
            os.environ["TRANSFORMERS_OFFLINE"] = "1" if local_only else "0"
            if local_only:
                os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
            tokenizer = AutoTokenizer.from_pretrained(FINBERT_MODEL_NAME, local_files_only=local_only)
            model = AutoModelForSequenceClassification.from_pretrained(
                FINBERT_MODEL_NAME,
                local_files_only=local_only,
                use_safetensors=False,
            )
            model.eval()
            return tokenizer, model
        except Exception:
            continue
    return None


def _load_finbert_components() -> tuple[Any, Any] | None:
    if _env_flag("DISABLE_LOCAL_FINBERT"):
        return None
    if not _railway_finbert_permitted():
        return None

    allow_download = _env_flag("FINBERT_ALLOW_DOWNLOAD", FINBERT_DEFAULT_DOWNLOAD_ENABLED)
    cache_home = str(os.environ.get("HF_HOME", "") or "")
    transformers_cache = str(os.environ.get("TRANSFORMERS_CACHE", "") or "")
    return _load_finbert_components_cached(allow_download, cache_home, transformers_cache)


def score_finbert_sentiment(text: str) -> dict[str, Any] | None:
    components = _load_finbert_components()
    if not components:
        return None

    tokenizer, model = components
    try:
        import torch

        encoded = tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
        )
        with torch.no_grad():
            logits = model(**encoded).logits
            probabilities = torch.softmax(logits, dim=-1)[0].tolist()
    except Exception:
        return None

    labels = [label.lower() for _, label in sorted(model.config.id2label.items())]
    scores = dict(zip(labels, probabilities, strict=False))
    positive = float(scores.get("positive", 0.0))
    negative = float(scores.get("negative", 0.0))
    neutral = float(scores.get("neutral", 0.0))
    dominant_label = max(scores.items(), key=lambda item: item[1])[0] if scores else "neutral"
    signed_score = max(min(positive - negative, 1.0), -1.0)
    return {
        "label": dominant_label,
        "score": round(signed_score, 3),
        "confidence": round(max(positive, negative, neutral), 3),
        "positive_probability": round(positive, 3),
        "negative_probability": round(negative, 3),
        "neutral_probability": round(neutral, 3),
    }


def _cached_finbert_result(article: dict[str, Any]) -> dict[str, Any] | None:
    if not bool(article.get("cached_finbert_model_available")):
        return None
    label = str(article.get("cached_finbert_label", "") or "").strip().lower()
    if not label:
        return None
    return {
        "label": label,
        "score": round(float(article.get("cached_finbert_score", 0.0) or 0.0), 3),
        "confidence": round(float(article.get("cached_finbert_confidence", 0.0) or 0.0), 3),
        "positive_probability": round(
            float(article.get("cached_finbert_positive_probability", 0.0) or 0.0), 3
        ),
        "negative_probability": round(
            float(article.get("cached_finbert_negative_probability", 0.0) or 0.0), 3
        ),
        "neutral_probability": round(
            float(article.get("cached_finbert_neutral_probability", 0.0) or 0.0), 3
        ),
    }


def _recency_weight(article: dict[str, Any]) -> float:
    article_dt = None
    for field in ("published_at", "collected_at", "last_seen_at", "first_seen_at"):
        article_dt = _parse_iso_datetime(article.get(field))
        if article_dt is not None:
            break
    if article_dt is None:
        return 0.85
    age_hours = max((datetime.now(timezone.utc) - article_dt).total_seconds(), 0.0) / 3600.0
    if age_hours <= 3:
        return 1.0
    if age_hours <= 12:
        return 0.95
    if age_hours <= 24:
        return 0.9
    if age_hours <= 48:
        return 0.8
    return 0.65


def _exact_token_match(text: str, token: str) -> bool:
    if not token:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(token.lower())}(?![a-z0-9])", text))


def _company_identity_terms(article: dict[str, Any]) -> list[str]:
    company = str(article.get("company", "") or "").strip().lower()
    if not company:
        return []
    tokens = re.findall(r"[a-z0-9]+", company)
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in COMPANY_TOKEN_STOPWORDS
    ]


def _should_apply_finbert(
    article: dict[str, Any],
    *,
    finbert_ready: bool,
    normalized_score: float,
    confidence: float,
    ticker_relevance_confidence: float,
    positive_markers: list[str],
    negative_markers: list[str],
) -> bool:
    if not finbert_ready or _env_flag("DISABLE_LOCAL_FINBERT"):
        return False

    event_type = str(
        article.get("event_type", "")
        or ((article.get("event_types") or [""])[0] if isinstance(article.get("event_types"), list) else "")
    ).strip()
    marker_count = len(set(positive_markers + negative_markers))

    # Keep very explicit finance headlines on the fast rule path.
    if confidence >= 0.78 and marker_count >= 2 and abs(normalized_score) >= 0.42:
        return False

    if positive_markers and negative_markers:
        return True

    if event_type in {"general_company_focus", "market_reaction", "regulatory_or_geopolitical", "product_or_strategy"}:
        return True

    if 0.3 <= confidence <= 0.78:
        return True

    if 0.22 <= ticker_relevance_confidence <= 0.58:
        return True

    if abs(normalized_score) <= 0.3:
        return True

    return False


def score_ticker_relevance(article: dict[str, Any]) -> dict[str, Any]:
    title_text = _normalize_text(article.get("title", ""))
    summary_text = _normalize_text(article.get("summary", ""))
    body_text = f"{title_text} {summary_text}".strip()

    source_quality_tier = str(article.get("source_quality_tier", "") or "secondary_structured")
    event_type = str(
        article.get("event_type", "")
        or ((article.get("event_types") or [""])[0] if isinstance(article.get("event_types"), list) else "")
    ).strip()

    matched_tickers = [
        str(value).strip().upper()
        for value in article.get("matched_tickers", []) or []
        if str(value).strip()
    ]
    primary_ticker = str(article.get("ticker", "") or (matched_tickers[0] if matched_tickers else "")).strip().upper()
    source_scope_ticker = str(article.get("source_scope_ticker", "") or "").strip().upper()

    relevance = 0.24
    markers: list[str] = []

    if source_scope_ticker and primary_ticker and source_scope_ticker == primary_ticker:
        relevance += 0.22
        markers.append("source_scoped_ticker")

    if primary_ticker and _exact_token_match(title_text, primary_ticker.lower()):
        relevance += 0.25
        markers.append("ticker_in_title")
    elif primary_ticker and _exact_token_match(summary_text, primary_ticker.lower()):
        relevance += 0.12
        markers.append("ticker_in_summary")

    company_terms = _company_identity_terms(article)
    company_title_hits = [term for term in company_terms if _exact_token_match(title_text, term)]
    company_summary_hits = [term for term in company_terms if _exact_token_match(summary_text, term)]
    if company_title_hits:
        relevance += min(0.28, 0.11 * len(company_title_hits))
        markers.append("company_in_title")
    if company_summary_hits:
        relevance += min(0.16, 0.06 * len(company_summary_hits))
        markers.append("company_in_summary")

    if len(matched_tickers) == 1:
        relevance += 0.12
        markers.append("single_ticker_match")
    elif len(matched_tickers) == 2:
        relevance += 0.05
    elif len(matched_tickers) >= 4:
        relevance -= 0.08
        markers.append("multi_ticker_context")

    if source_quality_tier == "primary_structured":
        relevance += 0.08
    elif source_quality_tier == "secondary_structured":
        relevance += 0.04
    elif source_quality_tier == "supplemental_unstructured":
        relevance -= 0.03

    relevance *= EVENT_RELEVANCE_MULTIPLIER.get(event_type, 1.0)
    relevance = min(0.99, max(0.08, relevance))

    return {
        "ticker_relevance_confidence": round(relevance, 3),
        "ticker_relevance_markers": sorted(set(markers)),
    }


def score_article_sentiment(
    article: dict[str, Any],
    *,
    allow_finbert: bool = True,
    force_finbert_ready: bool = False,
) -> dict[str, Any]:
    source_quality_tier = str(article.get("source_quality_tier", "") or "secondary_structured")
    event_type = str(
        article.get("event_type", "")
        or ((article.get("event_types") or [""])[0] if isinstance(article.get("event_types"), list) else "")
    ).strip()

    title_text = _normalize_text(article.get("title", ""))
    summary_text = _normalize_text(article.get("summary", ""))
    event_text = _normalize_text(event_type.replace("_", " "))

    positive_title_phrases, positive_title_phrase_score = _find_phrase_markers(
        f"{title_text} {event_text}".strip(),
        POSITIVE_PHRASE_ITEMS,
        weight_multiplier=TITLE_WEIGHT,
    )
    negative_title_phrases, negative_title_phrase_score = _find_phrase_markers(
        f"{title_text} {event_text}".strip(),
        NEGATIVE_PHRASE_ITEMS,
        weight_multiplier=TITLE_WEIGHT,
    )
    positive_summary_phrases, positive_summary_phrase_score = _find_phrase_markers(
        summary_text,
        POSITIVE_PHRASE_ITEMS,
        weight_multiplier=SUMMARY_WEIGHT,
    )
    negative_summary_phrases, negative_summary_phrase_score = _find_phrase_markers(
        summary_text,
        NEGATIVE_PHRASE_ITEMS,
        weight_multiplier=SUMMARY_WEIGHT,
    )

    positive_title_terms, positive_title_term_score = _find_term_markers(
        title_text,
        POSITIVE_TERMS,
        weight_multiplier=TITLE_WEIGHT,
    )
    negative_title_terms, negative_title_term_score = _find_term_markers(
        title_text,
        NEGATIVE_TERMS,
        weight_multiplier=TITLE_WEIGHT,
    )
    positive_summary_terms, positive_summary_term_score = _find_term_markers(
        summary_text,
        POSITIVE_TERMS,
        weight_multiplier=SUMMARY_WEIGHT,
    )
    negative_summary_terms, negative_summary_term_score = _find_term_markers(
        summary_text,
        NEGATIVE_TERMS,
        weight_multiplier=SUMMARY_WEIGHT,
    )

    positive_markers = sorted(
        set(
            positive_title_phrases
            + positive_summary_phrases
            + positive_title_terms
            + positive_summary_terms
        )
    )
    negative_markers = sorted(
        set(
            negative_title_phrases
            + negative_summary_phrases
            + negative_title_terms
            + negative_summary_terms
        )
    )

    raw_score = (
        positive_title_phrase_score
        + positive_summary_phrase_score
        + positive_title_term_score
        + positive_summary_term_score
        - negative_title_phrase_score
        - negative_summary_phrase_score
        - negative_title_term_score
        - negative_summary_term_score
    )

    if raw_score > 0:
        raw_score += EVENT_SCORE_TILT.get(event_type, 0.0)
    elif raw_score < 0:
        raw_score -= EVENT_SCORE_TILT.get(event_type, 0.0)

    normalized_score = max(min(raw_score / 5.8, 1.0), -1.0)
    relevance = score_ticker_relevance(article)
    ticker_relevance_confidence = float(relevance["ticker_relevance_confidence"])

    marker_count = len(set(positive_markers + negative_markers))
    source_confidence = SOURCE_CONFIDENCE_MULTIPLIER.get(source_quality_tier, 1.0)
    event_confidence = EVENT_CONFIDENCE_MULTIPLIER.get(event_type, 0.95)
    confidence = min(
        0.985,
        max(
            0.12,
            (
                0.24
                + min(marker_count, 5) * 0.1
                + min(abs(normalized_score), 1.0) * 0.18
                + min(ticker_relevance_confidence, 1.0) * 0.22
            )
            * source_confidence
            * event_confidence,
        ),
    )

    finbert_payload = prepare_finbert_payload(article)
    cached_finbert_result = _cached_finbert_result(article)
    finbert_result = cached_finbert_result
    if finbert_result is None:
        finbert_result = (
            score_finbert_sentiment(str(finbert_payload.get("finbert_input_text", "")))
            if allow_finbert
            and (
                (force_finbert_ready and bool(finbert_payload.get("finbert_ready")))
                or _should_apply_finbert(
                    article,
                    finbert_ready=bool(finbert_payload.get("finbert_ready")),
                    normalized_score=normalized_score,
                    confidence=confidence,
                    ticker_relevance_confidence=ticker_relevance_confidence,
                    positive_markers=positive_markers,
                    negative_markers=negative_markers,
                )
            )
            else None
        )

    if finbert_result:
        finbert_weight = 0.42 if event_type in {"general_company_focus", "market_reaction"} else 0.28
        if any(
            marker in positive_markers
            for marker in (
                "stock soaring today",
                "trading up today",
                "stock is trading up today",
                "raises quarterly dividend",
                "beats quarterly sales estimates",
                "maintained at outperform",
                "maintained at buy",
            )
        ):
            finbert_weight = min(finbert_weight, 0.18)
        if any(
            marker in negative_markers
            for marker in (
                "cut bank jobs",
                "cuts guidance",
                "cut guidance",
                "cuts dividend",
                "dividend cut",
            )
        ):
            finbert_weight = min(finbert_weight, 0.18)
        normalized_score = ((1.0 - finbert_weight) * normalized_score) + (finbert_weight * float(finbert_result["score"]))
        confidence = min(
            0.99,
            max(
                0.12,
                (0.7 * confidence) + (0.3 * float(finbert_result["confidence"])),
            ),
        )

    if abs(normalized_score) < 0.1 and ticker_relevance_confidence < 0.3:
        label = "neutral"
    elif normalized_score >= 0.22 or (
        positive_markers and not negative_markers and normalized_score >= 0.16
    ):
        label = "bullish"
    elif normalized_score <= -0.22 or (
        negative_markers and not positive_markers and normalized_score <= -0.16
    ):
        label = "bearish"
    elif positive_markers and negative_markers:
        label = "mixed"
    else:
        label = "neutral"

    if normalized_score >= 0.15:
        impact_direction = "positive"
    elif normalized_score <= -0.15:
        impact_direction = "negative"
    else:
        impact_direction = "neutral"

    signal_confidence = min(
        0.99,
        max(0.08, 0.55 * confidence + 0.45 * ticker_relevance_confidence),
    )

    return {
        "sentiment_label": label,
        "sentiment_score": round(normalized_score, 3),
        "sentiment_confidence": round(confidence, 3),
        "raw_sentiment_confidence": round(confidence, 3),
        "signal_confidence": round(signal_confidence, 3),
        "ticker_relevance_confidence": round(ticker_relevance_confidence, 3),
        "ticker_relevance_markers": relevance["ticker_relevance_markers"],
        "sentiment_source_weight": SOURCE_WEIGHT.get(source_quality_tier, 0.8),
        "market_impact_bias": impact_direction,
        "sentiment_positive_markers": positive_markers,
        "sentiment_negative_markers": negative_markers,
        **finbert_payload,
        "sentiment_pipeline_stage": "hybrid_finbert_rule" if finbert_result else finbert_payload["sentiment_pipeline_stage"],
        "sentiment_model_used": "hybrid_finbert_rule" if finbert_result else finbert_payload["sentiment_model_used"],
        "finbert_model_name": FINBERT_MODEL_NAME,
        "finbert_model_available": bool(finbert_result),
        "finbert_label": str(finbert_result["label"]) if finbert_result else "",
        "finbert_score": float(finbert_result["score"]) if finbert_result else 0.0,
        "finbert_confidence": float(finbert_result["confidence"]) if finbert_result else 0.0,
        "finbert_positive_probability": float(finbert_result["positive_probability"]) if finbert_result else 0.0,
        "finbert_negative_probability": float(finbert_result["negative_probability"]) if finbert_result else 0.0,
        "finbert_neutral_probability": float(finbert_result["neutral_probability"]) if finbert_result else 0.0,
    }


def summarize_article_sentiment(articles: list[dict[str, Any]]) -> dict[str, Any]:
    if not articles:
        return {
            "label": "Neutral",
            "score": 0.0,
            "confidence": 0.0,
            "signal_confidence": 0.0,
            "avg_relevance_confidence": 0.0,
            "bullish_count": 0,
            "bearish_count": 0,
            "mixed_count": 0,
            "neutral_count": 0,
        }

    total_weight = 0.0
    score_weight_total = 0.0
    weighted_score = 0.0
    weighted_confidence = 0.0
    weighted_signal_confidence = 0.0
    weighted_relevance = 0.0
    bullish_count = bearish_count = mixed_count = neutral_count = 0

    for article in articles:
        sentiment = (
            {
                "sentiment_label": article.get("sentiment_label", ""),
                "sentiment_score": article.get("sentiment_score", 0.0),
                "sentiment_confidence": article.get("sentiment_confidence", 0.0),
                "signal_confidence": article.get("signal_confidence", article.get("sentiment_confidence", 0.0)),
                "sentiment_source_weight": article.get("sentiment_source_weight", 0.8),
                "ticker_relevance_confidence": article.get("ticker_relevance_confidence", 0.0),
            }
            if article.get("sentiment_label")
            else score_article_sentiment(article)
        )
        label = str(sentiment.get("sentiment_label", "neutral"))
        score = float(sentiment.get("sentiment_score", 0.0) or 0.0)
        confidence = float(sentiment.get("sentiment_confidence", 0.0) or 0.0)
        signal_confidence = float(sentiment.get("signal_confidence", confidence) or confidence)
        source_weight = float(sentiment.get("sentiment_source_weight", 0.8) or 0.8)
        relevance_confidence = float(sentiment.get("ticker_relevance_confidence", 0.0) or 0.0)
        recency_weight = _recency_weight(article)
        weight = max(0.08, confidence * source_weight * recency_weight)
        directional_support = max(
            0.14,
            min(1.0, (abs(score) * 2.6) + (relevance_confidence * 0.18)),
        )
        weighted_score += score * weight * directional_support
        total_weight += weight
        score_weight_total += weight * directional_support
        weighted_confidence += confidence * weight
        weighted_signal_confidence += signal_confidence * weight
        weighted_relevance += relevance_confidence * weight
        if label == "bullish":
            bullish_count += 1
        elif label == "bearish":
            bearish_count += 1
        elif label == "mixed":
            mixed_count += 1
        else:
            neutral_count += 1

    aggregate_score = weighted_score / score_weight_total if score_weight_total else 0.0
    divisor = total_weight if total_weight else float(len(articles))
    average_confidence = weighted_confidence / divisor if divisor else 0.0
    average_signal_confidence = weighted_signal_confidence / divisor if divisor else 0.0
    average_relevance = weighted_relevance / divisor if divisor else 0.0

    if aggregate_score >= 0.12:
        label = "Bullish Tilt"
    elif aggregate_score <= -0.12:
        label = "Bearish Tilt"
    elif bullish_count and bearish_count:
        label = "Mixed"
    else:
        label = "Neutral"

    return {
        "label": label,
        "score": round(aggregate_score, 3),
        "confidence": round(average_confidence, 3),
        "signal_confidence": round(average_signal_confidence, 3),
        "avg_relevance_confidence": round(average_relevance, 3),
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "mixed_count": mixed_count,
        "neutral_count": neutral_count,
    }
