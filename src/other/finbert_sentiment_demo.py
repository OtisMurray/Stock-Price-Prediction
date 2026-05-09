"""Baseline multi-source FinBERT sentiment demo."""

from pathlib import Path
import sys

from transformers import pipeline

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.ingestion.rss_collectors import build_keywords, collect_baseline_articles, filter_articles

# Define the ticker and related keywords you want to track.
ticker = "AAPL"
company_name = "Apple"
extra_keywords = ["iphone", "mac", "tim cook"]

pipe = pipeline("text-classification", model="ProsusAI/finbert")
keywords = build_keywords(ticker=ticker, company_name=company_name, extra_keywords=extra_keywords)
articles = collect_baseline_articles(ticker=ticker, limit_per_source=10)
matching_articles = filter_articles(articles, keywords)

# Set counters for total score and number of articles.
total_score = 0
num_articles = 0

for article in matching_articles:
    print(f"Source: {article.source_name}")
    print(f"Title: {article.title}")
    print(f"Link: {article.link}")
    print(f"Published: {article.published}")
    print(f"Summary: {article.summary}")

    sentiment = pipe(article.text)[0]

    # Print the specific sentiment and score.
    print(f'Sentiment {sentiment["label"]}, Score: {sentiment["score"]}')
    print("-" * 40)

    # Calculate total sentiment score using the different labels.
    if sentiment["label"] == "positive":
        total_score += sentiment["score"]
        num_articles += 1
    elif sentiment["label"] == "negative":
        total_score -= sentiment["score"]
        num_articles += 1

if num_articles == 0:
    print("No matching articles found for sentiment analysis.")
    raise SystemExit(0)

final_score = total_score / num_articles
overall_sentiment = (
    "Positive" if final_score >= 0.15 else "Negative" if final_score <= -0.15 else "Neutral"
)
print(f"Overall Sentiment: {overall_sentiment} {final_score:.4f}")
