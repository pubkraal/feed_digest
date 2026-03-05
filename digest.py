#!/usr/bin/env python3
"""
feed_digest.py — RSS → Claude → Mailgun digest
Fetches articles from RSS feeds, filters for relevance via Claude API,
summarizes them, and sends an HTML email via Mailgun.
"""

import json
import re
import sqlite3
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import anthropic

from config import load_config
from feeds import fetch_rss_articles
from mailer import send_digest
from templates import render_email

log = logging.getLogger(__name__)


def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


DB_PATH = Path(__file__).parent / "state.db"


MAX_TOKENS = 16384


def _group_by_category(articles: list[dict]) -> dict[str, list[dict]]:
    groups = {}
    for a in articles:
        groups.setdefault(a.get("category", "Uncategorized"), []).append(a)
    return groups


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) from Claude responses."""
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


# ── Database ──────────────────────────────────────────────────────────────────


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen_articles (
            article_id TEXT PRIMARY KEY,
            seen_at    TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sent_articles (
            article_id TEXT,
            title      TEXT,
            url        TEXT,
            source     TEXT,
            category   TEXT,
            summary    TEXT,
            reason     TEXT,
            sent_at    TEXT
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


def record_sent(con, articles: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    con.executemany(
        "INSERT INTO sent_articles"
        " (article_id, title, url, source, category, summary, reason, sent_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                a["id"],
                a.get("title", ""),
                a.get("url", ""),
                a.get("source", ""),
                a.get("category", ""),
                a.get("summary", ""),
                a.get("reason", ""),
                now,
            )
            for a in articles
        ],
    )
    con.commit()


def cleanup_old_seen(con, days: int = 7):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con.execute("DELETE FROM seen_articles WHERE seen_at < ?", (cutoff,))
    con.commit()


# ── Claude: relevance + summary ───────────────────────────────────────────────

RELEVANCE_SYSTEM = """You are a research assistant filtering news articles for a specific reader.
The reader's interests, preferences, and example articles they liked/disliked are described below.
For each article, decide if it is relevant and worth reading.
Return at most {max_relevant} articles marked as relevant — pick only the best.
Return ONLY a JSON array — no markdown, no explanation — with one object per article:
[{{"id": "...", "relevant": true/false, "reason": "one sentence explaining why the reader would or would not find this interesting"}}]

