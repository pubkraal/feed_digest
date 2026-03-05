"""Tests for linkedin.py — weekly LinkedIn post generator."""

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from linkedin import (
    fetch_recent_sent,
    select_sector_articles,
    generate_linkedin_post,
    format_post_as_html,
    cleanup_old_sent,
    main,
)


def _sent_row(
    article_id="1",
    title="Title",
    url="https://example.com",
    source="Source",
    category="Security",
    summary="Summary text",
    reason="Reason text",
    sent_at=None,
):
    if sent_at is None:
        sent_at = datetime.now(timezone.utc).isoformat()
    return (article_id, title, url, source, category, summary, reason, sent_at)


def _make_db():
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE sent_articles (
            article_id TEXT, title TEXT, url TEXT, source TEXT,
            category TEXT, summary TEXT, reason TEXT, sent_at TEXT
        )
    """)
    con.commit()
    return con


# ── fetch_recent_sent ────────────────────────────────────────────────────────


class TestFetchRecentSent(unittest.TestCase):

    def test_returns_articles_from_past_seven_days(self):
        con = _make_db()
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        con.execute(
            "INSERT INTO sent_articles VALUES (?,?,?,?,?,?,?,?)",
            _sent_row(article_id="new", sent_at=recent),
        )
        con.execute(
            "INSERT INTO sent_articles VALUES (?,?,?,?,?,?,?,?)",
            _sent_row(article_id="old", sent_at=old),
        )
        con.commit()

        result = fetch_recent_sent(con)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "new")

    def test_returns_empty_when_no_articles(self):
        con = _make_db()

        result = fetch_recent_sent(con)

        self.assertEqual(result, [])

    def test_returns_all_fields(self):
        con = _make_db()
        con.execute(
            "INSERT INTO sent_articles VALUES (?,?,?,?,?,?,?,?)",
            _sent_row(
                article_id="x",
                title="T",
                url="https://t.com",
                source="S",
                category="C",
                summary="Sum",
                reason="R",
            ),
        )
        con.commit()

        result = fetch_recent_sent(con)

        self.assertEqual(result[0]["title"], "T")
        self.assertEqual(result[0]["url"], "https://t.com")
        self.assertEqual(result[0]["source"], "S")
        self.assertEqual(result[0]["category"], "C")
        self.assertEqual(result[0]["summary"], "Sum")


# ── select_sector_articles ───────────────────────────────────────────────────


def _mock_client(response_text):
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    client.messages.create.return_value = msg
    return client


class TestSelectSectorArticles(unittest.TestCase):

    def test_returns_selected_article_ids(self):
        articles = [
            {"id": "1", "title": "A", "summary": "Sum A"},
            {"id": "2", "title": "B", "summary": "Sum B"},
            {"id": "3", "title": "C", "summary": "Sum C"},
        ]
        response = json.dumps(["1", "3"])
        client = _mock_client(response)

        result = select_sector_articles(client, articles, "energy")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "1")
        self.assertEqual(result[1]["id"], "3")

    def test_passes_sector_in_prompt(self):
        articles = [{"id": "1", "title": "A", "summary": "Sum"}]
        client = _mock_client(json.dumps(["1"]))

        select_sector_articles(client, articles, "finance")

        call_args = client.messages.create.call_args
        system = call_args[1]["system"]
        self.assertIn("finance", system)

    def test_returns_empty_on_json_error(self):
        articles = [{"id": "1", "title": "A", "summary": "Sum"}]
        client = _mock_client("not json")

        result = select_sector_articles(client, articles, "energy")

        self.assertEqual(result, [])

    def test_returns_empty_on_api_error(self):
        articles = [{"id": "1", "title": "A", "summary": "Sum"}]
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")

        result = select_sector_articles(client, articles, "energy")

        self.assertEqual(result, [])


# ── generate_linkedin_post ───────────────────────────────────────────────────


class TestGenerateLinkedinPost(unittest.TestCase):

    def test_returns_post_text(self):
        articles = [
            {"id": "1", "title": "A", "url": "https://a.com", "summary": "Sum A"},
        ]
        client = _mock_client("Here is a great LinkedIn post about security.")

        result = generate_linkedin_post(client, articles, "energy")

        self.assertEqual(result, "Here is a great LinkedIn post about security.")

    def test_passes_sector_in_system_prompt(self):
        articles = [{"id": "1", "title": "A", "url": "https://a.com", "summary": "S"}]
        client = _mock_client("Post text")

        generate_linkedin_post(client, articles, "finance")

        call_args = client.messages.create.call_args
        system = call_args[1]["system"]
        self.assertIn("finance", system)

    def test_returns_empty_on_api_error(self):
        articles = [{"id": "1", "title": "A", "url": "https://a.com", "summary": "S"}]
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")

        result = generate_linkedin_post(client, articles, "energy")

        self.assertEqual(result, "")

    def test_prompt_includes_action_recommendations(self):
        articles = [{"id": "1", "title": "A", "url": "https://a.com", "summary": "S"}]
        client = _mock_client("Post text")

        generate_linkedin_post(client, articles, "energy")

        call_args = client.messages.create.call_args
        system = call_args[1]["system"]
        self.assertIn("actionable", system.lower())
        self.assertIn("recommend", system.lower())

    def test_prompt_emphasizes_positive_tone(self):
        from linkedin import LINKEDIN_POST_SYSTEM

        prompt = LINKEDIN_POST_SYSTEM.lower()
        self.assertIn("positive", prompt)
        self.assertNotIn("alarmist", prompt)


# ── format_post_as_html ──────────────────────────────────────────────────────


class TestFormatPostAsHtml(unittest.TestCase):

    def test_wraps_in_html_document(self):
        post = "Hello world"

        result = format_post_as_html(post)

        self.assertIn("<html", result)
        self.assertIn("</html>", result)
        self.assertIn("Hello world", result)

    def test_converts_newlines_to_br(self):
        post = "Line one\nLine two\nLine three"

        result = format_post_as_html(post)

        self.assertIn("Line one<br>", result)
        self.assertIn("Line two<br>", result)

    def test_preserves_paragraph_breaks(self):
        post = "Paragraph one.\n\nParagraph two."

        result = format_post_as_html(post)

        self.assertIn("Paragraph one.", result)
        self.assertIn("Paragraph two.", result)

    def test_escapes_html_entities(self):
        post = "Use <script> & run"

        result = format_post_as_html(post)

        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertIn("&amp;", result)


# ── cleanup_old_sent ─────────────────────────────────────────────────────────


class TestCleanupOldSent(unittest.TestCase):

    def test_deletes_rows_older_than_days(self):
        con = _make_db()
        old = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        con.execute(
            "INSERT INTO sent_articles VALUES (?,?,?,?,?,?,?,?)",
            _sent_row(article_id="old", sent_at=old),
        )
        con.execute(
            "INSERT INTO sent_articles VALUES (?,?,?,?,?,?,?,?)",
            _sent_row(article_id="new", sent_at=recent),
        )
        con.commit()

        cleanup_old_sent(con, days=30)

        rows = con.execute("SELECT article_id FROM sent_articles").fetchall()
        ids = {r[0] for r in rows}
        self.assertEqual(ids, {"new"})

    def test_default_days_is_thirty(self):
        con = _make_db()
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        con.execute(
            "INSERT INTO sent_articles VALUES (?,?,?,?,?,?,?,?)",
            _sent_row(article_id="old", sent_at=old),
        )
        con.commit()

        cleanup_old_sent(con)

        count = con.execute("SELECT count(*) FROM sent_articles").fetchone()[0]
        self.assertEqual(count, 0)


# ── main flow ────────────────────────────────────────────────────────────────


class TestLinkedinMain(unittest.TestCase):

    @patch("linkedin.cleanup_old_sent")
    @patch("linkedin.send_one")
    @patch("linkedin.generate_linkedin_post")
    @patch("linkedin.select_sector_articles")
    @patch("linkedin.random.choice", return_value="energy")
    @patch("linkedin.fetch_recent_sent")
    @patch("linkedin.init_db")
    @patch("linkedin.load_config")
    @patch("linkedin.anthropic.Anthropic")
    def test_full_flow(
        self,
        mock_anthropic,
        mock_load_config,
        mock_init_db,
        mock_fetch_recent,
        mock_choice,
        mock_select,
        mock_generate,
        mock_send_one,
        mock_cleanup,
    ):
        cfg = {
            "anthropic": {"api_key": "test"},
            "mailgun": {"api_key": "mg-key", "domain": "mg.example.com"},
            "email": {"from": "digest@example.com"},
        }
        mock_load_config.return_value = cfg
        mock_init_db.return_value = MagicMock()
        mock_fetch_recent.return_value = [
            {"id": "1", "title": "A", "url": "https://a.com", "summary": "Sum"},
        ]
        mock_select.return_value = [
            {"id": "1", "title": "A", "url": "https://a.com", "summary": "Sum"},
        ]
        mock_generate.return_value = "LinkedIn post text"

        main()

        mock_fetch_recent.assert_called_once()
        mock_select.assert_called_once()
        mock_generate.assert_called_once()
        mock_send_one.assert_called_once()
        # Verify HTML is sent, not plain text
        sent_html = mock_send_one.call_args[0][5]
        self.assertIn("<html", sent_html)
        self.assertIn("LinkedIn post text", sent_html)
        mock_cleanup.assert_called_once()

    @patch("linkedin.cleanup_old_sent")
    @patch("linkedin.send_one")
    @patch("linkedin.fetch_recent_sent", return_value=[])
    @patch("linkedin.init_db")
    @patch("linkedin.load_config")
    @patch("linkedin.anthropic.Anthropic")
    def test_exits_early_when_no_articles(
        self,
        mock_anthropic,
        mock_load_config,
        mock_init_db,
        mock_fetch_recent,
        mock_send_one,
        mock_cleanup,
    ):
        mock_load_config.return_value = {"anthropic": {"api_key": "test"}}
        mock_init_db.return_value = MagicMock()

        main()

        mock_send_one.assert_not_called()
        mock_cleanup.assert_called_once()

    @patch("linkedin.cleanup_old_sent")
    @patch("linkedin.send_one")
    @patch("linkedin.generate_linkedin_post", return_value="")
    @patch("linkedin.select_sector_articles")
    @patch("linkedin.random.choice", return_value="finance")
    @patch("linkedin.fetch_recent_sent")
    @patch("linkedin.init_db")
    @patch("linkedin.load_config")
    @patch("linkedin.anthropic.Anthropic")
    def test_skips_email_when_post_empty(
        self,
        mock_anthropic,
        mock_load_config,
        mock_init_db,
        mock_fetch_recent,
        mock_choice,
        mock_select,
        mock_generate,
        mock_send_one,
        mock_cleanup,
    ):
        cfg = {
            "anthropic": {"api_key": "test"},
            "mailgun": {"api_key": "mg-key", "domain": "mg.example.com"},
            "email": {"from": "digest@example.com"},
        }
        mock_load_config.return_value = cfg
        mock_init_db.return_value = MagicMock()
        mock_fetch_recent.return_value = [
            {"id": "1", "title": "A", "url": "https://a.com", "summary": "Sum"},
        ]
        mock_select.return_value = [
            {"id": "1", "title": "A", "url": "https://a.com", "summary": "Sum"},
        ]

        main()

        mock_send_one.assert_not_called()

    @patch("linkedin.cleanup_old_sent")
    @patch("linkedin.send_one")
    @patch("linkedin.select_sector_articles", return_value=[])
    @patch("linkedin.random.choice", return_value="energy")
    @patch("linkedin.fetch_recent_sent")
    @patch("linkedin.init_db")
    @patch("linkedin.load_config")
    @patch("linkedin.anthropic.Anthropic")
    def test_skips_generate_when_no_sector_articles(
        self,
        mock_anthropic,
        mock_load_config,
        mock_init_db,
        mock_fetch_recent,
        mock_choice,
        mock_select,
        mock_send_one,
        mock_cleanup,
    ):
        cfg = {
            "anthropic": {"api_key": "test"},
            "mailgun": {"api_key": "mg-key", "domain": "mg.example.com"},
            "email": {"from": "digest@example.com"},
        }
        mock_load_config.return_value = cfg
        mock_init_db.return_value = MagicMock()
        mock_fetch_recent.return_value = [
            {"id": "1", "title": "A", "url": "https://a.com", "summary": "Sum"},
        ]

        main()

        mock_send_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
