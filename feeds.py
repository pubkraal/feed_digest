"""
feeds.py — Fetch articles from RSS/Atom feeds.
"""

import logging
import re

import feedparser

log = logging.getLogger(__name__)


def fetch_rss_articles(cfg: dict) -> list[dict]:
    """Fetch articles from RSS/Atom feeds defined in config."""
    feeds_cfg = cfg["feeds"]
    categories = feeds_cfg.get("categories", {})
    max_per_category = feeds_cfg.get("max_articles_per_category", 50)

    seen_urls: set[str] = set()
    articles: list[dict] = []

    for category, urls in categories.items():
        count = 0

        for feed_url in urls:
            parsed = feedparser.parse(feed_url)
            feed_title = getattr(parsed.feed, "title", "Unknown")

            for entry in parsed.entries:
                if count >= max_per_category:
                    break

                link = getattr(entry, "link", "") or getattr(entry, "id", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                title = getattr(entry, "title", "") or "(no title)"
                summary = getattr(entry, "summary", "")
                published = getattr(entry, "published", "")

                articles.append(
                    {
                        "id": link,
                        "title": title,
                        "url": link,
                        "source": feed_title,
                        "category": category,
                        "published": published,
                        "snippet": _strip_html(summary)[:800],
                    }
                )
                count += 1

        log.info(f"  {count} articles from category '{category}'")

    log.info(
        f"Fetched {len(articles)} total articles across {len(categories)} categories"
    )

    return articles


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip()
