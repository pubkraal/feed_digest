"""Tests for templates.py — email rendering."""

import unittest

from templates import render_email


def _article(
    id="1",
    title="Test Article",
    url="https://example.com",
    source="TestSource",
    category="Security",
    summary="Article summary.",
    reason="Relevant reason.",
    published="",
):
    return {
        "id": id,
        "title": title,
        "url": url,
        "source": source,
        "category": category,
        "summary": summary,
        "reason": reason,
        "published": published,
    }


class TestRenderEmailActionItems(unittest.TestCase):

    def test_action_items_section_rendered(self):
        articles = [_article()]
        action_items = [
            {"action": "Patch your VPN appliances immediately.", "source_url": None, "source_title": None},
        ]

        html = render_email(articles, {}, action_items=action_items)

        self.assertIn("Action Items", html)
        self.assertIn("Patch your VPN appliances immediately.", html)

    def test_action_item_with_source_link(self):
        articles = [_article()]
        action_items = [
            {
                "action": "Review your MFA configuration.",
                "source_url": "https://example.com/mfa-bypass",
                "source_title": "New MFA bypass technique discovered",
            },
        ]

        html = render_email(articles, {}, action_items=action_items)

        self.assertIn("Review your MFA configuration.", html)
        self.assertIn('href="https://example.com/mfa-bypass"', html)
        self.assertIn("New MFA bypass technique discovered", html)

    def test_multiple_action_items_rendered(self):
        articles = [_article()]
        action_items = [
            {"action": "First action.", "source_url": None, "source_title": None},
            {"action": "Second action.", "source_url": None, "source_title": None},
            {"action": "Third action.", "source_url": None, "source_title": None},
        ]

        html = render_email(articles, {}, action_items=action_items)

        self.assertIn("First action.", html)
        self.assertIn("Second action.", html)
        self.assertIn("Third action.", html)


class TestRenderEmailFellThrough(unittest.TestCase):

    def test_fell_through_section_from_action_items_with_sources(self):
        articles = [_article()]
        action_items = [
            {"action": "Action without source.", "source_url": None, "source_title": None},
            {
                "action": "Action with source.",
                "source_url": "https://example.com/fell",
                "source_title": "Fell Through Article",
            },
        ]

        html = render_email(articles, {}, action_items=action_items)

        self.assertIn("Fell Through the Cracks", html)
        self.assertIn('href="https://example.com/fell"', html)
        self.assertIn("Fell Through Article", html)

    def test_no_fell_through_when_no_source_urls(self):
        articles = [_article()]
        action_items = [
            {"action": "Action one.", "source_url": None, "source_title": None},
            {"action": "Action two.", "source_url": None, "source_title": None},
        ]

        html = render_email(articles, {}, action_items=action_items)

        self.assertNotIn("Fell Through the Cracks", html)

    def test_no_fell_through_when_no_action_items(self):
        articles = [_article()]

        html = render_email(articles, {})

        self.assertNotIn("Fell Through the Cracks", html)


class TestRenderEmailBackwardCompatibility(unittest.TestCase):

    def test_renders_without_action_items(self):
        articles = [_article()]

        html = render_email(articles, {})

        self.assertIn("Test Article", html)
        self.assertNotIn("Action Items", html)

    def test_renders_with_empty_action_items(self):
        articles = [_article()]

        html = render_email(articles, {}, action_items=[])

        self.assertIn("Test Article", html)
        self.assertNotIn("Action Items", html)

    def test_escapes_action_text(self):
        articles = [_article()]
        action_items = [
            {"action": "Check <script>alert('xss')</script> configs.", "source_url": None, "source_title": None},
        ]

        html = render_email(articles, {}, action_items=action_items)

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
