#!/usr/bin/env python3
"""
feed_digest.py — RSS → Claude → Mailgun digest
Fetches articles from RSS feeds, filters for relevance via Claude API,
summarizes them, and sends an HTML email via Mailgun.
"""

import json
import sqlite3
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from config import load_config
from feeds import fetch_rss_articles
from mailer import send_digest
from templates import render_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "state.db"


# ── Database ──────────────────────────────────────────────────────────────────


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen_articles (
            article_id TEXT PRIMARY KEY,
            seen_at    TEXT NOT NULL
        )
    """)
    con.commit()
    return con


def filter_unseen(con, articles: list[dict]) -> list[dict]:
    ids = [a["id"] for a in articles]
    placeholders = ",".join("?" * len(ids))
    seen = {
        row[0]
        for row in con.execute(
            f"SELECT article_id FROM seen_articles WHERE article_id IN ({placeholders})",  # nosec B608
            ids,
        )
    }
    return [a for a in articles if a["id"] not in seen]


def mark_seen(con, articles: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    con.executemany(
        "INSERT OR IGNORE INTO seen_articles (article_id, seen_at) VALUES (?, ?)",
        [(a["id"], now) for a in articles],
    )
    con.commit()


# ── Claude: relevance + summary ───────────────────────────────────────────────

RELEVANCE_SYSTEM = """You are a research assistant filtering news articles for a specific reader.
The reader's interests, preferences, and example articles they liked/disliked are described below.
For each article, decide if it is relevant and worth reading.
Return ONLY a JSON array — no markdown, no explanation — with one object per article:
[{{"id": "...", "relevant": true/false, "reason": "one sentence explaining why the reader would or would not find this interesting"}}]

When writing the reason for relevant articles, be specific about WHY this article matters to this
particular reader — connect it to their stated interests, role, or priorities. Do not write generic
reasons like "this is relevant to your interests"."""

SUMMARY_SYSTEM = """You are a concise technical analyst. For each article provided, write a 3–5 sentence
summary that captures: what happened, why it matters, and any action or implication for the reader.
Return ONLY a JSON array — no markdown, no explanation — with:
[{{"id": "...", "summary": "..."}}]"""


def score_relevance(
    client: anthropic.Anthropic, cfg: dict, articles: list[dict]
) -> list[dict]:
    """Ask Claude which articles are relevant. Preserves the reason on each article."""
    interests = cfg["interests"]
    prefs = cfg.get("preferences", {})

    article_list = "\n".join(
        f'- id={a["id"]!r} source={a["source"]!r} title={a["title"]!r} snippet={a["snippet"][:300]!r}'
        for a in articles
    )

    prompt_parts = [f"Reader interests:\n{interests}"]

    if prefs.get("positive_examples"):
        prompt_parts.append(
            "Examples of articles this reader LIKED:\n"
            + "\n".join(f"- {ex}" for ex in prefs["positive_examples"])
        )
    if prefs.get("negative_examples"):
        prompt_parts.append(
            "Examples of articles this reader DID NOT like:\n"
            + "\n".join(f"- {ex}" for ex in prefs["negative_examples"])
        )
    if prefs.get("high_priority_topics"):
        prompt_parts.append(
            "High-priority topics (always include if relevant):\n"
            + "\n".join(f"- {t}" for t in prefs["high_priority_topics"])
        )
    if prefs.get("low_priority_topics"):
        prompt_parts.append(
            "Low-priority topics (include only if exceptionally insightful):\n"
            + "\n".join(f"- {t}" for t in prefs["low_priority_topics"])
        )

    prompt_parts.append(f"Articles to evaluate:\n{article_list}")

    prompt = "\n\n".join(prompt_parts)

    msg = client.messages.create(
        model="claude-sonnet-4-5-20250514",
        max_tokens=4096,
        system=RELEVANCE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = msg.content[0].text.strip()
    scores = json.loads(raw)
    reason_by_id = {s["id"]: s.get("reason", "") for s in scores}
    relevant_ids = {s["id"] for s in scores if s.get("relevant")}

    result = []
    for a in articles:
        if a["id"] in relevant_ids:
            result.append({**a, "reason": reason_by_id.get(a["id"], "")})

    return result


def summarize_articles(client: anthropic.Anthropic, articles: list[dict]) -> list[dict]:
    """Ask Claude to summarize each relevant article."""
    article_list = "\n\n".join(
        f'id={a["id"]!r}\ntitle: {a["title"]}\nsource: {a["source"]}\ncontent: {a["snippet"]}'
        for a in articles
    )

    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        system=SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": article_list}],
    )

    raw = msg.content[0].text.strip()
    summaries = {s["id"]: s["summary"] for s in json.loads(raw)}

    result = []
    for a in articles:
        result.append({**a, "summary": summaries.get(a["id"], "No summary available.")})
    return result


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    cfg = load_config()
    con = init_db()
    claude = anthropic.Anthropic(api_key=cfg["anthropic"]["api_key"])

    # 1. Fetch from RSS feeds
    raw_articles = fetch_rss_articles(cfg)
    if not raw_articles:
        log.info("No articles found — exiting.")
        return

    # 2. Deduplicate against local seen DB
    unseen = filter_unseen(con, raw_articles)
    log.info(f"{len(unseen)} unseen articles after dedup")
    if not unseen:
        log.info("Nothing new — exiting.")
        return

    # 3. Relevance filter
    relevant = score_relevance(claude, cfg, unseen)
    log.info(f"{len(relevant)} articles deemed relevant")
    if not relevant:
        log.info("No relevant articles this run.")
        mark_seen(con, unseen)
        return

    # 4. Summarize
    summarized = summarize_articles(claude, relevant)

    # 5. Send email
    html = render_email(summarized, cfg)
    subject = f"📰 Feed Digest — {datetime.now().strftime('%d %b %Y, %H:%M')}"
    send_digest(cfg, subject, html)
    log.info("Digest email sent.")

    # 6. Persist state
    mark_seen(con, unseen)
    log.info("Done.")


if __name__ == "__main__":  # pragma: no cover
    main()