When writing the reason for relevant articles, be specific about WHY this article matters to this
particular reader — connect it to their stated interests, role, or priorities. Do not write generic
reasons like "this is relevant to your interests"."""

SUMMARY_SYSTEM = """You are a concise technical analyst. For each article provided, write a 3–5 sentence
summary that captures: what happened, why it matters, and any action or implication for the reader.
Return ONLY a JSON array — no markdown, no explanation — with:
[{{"id": "...", "summary": "..."}}]"""

INTRO_SYSTEM = """You write short, punchy introductions for a daily news digest email.
Given a list of article titles and summaries, write 1–2 sentences that highlight the most
important takeaways for the reader. Adjust your tone to match the news:
- If the news is alarming or involves breaches/threats, be direct and urgent.
- If the news is mostly positive (new tools, regulations progressing), be upbeat and encouraging.
- If it's mixed, strike a balanced tone.
Return ONLY the intro text — no quotes, no markdown, no labels."""


def _build_relevance_context(cfg: dict) -> list[str]:
    """Build the reusable context parts of the relevance prompt (interests, prefs)."""
    prefs = cfg.get("preferences", {})
    parts = [f"Reader interests:\n{cfg['interests']}"]

    if prefs.get("positive_examples"):
        parts.append(
            "Examples of articles this reader LIKED:\n"
            + "\n".join(f"- {ex}" for ex in prefs["positive_examples"])
        )
    if prefs.get("negative_examples"):
        parts.append(
            "Examples of articles this reader DID NOT like:\n"
            + "\n".join(f"- {ex}" for ex in prefs["negative_examples"])
        )
    if prefs.get("high_priority_topics"):
        parts.append(
            "High-priority topics (always include if relevant):\n"
            + "\n".join(f"- {t}" for t in prefs["high_priority_topics"])
        )
    if prefs.get("low_priority_topics"):
        parts.append(
            "Low-priority topics (include only if exceptionally insightful):\n"
            + "\n".join(f"- {t}" for t in prefs["low_priority_topics"])
        )

    return parts


def _score_batch(
    client: anthropic.Anthropic,
    context_parts: list[str],
    articles: list[dict],
    category: str,
    max_relevant: int = 5,
) -> tuple[list[dict], list[dict]]:
    """Score a single batch of articles for relevance.

    Returns (relevant, non_relevant) — both with reasons attached.
    """
    article_list = "\n".join(
        f'- id={a["id"]!r} source={a["source"]!r} title={a["title"]!r} snippet={a["snippet"][:300]!r}'
        for a in articles
    )

    prompt = "\n\n".join([*context_parts, f"Articles to evaluate:\n{article_list}"])

    log.debug("Relevance prompt [%s]:\n%s", category, prompt)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=MAX_TOKENS,
            system=RELEVANCE_SYSTEM.format(max_relevant=max_relevant),
            messages=[{"role": "user", "content": prompt}],
        )
    except (httpx.TimeoutException, anthropic.APIError) as exc:
        log.error("Relevance API call failed for category '%s': %s", category, exc)
        return [], []

    raw_text = msg.content[0].text
    log.debug("Relevance raw response [%s]:\n%s", category, raw_text)
    log.debug(
        "Relevance API usage [%s]: input=%d output=%d stop=%s",
        category,
        msg.usage.input_tokens,
        msg.usage.output_tokens,
        msg.stop_reason,
    )

    if msg.stop_reason == "max_tokens":
        log.warning(
            "Relevance response truncated for category '%s' — output hit %d token limit",
            category,
            MAX_TOKENS,
        )

    raw = strip_code_fences(raw_text)
    if not raw:
        log.warning(
            "Claude returned empty response for relevance scoring [%s]", category
        )
        return [], []
    try:
        scores = json.loads(raw)
    except json.JSONDecodeError:
        log.error(
            "Failed to parse relevance JSON [%s]. Raw response:\n%s", category, raw_text
        )
        return [], []

    reason_by_id = {s["id"]: s.get("reason", "") for s in scores}
    relevant_ids = set(s["id"] for s in scores if s.get("relevant"))

    relevant = []
    non_relevant = []
    for a in articles:
        enriched = {**a, "reason": reason_by_id.get(a["id"], "")}
        if a["id"] in relevant_ids and len(relevant) < max_relevant:
            relevant.append(enriched)
        else:
            non_relevant.append(enriched)

    return relevant, non_relevant


def score_relevance(
    client: anthropic.Anthropic, cfg: dict, articles: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Ask Claude which articles are relevant, batched by category.

    Returns (relevant, non_relevant).
    """
    context_parts = _build_relevance_context(cfg)
    groups = _group_by_category(articles)
    max_relevant = cfg.get("feeds", {}).get("max_relevant_per_category", 5)

    all_relevant = []
    all_non_relevant = []
    for category, batch in groups.items():
        log.info("Scoring %d articles in category '%s'", len(batch), category)
        relevant, non_relevant = _score_batch(
            client, context_parts, batch, category, max_relevant
        )
        all_relevant.extend(relevant)
        all_non_relevant.extend(non_relevant)

    return all_relevant, all_non_relevant


def _summarize_batch(
    client: anthropic.Anthropic, articles: list[dict], category: str
) -> dict[str, str]:
    """Summarize a single batch of articles. Returns {id: summary}."""
    article_list = "\n\n".join(
        f'id={a["id"]!r}\ntitle: {a["title"]}\nsource: {a["source"]}\ncontent: {a["snippet"]}'
        for a in articles
    )

    log.debug("Summary prompt [%s]:\n%s", category, article_list)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=MAX_TOKENS,
            system=SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": article_list}],
        )
    except (httpx.TimeoutException, anthropic.APIError) as exc:
        log.error("Summary API call failed for category '%s': %s", category, exc)
        return {}

    raw_text = msg.content[0].text
    log.debug("Summary raw response [%s]:\n%s", category, raw_text)
    log.debug(
        "Summary API usage [%s]: input=%d output=%d stop=%s",
        category,
        msg.usage.input_tokens,
        msg.usage.output_tokens,
        msg.stop_reason,
    )

    if msg.stop_reason == "max_tokens":
        log.warning(
            "Summary response truncated for category '%s' — output hit %d token limit",
            category,
            MAX_TOKENS,
        )

    raw = strip_code_fences(raw_text)
    if not raw:
        log.warning("Claude returned empty response for summarization [%s]", category)
        return {}
    try:
        return {s["id"]: s["summary"] for s in json.loads(raw)}
    except json.JSONDecodeError:
        log.error(
            "Failed to parse summary JSON [%s]. Raw response:\n%s", category, raw_text
        )
        return {}


