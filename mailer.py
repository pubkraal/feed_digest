"""
mailer.py — Send digest via Mailgun.
"""

import logging
import requests

log = logging.getLogger(__name__)


def send_digest(cfg: dict, subject: str, html: str):
    mg = cfg["mailgun"]
    api_key = mg["api_key"]
    domain = mg["domain"]
    to_raw = cfg["email"]["to"]
    recipients = to_raw if isinstance(to_raw, list) else [to_raw]
    from_addr = cfg["email"].get("from", f"digest@{domain}")

    for recipient in recipients:
        send_one(api_key, domain, from_addr, recipient, subject, html)


def send_one(
    api_key: str, domain: str, from_addr: str, to_addr: str, subject: str, html: str
):
    resp = requests.post(
        f"https://api.eu.mailgun.net/v3/{domain}/messages",
        auth=("api", api_key),
        data={
            "from": from_addr,
            "to": to_addr,
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )

    if resp.status_code == 200:
        log.info(f"Mailgun accepted message to {to_addr} (id={resp.json().get('id')})")
    else:
        log.error(f"Mailgun error {resp.status_code} for {to_addr}: {resp.text}")
