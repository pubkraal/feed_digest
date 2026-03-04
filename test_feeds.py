"""Tests for feeds.py — RSS fetching module."""

import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from feeds import fetch_rss_articles


def _recent_time_struct():
    """Return a time.struct_time for 1 hour ago (within 24h window)."""
    dt = datetime.now(timezone.utc) - timedelta(hours=1)
    return dt.timetuple()


def _old_time_struct():
    """Return a time.struct_time for 48 hours ago (outside 24h window)."""
    dt = datetime.now(timezone.utc) - timedelta(hours=48)
    return dt.timetuple()


def _make_feed(title="Test Feed", entries=None):
    """Build a fake feedparser result."""
    feed = MagicMock()
    feed.feed.title = title
    feed.entries = entries or []
    return feed


def _make_entry(
    title="Article",
    link="https://example.com/1",
    summary="A summary",
    published="2026-03-01",
    published_parsed=None,
):
    entry = MagicMock()
    entry.get = lambda k, d=None: {
        "title": title,
        "link": link,
        "id": link,
        "summary": summary,
        "published": published,
    }.get(k, d)
    entry.title = title
    entry.link = link
    entry.id = link
    entry.summary = summary
    entry.published = published
    entry.published_parsed = (
        published_parsed if published_parsed is not None else _recent_time_struct()
    )
    return entry


