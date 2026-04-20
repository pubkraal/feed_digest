"""
templates.py — Render the HTML digest email.
"""

from collections import defaultdict
from datetime import datetime


def render_email(
    articles: list[dict],
    cfg: dict,
    intro: str = "",
    action_items: list[dict] | None = None,
) -> str:
    now = datetime.now().strftime("%A, %d %B %Y — %H:%M")
    count = len(articles)

    # Group articles by category, preserving insertion order.
    by_category: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        by_category[a.get("category", "Uncategorized")].append(a)

    sections = ""
    for category, items in by_category.items():
        sections += f"""
        <tr><td style="padding:28px 32px 8px;">
          <h2 style="margin:0;font-size:13px;font-weight:700;color:#0078FF;
                      text-transform:uppercase;letter-spacing:.1em;">
            {_esc(category)} <span style="color:#94a3b8;">({len(items)})</span>
          </h2>
          <div style="width:32px;height:2px;background:#0078FF;margin-top:8px;"></div>
        </td></tr>
        <tr><td style="padding:12px 32px 16px;">"""

        for a in items:
            pub = ""
            if a.get("published"):
                try:
                    pub = datetime.fromtimestamp(a["published"] / 1000).strftime(
                        "%d %b %Y"
                    )
                except (TypeError, ValueError, OSError):
                    pass

            reason_html = ""
            if a.get("reason"):
                reason_html = f"""
              <div style="background:#f0f7ff;border-left:2px solid #0078FF;
                          padding:10px 14px;margin-bottom:12px;border-radius:0 6px 6px 0;">
                <span style="font-size:10px;font-weight:600;color:#0078FF;
                             text-transform:uppercase;letter-spacing:.08em;">Why this matters</span>
                <p style="margin:4px 0 0;font-size:13px;line-height:1.55;color:#334155;">
                  {_esc(a['reason'])}
                </p>
              </div>"""

            sections += f"""
          <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;
                      padding:22px 26px;margin-bottom:14px;">
            <div style="font-size:11px;color:#94a3b8;font-weight:500;
                        letter-spacing:.04em;margin-bottom:6px;">
              {_esc(a['source'])} {f'&middot; {pub}' if pub else ''}
            </div>
            <h3 style="margin:0 0 10px;font-size:17px;font-weight:600;line-height:1.4;">
              <a href="{_esc(a['url'])}" style="color:#0f172a;text-decoration:none;">
                {_esc(a['title'])}
              </a>
            </h3>{reason_html}
            <p style="margin:0;font-size:14px;line-height:1.7;color:#475569;">
              {_esc(a['summary'])}
            </p>
            <a href="{_esc(a['url'])}"
               style="display:inline-block;margin-top:14px;font-size:13px;
                      font-weight:500;color:#0078FF;text-decoration:none;">
              Read full article &rarr;
            </a>
          </div>"""

        sections += """
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Feed Digest</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
        rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Inter',-apple-system,
             BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
    <tr><td align="center" style="padding:40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" role="presentation"
             style="max-width:600px;width:100%;">

        <!-- Header -->
        <tr><td style="background:#ffffff;border-radius:12px 12px 0 0;
                        padding:32px 32px 24px;border-top:3px solid #0078FF;">
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
            <tr>
              <td>
                <p style="margin:0 0 4px;font-size:13px;font-weight:600;letter-spacing:.08em;
                          text-transform:uppercase;color:#94a3b8;">Feed Digest</p>
                <p style="margin:0;font-size:12px;color:#94a3b8;">{now}</p>
              </td>
              <td align="right" valign="top">
                <span style="display:inline-block;background:#f0f7ff;color:#0078FF;
                             font-size:12px;font-weight:600;padding:5px 12px;
                             border-radius:20px;">
                  {count} article{'s' if count != 1 else ''}
                </span>
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- Intro -->
        {f'''<tr><td style="background:#ffffff;padding:0 32px 24px;">
          <p style="margin:0;font-size:15px;line-height:1.65;color:#475569;
                    font-style:italic;border-top:1px solid #e2e8f0;padding-top:20px;">
            {_esc(intro)}
          </p>
        </td></tr>''' if intro else ''}

        {_render_action_items(action_items)}

        <!-- Category sections -->
        <tr><td style="background:#ffffff;">
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
            {sections}
          </table>
        </td></tr>

        {_render_fell_through(action_items)}

        <!-- Footer -->
        <tr><td style="background:#ffffff;border-radius:0 0 12px 12px;
                        padding:24px 32px;text-align:center;
                        border-top:1px solid #e2e8f0;">
          <p style="margin:0 0 6px;font-size:11px;color:#94a3b8;letter-spacing:.04em;">
            Curated by John Kraal
          </p>
          <p style="margin:0;font-size:14px;">
            <span style="color:#0f172a;font-weight:600;">caas</span><span style="color:#0078FF;font-weight:600;">&nbsp;&#9679; consultancy</span>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _render_action_items(action_items: list[dict] | None) -> str:
    if not action_items:
        return ""

    items_html = ""
    for item in action_items:
        action_text = _esc(item["action"])
        source_html = ""
        if item.get("source_url") and item.get("source_title"):
            source_html = (
                f' <a href="{_esc(item["source_url"])}" '
                f'style="color:#0078FF;text-decoration:none;font-size:12px;">'
                f'({_esc(item["source_title"])})</a>'
            )
        items_html += f"""
              <li style="margin-bottom:8px;font-size:14px;line-height:1.55;color:#334155;">
                {action_text}{source_html}
              </li>"""

    return f"""<tr><td style="background:#ffffff;padding:20px 32px 8px;">
          <div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:10px;
                      padding:20px 24px;">
            <h2 style="margin:0 0 12px;font-size:12px;font-weight:700;color:#92400e;
                        text-transform:uppercase;letter-spacing:.1em;">
              Action Items
            </h2>
            <ul style="margin:0;padding-left:20px;">{items_html}
            </ul>
          </div>
        </td></tr>"""


def _render_fell_through(action_items: list[dict] | None) -> str:
    if not action_items:
        return ""

    fell_through = [
        item for item in action_items
        if item.get("source_url") and item.get("source_title")
    ]
    if not fell_through:
        return ""

    links_html = ""
    for item in fell_through:
        links_html += f"""
              <li style="margin-bottom:6px;font-size:13px;line-height:1.55;">
                <a href="{_esc(item["source_url"])}"
                   style="color:#0078FF;text-decoration:none;">
                  {_esc(item["source_title"])}
                </a>
              </li>"""

    return f"""<tr><td style="background:#ffffff;padding:8px 32px 20px;">
          <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                      padding:18px 24px;">
            <h2 style="margin:0 0 10px;font-size:12px;font-weight:700;color:#94a3b8;
                        text-transform:uppercase;letter-spacing:.1em;">
              Fell Through the Cracks
            </h2>
            <p style="margin:0 0 8px;font-size:12px;color:#94a3b8;">
              Articles referenced in today&rsquo;s action items that didn&rsquo;t make the main digest:
            </p>
            <ul style="margin:0;padding-left:20px;">{links_html}
            </ul>
          </div>
        </td></tr>"""


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
