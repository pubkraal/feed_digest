"""
mailer.py — Send digest via Mailgun.
"""

import logging
import requests

log = logging.getLogger(__name__)


def send_digest(cfg: dict, subject: str, html: str):
    mg = cfg["mailgun"]
    api_key  = mg["api_key"]
    domain   = mg["domain"]
    to_addr  = cfg["email"]["to"]
    from_addr = cfg["email"].get("from", f"digest@{domain}")

    resp = requests.post(
        f"https://api.mailgun.net/v3/{domain}/messages",
        auth=("api", api_key),
        data={
            "from":    from_addr,
            "to":      to_addr,
            "subject": subject,
            "html":    html,
        },
        timeout=15,
    )

    if resp.status_code == 200:
        log.info(f"Mailgun accepted message (id={resp.json().get('id')})")
    else:
        log.error(f"Mailgun error {resp.status_code}: {resp.text}")
        resp.raise_for_status()