class TestFetchRssArticles(unittest.TestCase):

    def _cfg(self, categories=None, max_articles=50):
        return {
            "feeds": {
                "max_articles_per_category": max_articles,
                "categories": categories or {},
            }
        }

    @patch("feeds.feedparser.parse")
    def test_basic_fetch_returns_articles(self, mock_parse):
        entry = _make_entry(
            title="Breach Report",
            link="https://example.com/breach",
            summary="<p>Details of the breach</p>",
        )
        mock_parse.return_value = _make_feed(
            title="Krebs on Security",
            entries=[entry],
        )

        cfg = self._cfg(categories={"Security": ["https://krebs.com/feed/"]})
        articles = fetch_rss_articles(cfg)

        if len(articles) != 1:
            self.fail(f"expected 1 article, got {len(articles)}")

        a = articles[0]
        self.assertEqual(a["title"], "Breach Report")
        self.assertEqual(a["url"], "https://example.com/breach")
        self.assertEqual(a["id"], "https://example.com/breach")
        self.assertEqual(a["source"], "Krebs on Security")
        self.assertEqual(a["category"], "Security")
        self.assertEqual(a["snippet"], "Details of the breach")

    @patch("feeds.feedparser.parse")
    def test_html_stripped_from_snippet(self, mock_parse):
        entry = _make_entry(
            summary="<div><b>Bold</b> and <a href='#'>link</a> text</div>",
        )
        mock_parse.return_value = _make_feed(entries=[entry])

        cfg = self._cfg(categories={"Tech": ["https://t.com/feed"]})
        articles = fetch_rss_articles(cfg)

        self.assertNotIn("<", articles[0]["snippet"])
        self.assertNotIn(">", articles[0]["snippet"])

    @patch("feeds.feedparser.parse")
    def test_deduplicates_across_categories(self, mock_parse):
        entry = _make_entry(
            title="Shared Article",
            link="https://example.com/shared",
        )
        mock_parse.return_value = _make_feed(entries=[entry])

        cfg = self._cfg(
            categories={
                "Security": ["https://feed1.com/rss"],
                "Tech": ["https://feed2.com/rss"],
            }
        )
        articles = fetch_rss_articles(cfg)

        urls = [a["url"] for a in articles]
        self.assertEqual(len(urls), 1, "duplicate article should be removed")

    @patch("feeds.feedparser.parse")
    def test_max_articles_per_category(self, mock_parse):
        entries = [
            _make_entry(title=f"Art {i}", link=f"https://example.com/{i}")
            for i in range(10)
        ]
        mock_parse.return_value = _make_feed(entries=entries)

        cfg = self._cfg(
            categories={"Security": ["https://feed.com/rss"]},
            max_articles=3,
        )
        articles = fetch_rss_articles(cfg)

        self.assertEqual(len(articles), 3)

    @patch("feeds.feedparser.parse")
    def test_multiple_feeds_in_one_category(self, mock_parse):
        feed_a = _make_feed(
            title="Feed A",
            entries=[_make_entry(title="A1", link="https://a.com/1")],
        )
        feed_b = _make_feed(
            title="Feed B",
            entries=[_make_entry(title="B1", link="https://b.com/1")],
        )
        mock_parse.side_effect = [feed_a, feed_b]

        cfg = self._cfg(
            categories={
                "Security": ["https://a.com/feed", "https://b.com/feed"],
            }
        )
        articles = fetch_rss_articles(cfg)

        self.assertEqual(len(articles), 2)
        sources = {a["source"] for a in articles}
        self.assertEqual(sources, {"Feed A", "Feed B"})

    @patch("feeds.feedparser.parse")
    def test_empty_feed_returns_empty_list(self, mock_parse):
        mock_parse.return_value = _make_feed(entries=[])

        cfg = self._cfg(categories={"Tech": ["https://t.com/feed"]})
        articles = fetch_rss_articles(cfg)

        self.assertEqual(articles, [])

    @patch("feeds.feedparser.parse")
    def test_no_categories_returns_empty(self, mock_parse):
        cfg = self._cfg(categories={})
        articles = fetch_rss_articles(cfg)

        self.assertEqual(articles, [])
        mock_parse.assert_not_called()

    @patch("feeds.feedparser.parse")
    def test_snippet_truncated_to_800_chars(self, mock_parse):
        long_text = "x" * 1500
        entry = _make_entry(summary=long_text)
        mock_parse.return_value = _make_feed(entries=[entry])

        cfg = self._cfg(categories={"Tech": ["https://t.com/feed"]})
        articles = fetch_rss_articles(cfg)

        self.assertLessEqual(len(articles[0]["snippet"]), 800)

    @patch("feeds.feedparser.parse")
    def test_missing_title_defaults(self, mock_parse):
        entry = _make_entry()
        entry.title = ""
        entry.get = lambda k, d=None: {
            "title": "",
            "link": "https://example.com/1",
            "id": "https://example.com/1",
            "summary": "some text",
            "published": "",
        }.get(k, d)
        mock_parse.return_value = _make_feed(entries=[entry])

        cfg = self._cfg(categories={"Tech": ["https://t.com/feed"]})
        articles = fetch_rss_articles(cfg)

        self.assertEqual(articles[0]["title"], "(no title)")

    @patch("feeds.feedparser.parse")
    def test_default_max_articles_is_50(self, mock_parse):
        entries = [
            _make_entry(title=f"Art {i}", link=f"https://example.com/{i}")
            for i in range(60)
        ]
        mock_parse.return_value = _make_feed(entries=entries)

        cfg = {
            "feeds": {
                "categories": {"Tech": ["https://t.com/feed"]},
            }
        }
        articles = fetch_rss_articles(cfg)

        self.assertEqual(len(articles), 50)

    @patch("feeds.feedparser.parse")
    def test_old_articles_filtered_out(self, mock_parse):
        recent = _make_entry(
            title="New",
            link="https://example.com/new",
            published_parsed=_recent_time_struct(),
        )
        old = _make_entry(
            title="Old",
            link="https://example.com/old",
            published_parsed=_old_time_struct(),
        )
        mock_parse.return_value = _make_feed(entries=[recent, old])

        cfg = self._cfg(categories={"Tech": ["https://t.com/feed"]})
        articles = fetch_rss_articles(cfg)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "New")

    @patch("feeds.feedparser.parse")
    def test_article_without_published_parsed_is_included(self, mock_parse):
        entry = _make_entry(
            title="No date", link="https://example.com/nodate", published_parsed=None
        )
        entry.published_parsed = None
        mock_parse.return_value = _make_feed(entries=[entry])

        cfg = self._cfg(categories={"Tech": ["https://t.com/feed"]})
        articles = fetch_rss_articles(cfg)

        self.assertEqual(len(articles), 1)


if __name__ == "__main__":
    unittest.main()
