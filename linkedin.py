#!/usr/bin/env python3
"""
linkedin.py — Weekly LinkedIn post generator.
Picks articles from the past week's digests, selects the most relevant
for a random sector (energy or finance), generates a LinkedIn post via
Claude, and emails it.
"""

import json
import logging
import random
import sys
from datetime import datetime, timedelta, timezone

import anthropic
import httpx

from config import load_config
from digest import DB_PATH, DEFAULT_MODEL, init_db, setup_logging, strip_code_fences
from mailer import send_one

log = logging.getLogger(__name__)

MAX_TOKENS = 4096

SECTOR_SELECT_SYSTEM = """You are a content curator selecting articles for a LinkedIn post about
information security in the {sector} sector.
Given the articles below, pick the 3 most relevant for a LinkedIn post targeting
{sector} sector professionals who care about cybersecurity, compliance, and risk.
Return ONLY a JSON array of article IDs — no markdown, no explanation.
Example: ["id1", "id2", "id3"]"""

LINKEDIN_POST_SYSTEM = """You are a LinkedIn ghostwriter for an information security consultant
who advises {sector} sector clients. Write a compelling LinkedIn post (300–600 words) that:
- Opens with a hook that grabs attention
- Analyzes the 2–3 articles provided through the lens of {sector} sector infosec
- Connects the dots between the articles to form a narrative
- Closes with 2–3 concrete, actionable security recommendations that {sector} sector
  professionals can implement this week
- Ends with a thought-provoking question or call to action
- Uses a positive, informative, and approachable tone — frame recommendations as
  opportunities to strengthen security posture, never as fear-driven warnings
- Includes relevant hashtags at the end

Return ONLY the post text — no markdown formatting, no labels."""


def fetch_recent_sent(con, days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = con.execute(
        "SELECT article_id, title, url, source, category, summary, reason"
        " FROM sent_articles WHERE sent_at >= ?",
        (cutoff,),
    ).fetchall()

    return [
        {
            "id": row[0],
            "title": row[1],
            "url": row[2],
            "source": row[3],
            "category": row[4],
            "summary": row[5],
        }
        for row in rows
    ]


def select_sector_articles(
    client: anthropic.Anthropic,
    articles: list[dict],
    sector: str,
    model: str = DEFAULT_MODEL,
) -> list[dict]:
    article_list = "\n".join(
        f'- id={a["id"]!r} title={a["title"]!r} summary={a["summary"][:200]!r}'
        for a in articles
    )

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SECTOR_SELECT_SYSTEM.format(sector=sector),
            messages=[{"role": "user", "content": article_list}],
        )
    except Exception as exc:
        log.error("Sector selection API call failed: %s", exc)
        return []

    raw = strip_code_fences(msg.content[0].text)
    try:
        selected_ids = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Failed to parse sector selection JSON: %s", raw)
        return []

    by_id = {a["id"]: a for a in articles}

    return [by_id[aid] for aid in selected_ids if aid in by_id]


def generate_linkedin_post(
    client: anthropic.Anthropic,
    articles: list[dict],
    sector: str,
    model: str = DEFAULT_MODEL,
) -> str:
    article_text = "\n\n".join(
        f'Title: {a["title"]}\nURL: {a["url"]}\nSummary: {a["summary"]}'
        for a in articles
    )

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=LINKEDIN_POST_SYSTEM.format(sector=sector),
            messages=[{"role": "user", "content": article_text}],
        )
    except Exception as exc:
        log.error("LinkedIn post generation failed: %s", exc)
        return ""

    return msg.content[0].text.strip()


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def format_post_as_html(post: str) -> str:
    """Wrap a plain-text LinkedIn post in a simple HTML email."""
    escaped = _esc(post)
    body = escaped.replace("\n", "<br>\n")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>LinkedIn Post</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" role="presentation"
             style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;
                    border:1px solid #e5e7eb;">
        <tr><td style="padding:32px;">
          <p style="margin:0;font-size:15px;line-height:1.7;color:#1f2937;">
            {body}
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def cleanup_old_sent(con, days: int = 30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con.execute("DELETE FROM sent_articles WHERE sent_at < ?", (cutoff,))
    con.commit()


def main():
    cfg = load_config()
    setup_logging(cfg.get("debug", False))
    con = init_db()
    claude = anthropic.Anthropic(
        api_key=cfg["anthropic"]["api_key"],
        timeout=httpx.Timeout(120.0, connect=10.0),
    )
    model = cfg.get("anthropic", {}).get("model", DEFAULT_MODEL)

    # 1. Fetch recent sent articles
    articles = fetch_recent_sent(con)
    if not articles:
        log.info("No recent sent articles — exiting.")
        cleanup_old_sent(con)
        return

    # 2. Pick a random sector
    sector = random.choice(["energy", "finance"])  # nosec B311
    log.info("Selected sector: %s", sector)

    # 3. Select top articles for sector
    selected = select_sector_articles(claude, articles, sector, model=model)
    if not selected:
        log.info("No articles selected for sector '%s' — exiting.", sector)
        cleanup_old_sent(con)
        return

    # 4. Generate LinkedIn post
    post = generate_linkedin_post(claude, selected, sector, model=model)
    if not post:
        log.info("Failed to generate LinkedIn post — exiting.")
        cleanup_old_sent(con)
        return

    # 5. Email the post
    mg = cfg["mailgun"]
    from_addr = cfg["email"].get("from", f"digest@{mg['domain']}")
    subject = (
        f"LinkedIn Post — {sector.title()} — {datetime.now().strftime('%d %b %Y')}"
    )
    html = format_post_as_html(post)
    send_one(
        mg["api_key"], mg["domain"], from_addr, "john@johnkraal.com", subject, html
    )
    log.info("LinkedIn post emailed.")

    # 6. Cleanup old sent articles
    cleanup_old_sent(con)
    log.info("Done.")


if __name__ == "__main__":  # pragma: no cover
    main()