def summarize_articles(client: anthropic.Anthropic, articles: list[dict]) -> list[dict]:
    """Ask Claude to summarize each relevant article, batched by category."""
    groups = _group_by_category(articles)

    summaries = {}
    for category, batch in groups.items():
        log.info("Summarizing %d articles in category '%s'", len(batch), category)
        summaries.update(_summarize_batch(client, batch, category))

    return [
        {**a, "summary": summaries.get(a["id"], "No summary available.")}
        for a in articles
    ]


def generate_intro(client: anthropic.Anthropic, articles: list[dict]) -> str:
    """Generate a short intro for the digest email based on article summaries."""
    article_list = "\n".join(
        f'- [{a["category"]}] {a["title"]}: {a.get("summary", "")[:200]}'
        for a in articles
    )

    log.debug("Intro prompt:\n%s", article_list)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system=INTRO_SYSTEM,
            messages=[{"role": "user", "content": article_list}],
        )
    except (httpx.TimeoutException, anthropic.APIError) as exc:
        log.error("Intro API call failed: %s", exc)
        return ""

    raw_text = msg.content[0].text.strip()
    log.debug("Intro raw response:\n%s", raw_text)

    return raw_text


ACTIONS_SYSTEM = """You are a security advisor generating actionable recommendations from today's news.
Review ALL articles below — both the selected digest articles and the remaining articles.
Generate 3–5 practical, specific security actions the reader should take today based on the news.

Each action MUST be concise — one sentence, maximum 140 characters. Think headline-length.

For each action:
- If the action is based on a NON-SELECTED article (from the "Remaining articles" section),
  include the article's URL and title so the reader can follow up.
- If the action is based on a selected digest article, set source_url and source_title to null
  (the reader already has it in the main digest).

Return ONLY JSON — no markdown, no explanation:
{{"action_items": [
  {{"action": "concise actionable recommendation", "source_url": "url or null", "source_title": "title or null"}}
]}}"""


def generate_actions_and_briefs(
    client: anthropic.Anthropic,
    relevant: list[dict],
    non_relevant: list[dict],
) -> list[dict]:
    """Generate action items from all articles. Returns list of action item dicts."""
    selected_list = "\n".join(
        f'- title={a["title"]!r} url={a["url"]!r} snippet={a.get("snippet", "")[:200]!r}'
        for a in relevant
    )
    remaining_list = "\n".join(
        f'- title={a["title"]!r} url={a["url"]!r} snippet={a.get("snippet", "")[:200]!r}'
        for a in non_relevant
    )

    prompt = (
        f"Selected digest articles:\n{selected_list}\n\n"
        f"Remaining articles:\n{remaining_list}"
    )

    log.debug("Actions prompt:\n%s", prompt)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=MAX_TOKENS,
            system=ACTIONS_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except (httpx.TimeoutException, anthropic.APIError) as exc:
        log.error("Actions API call failed: %s", exc)
        return []

    raw_text = msg.content[0].text
    log.debug("Actions raw response:\n%s", raw_text)

    raw = strip_code_fences(raw_text)
    if not raw:
        log.warning("Claude returned empty response for actions")
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Failed to parse actions JSON. Raw response:\n%s", raw_text)
        return []

    return data.get("action_items", [])


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    cfg = load_config()
    setup_logging(cfg.get("debug", False))
    con = init_db()
    claude = anthropic.Anthropic(
        api_key=cfg["anthropic"]["api_key"],
        timeout=httpx.Timeout(120.0, connect=10.0),
    )

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
    relevant, non_relevant = score_relevance(claude, cfg, unseen)
    log.info(f"{len(relevant)} articles deemed relevant")
    if not relevant:
        log.info("No relevant articles this run.")
        mark_seen(con, unseen)
        return

    # 4. Summarize
    summarized = summarize_articles(claude, relevant)

    # 5. Send email (only if we have content)
    with_summary = [
        a for a in summarized if a.get("summary") != "No summary available."
    ]
    if not with_summary:
        log.info("No summarized content to send — skipping email.")
    else:
        action_items = generate_actions_and_briefs(claude, relevant, non_relevant)
        intro = generate_intro(claude, with_summary)
        html = render_email(summarized, cfg, intro=intro, action_items=action_items)
        subject = f"📰 Feed Digest — {datetime.now().strftime('%d %b %Y, %H:%M')}"
        send_digest(cfg, subject, html)
        record_sent(con, with_summary)
        log.info("Digest email sent.")

    # 6. Persist state
    mark_seen(con, unseen)
    cleanup_old_seen(con)
    log.info("Done.")


if __name__ == "__main__":  # pragma: no cover
    main()
