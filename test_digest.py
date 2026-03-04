"""Tests for digest.py — main digest flow."""

import json
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

import httpx

from datetime import datetime, timedelta, timezone

from digest import (
    init_db,
    filter_unseen,
    mark_seen,
    record_sent,
    cleanup_old_seen,
    score_relevance,
    summarize_articles,
    main,
)


def _article(id="1", title="A", url="https://a.com", source="S", category="Tech"):
    return {
        "id": id,
        "title": title,
        "url": url,
        "source": source,
        "category": category,
        "published": "",
        "snippet": "some text",
    }


# ── Database functions ───────────────────────────────────────────────────────


class TestInitDb(unittest.TestCase):

    @patch("digest.sqlite3.connect")
    def test_creates_tables_and_returns_connection(self, mock_connect):
        con = MagicMock()
        mock_connect.return_value = con

        result = init_db()

        self.assertEqual(con.execute.call_count, 2)
        sqls = [call[0][0] for call in con.execute.call_args_list]
        self.assertIn("CREATE TABLE IF NOT EXISTS seen_articles", sqls[0])
        self.assertIn("CREATE TABLE IF NOT EXISTS sent_articles", sqls[1])
        con.commit.assert_called_once()
        self.assertIs(result, con)


class TestFilterUnseen(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(
            "CREATE TABLE seen_articles (article_id TEXT PRIMARY KEY, seen_at TEXT)"
        )
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def test_returns_all_when_none_seen(self):
        articles = [_article(id="a"), _article(id="b")]

        result = filter_unseen(self.con, articles)

        self.assertEqual(len(result), 2)

    def test_filters_out_seen_articles(self):
        self.con.execute("INSERT INTO seen_articles VALUES (?, ?)", ("a", "2026-01-01"))
        self.con.commit()
        articles = [_article(id="a"), _article(id="b")]

        result = filter_unseen(self.con, articles)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "b")


class TestMarkSeen(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(
            "CREATE TABLE seen_articles (article_id TEXT PRIMARY KEY, seen_at TEXT)"
        )
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def test_inserts_article_ids(self):
        articles = [_article(id="x"), _article(id="y")]

        mark_seen(self.con, articles)

        rows = self.con.execute("SELECT article_id FROM seen_articles").fetchall()
        ids = {r[0] for r in rows}
        self.assertEqual(ids, {"x", "y"})

    def test_ignores_duplicates(self):
        self.con.execute("INSERT INTO seen_articles VALUES (?, ?)", ("x", "2026-01-01"))
        self.con.commit()

        mark_seen(self.con, [_article(id="x")])

        count = self.con.execute("SELECT count(*) FROM seen_articles").fetchone()[0]
        self.assertEqual(count, 1)


class TestRecordSent(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("""
            CREATE TABLE sent_articles (
                article_id TEXT, title TEXT, url TEXT, source TEXT,
                category TEXT, summary TEXT, reason TEXT, sent_at TEXT
            )
        """)
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def test_inserts_sent_articles(self):
        articles = [
            {
                **_article(id="a1", title="Title A", url="https://a.com"),
                "summary": "Sum A",
                "reason": "Reason A",
            },
            {
                **_article(id="a2", title="Title B", url="https://b.com"),
                "summary": "Sum B",
                "reason": "Reason B",
            },
        ]

        record_sent(self.con, articles)

        rows = self.con.execute("SELECT * FROM sent_articles").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "a1")
        self.assertEqual(rows[0][1], "Title A")
        self.assertIsNotNone(rows[0][7])  # sent_at

    def test_records_articles_without_summary_or_reason(self):
        articles = [_article(id="x")]

        record_sent(self.con, articles)

        rows = self.con.execute("SELECT * FROM sent_articles").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], "")  # summary defaults to ""
        self.assertEqual(rows[0][6], "")  # reason defaults to ""


class TestCleanupOldSeen(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(
            "CREATE TABLE seen_articles (article_id TEXT PRIMARY KEY, seen_at TEXT)"
        )
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def test_deletes_rows_older_than_days(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        self.con.execute("INSERT INTO seen_articles VALUES (?, ?)", ("old1", old))
        self.con.execute("INSERT INTO seen_articles VALUES (?, ?)", ("new1", recent))
        self.con.commit()

        cleanup_old_seen(self.con, days=7)

        rows = self.con.execute("SELECT article_id FROM seen_articles").fetchall()
        ids = {r[0] for r in rows}
        self.assertEqual(ids, {"new1"})

    def test_keeps_all_when_nothing_old(self):
        recent = datetime.now(timezone.utc).isoformat()
        self.con.execute("INSERT INTO seen_articles VALUES (?, ?)", ("a", recent))
        self.con.execute("INSERT INTO seen_articles VALUES (?, ?)", ("b", recent))
        self.con.commit()

        cleanup_old_seen(self.con, days=7)

        count = self.con.execute("SELECT count(*) FROM seen_articles").fetchone()[0]
        self.assertEqual(count, 2)

    def test_default_days_is_seven(self):
        eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        self.con.execute(
            "INSERT INTO seen_articles VALUES (?, ?)", ("old", eight_days_ago)
        )
        self.con.commit()

        cleanup_old_seen(self.con)

        count = self.con.execute("SELECT count(*) FROM seen_articles").fetchone()[0]
        self.assertEqual(count, 0)


# ── Claude functions ─────────────────────────────────────────────────────────


def _mock_client(response_json):
    """Build a mock Anthropic client that returns the given JSON."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(response_json))]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    client.messages.create.return_value = msg
    return client


class TestScoreRelevance(unittest.TestCase):

    def test_returns_relevant_articles_with_reasons(self):
        articles = [_article(id="1"), _article(id="2")]
        response = [
            {"id": "1", "relevant": True, "reason": "matches interests"},
            {"id": "2", "relevant": False, "reason": "not relevant"},
        ]
        client = _mock_client(response)
        cfg = {"interests": "security", "preferences": {}}

        result = score_relevance(client, cfg, articles)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "1")
        self.assertEqual(result[0]["reason"], "matches interests")

    def test_includes_preference_examples_in_prompt(self):
        articles = [_article()]
        response = [{"id": "1", "relevant": True, "reason": "good"}]
        client = _mock_client(response)
        cfg = {
            "interests": "security",
            "preferences": {
                "positive_examples": ["Good article"],
                "negative_examples": ["Bad article"],
                "high_priority_topics": ["NIS2"],
                "low_priority_topics": ["crypto"],
            },
        }

        score_relevance(client, cfg, articles)

        call_args = client.messages.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        self.assertIn("Good article", prompt)
        self.assertIn("Bad article", prompt)
        self.assertIn("NIS2", prompt)
        self.assertIn("crypto", prompt)

    def test_uses_max_relevant_from_config(self):
        articles = [_article(id=str(i)) for i in range(10)]
        response = [
            {"id": str(i), "relevant": True, "reason": "good"} for i in range(10)
        ]
        client = _mock_client(response)
        cfg = {
            "interests": "security",
            "preferences": {},
            "feeds": {"max_relevant_per_category": 2},
        }

        result = score_relevance(client, cfg, articles)

        self.assertEqual(len(result), 2)

    def test_max_relevant_defaults_to_five(self):
        articles = [_article(id=str(i)) for i in range(10)]
        response = [
            {"id": str(i), "relevant": True, "reason": "good"} for i in range(10)
        ]
        client = _mock_client(response)
        cfg = {"interests": "security", "preferences": {}}

        result = score_relevance(client, cfg, articles)

        self.assertEqual(len(result), 5)

    def test_max_relevant_in_system_prompt(self):
        articles = [_article()]
        response = [{"id": "1", "relevant": True, "reason": "good"}]
        client = _mock_client(response)
        cfg = {
            "interests": "security",
            "preferences": {},
            "feeds": {"max_relevant_per_category": 7},
        }

        score_relevance(client, cfg, articles)

        call_args = client.messages.create.call_args
        system = call_args[1]["system"]
        self.assertIn("7", system)


class TestSummarizeArticles(unittest.TestCase):

    def test_returns_articles_with_summaries(self):
        articles = [_article(id="1"), _article(id="2")]
        response = [
            {"id": "1", "summary": "Summary one."},
            {"id": "2", "summary": "Summary two."},
        ]
        client = _mock_client(response)

        result = summarize_articles(client, articles)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["summary"], "Summary one.")
        self.assertEqual(result[1]["summary"], "Summary two.")

    def test_missing_summary_gets_default(self):
        articles = [_article(id="1")]
        client = _mock_client([])

        result = summarize_articles(client, articles)

        self.assertEqual(result[0]["summary"], "No summary available.")


# ── Main flow ────────────────────────────────────────────────────────────────


class TestMain(unittest.TestCase):

    @patch("digest.cleanup_old_seen")
    @patch("digest.record_sent")
    @patch("digest.send_digest")
    @patch("digest.render_email", return_value="<html>digest</html>")
    @patch("digest.summarize_articles")
    @patch("digest.score_relevance")
    @patch("digest.filter_unseen")
    @patch("digest.mark_seen")
    @patch("digest.init_db")
    @patch("digest.fetch_rss_articles")
    @patch("digest.load_config")
    @patch("digest.anthropic.Anthropic")
    def test_main_full_flow(
        self,
        mock_anthropic,
        mock_load_config,
        mock_fetch,
        mock_init_db,
        mock_mark_seen,
        mock_filter_unseen,
        mock_score,
        mock_summarize,
        mock_render,
        mock_send,
        mock_record_sent,
        mock_cleanup,
    ):
        cfg = {
            "anthropic": {"api_key": "test"},
            "feeds": {"categories": {"Tech": ["https://t.com/feed"]}},
        }
        mock_load_config.return_value = cfg
        mock_init_db.return_value = MagicMock()

        articles = [_article()]
        mock_fetch.return_value = articles
        mock_filter_unseen.return_value = articles
        mock_score.return_value = articles
        mock_summarize.return_value = articles

        main()

        mock_fetch.assert_called_once_with(cfg)
        mock_filter_unseen.assert_called_once()
        mock_score.assert_called_once()
        mock_summarize.assert_called_once()
        mock_send.assert_called_once()
        mock_record_sent.assert_called_once()
        mock_mark_seen.assert_called_once()
        mock_cleanup.assert_called_once()

    @patch("digest.init_db")
    @patch("digest.fetch_rss_articles", return_value=[])
    @patch("digest.load_config")
    @patch("digest.anthropic.Anthropic")
    def test_main_no_articles_exits_early(
        self,
        mock_anthropic,
        mock_load_config,
        mock_fetch,
        mock_init_db,
    ):
        mock_load_config.return_value = {"anthropic": {"api_key": "test"}}
        mock_init_db.return_value = MagicMock()

        main()

        mock_fetch.assert_called_once()

    @patch("digest.mark_seen")
    @patch("digest.score_relevance")
    @patch("digest.filter_unseen", return_value=[])
    @patch("digest.init_db")
    @patch("digest.fetch_rss_articles")
    @patch("digest.load_config")
    @patch("digest.anthropic.Anthropic")
    def test_main_nothing_new_exits(
        self,
        mock_anthropic,
        mock_load_config,
        mock_fetch,
        mock_init_db,
        mock_filter_unseen,
        mock_score,
        mock_mark_seen,
    ):
        mock_load_config.return_value = {"anthropic": {"api_key": "test"}}
        mock_init_db.return_value = MagicMock()
        mock_fetch.return_value = [_article()]

        main()

        mock_score.assert_not_called()

    @patch("digest.summarize_articles")
    @patch("digest.mark_seen")
    @patch("digest.score_relevance", return_value=[])
    @patch("digest.filter_unseen")
    @patch("digest.init_db")
    @patch("digest.fetch_rss_articles")
    @patch("digest.load_config")
    @patch("digest.anthropic.Anthropic")
    def test_main_no_relevant_articles_marks_seen_and_exits(
        self,
        mock_anthropic,
        mock_load_config,
        mock_fetch,
        mock_init_db,
        mock_filter_unseen,
        mock_score,
        mock_mark_seen,
        mock_summarize,
    ):
        mock_load_config.return_value = {"anthropic": {"api_key": "test"}}
        mock_init_db.return_value = MagicMock()
        articles = [_article()]
        mock_fetch.return_value = articles
        mock_filter_unseen.return_value = articles

        main()

        mock_mark_seen.assert_called_once()
        mock_summarize.assert_not_called()


class TestTimeoutHandling(unittest.TestCase):

    def test_score_relevance_timeout_returns_empty(self):
        client = MagicMock()
        client.messages.create.side_effect = httpx.ReadTimeout("timed out")
        articles = [_article(id="1")]
        cfg = {"interests": "security", "preferences": {}}

        result = score_relevance(client, cfg, articles)

        self.assertEqual(result, [])

    def test_summarize_timeout_returns_no_summaries(self):
        client = MagicMock()
        client.messages.create.side_effect = httpx.ReadTimeout("timed out")
        articles = [_article(id="1")]

        result = summarize_articles(client, articles)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["summary"], "No summary available.")

    def test_score_relevance_json_error_returns_empty(self):
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text="not json at all")]
        msg.stop_reason = "end_turn"
        msg.usage = MagicMock(input_tokens=100, output_tokens=50)
        client.messages.create.return_value = msg
        cfg = {"interests": "security", "preferences": {}}

        result = score_relevance(client, cfg, [_article(id="1")])

        self.assertEqual(result, [])

    def test_summarize_json_error_returns_empty_summaries(self):
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text="not json")]
        msg.stop_reason = "end_turn"
        msg.usage = MagicMock(input_tokens=100, output_tokens=50)
        client.messages.create.return_value = msg

        result = summarize_articles(client, [_article(id="1")])

        self.assertEqual(result[0]["summary"], "No summary available.")


class TestMainEmailGuard(unittest.TestCase):

    @patch("digest.send_digest")
    @patch("digest.render_email", return_value="<html>digest</html>")
    @patch("digest.summarize_articles")
    @patch("digest.score_relevance")
    @patch("digest.filter_unseen")
    @patch("digest.mark_seen")
    @patch("digest.init_db")
    @patch("digest.fetch_rss_articles")
    @patch("digest.load_config")
    @patch("digest.anthropic.Anthropic")
    def test_no_email_when_all_summaries_missing(
        self,
        mock_anthropic,
        mock_load_config,
        mock_fetch,
        mock_init_db,
        mock_mark_seen,
        mock_filter_unseen,
        mock_score,
        mock_summarize,
        mock_render,
        mock_send,
    ):
        cfg = {
            "anthropic": {"api_key": "test"},
            "feeds": {"categories": {"Tech": ["https://t.com/feed"]}},
        }
        mock_load_config.return_value = cfg
        mock_init_db.return_value = MagicMock()

        articles = [_article()]
        mock_fetch.return_value = articles
        mock_filter_unseen.return_value = articles
        mock_score.return_value = articles
        mock_summarize.return_value = [
            {**_article(), "summary": "No summary available."}
        ]

        main()

        mock_send.assert_not_called()


class TestFeedlyFunctionsRemoved(unittest.TestCase):

    def test_no_fetch_feedly_articles(self):
        import digest

        self.assertFalse(
            hasattr(digest, "fetch_feedly_articles"),
            "fetch_feedly_articles should be removed",
        )

    def test_no_mark_as_read_on_feedly(self):
        import digest

        self.assertFalse(
            hasattr(digest, "mark_as_read_on_feedly"),
            "mark_as_read_on_feedly should be removed",
        )

    def test_uses_fetch_rss_articles(self):
        import digest

        self.assertTrue(
            hasattr(digest, "fetch_rss_articles"),
            "digest should import fetch_rss_articles from feeds",
        )


if __name__ == "__main__":
    main()
