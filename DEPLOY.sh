#!/bin/sh
# /usr/local/etc/rc.d/feed_digest
# FreeBSD rc(8) service — runs the digest on a schedule via periodic(8) or cron.
# This file is provided for reference; the simplest deployment is a cron job.

# ── Option A: cron (recommended, simplest) ───────────────────────────────────
#
# Run `crontab -e` as the service user and add:
#
#   # Morning digest at 07:00
#   0 7 * * * /usr/local/bin/python3 /opt/feed-digest/digest.py >> /var/log/feed-digest.log 2>&1
#
#   # Daytime runs at 11:00, 15:00, 19:00
#   0 11,15,19 * * * /usr/local/bin/python3 /opt/feed-digest/digest.py >> /var/log/feed-digest.log 2>&1
#
# ── Option B: periodic(8) ────────────────────────────────────────────────────
#
# Create /usr/local/etc/periodic/daily/500.feed-digest:
#   #!/bin/sh
#   /usr/local/bin/python3 /opt/feed-digest/digest.py
#
# chmod +x /usr/local/etc/periodic/daily/500.feed-digest
#
# ── Installation steps ───────────────────────────────────────────────────────
#
#   pkg install python311 py311-pip
#   mkdir -p /opt/feed-digest
#   cp *.py requirements.txt /opt/feed-digest/
#   cp config.yaml.example /opt/feed-digest/config.yaml
#   # Edit config.yaml with your credentials
#   cd /opt/feed-digest && pip install -r requirements.txt
#   # Test run:
#   python3 /opt/feed-digest/digest.py
#
# ── Log rotation (/etc/newsyslog.conf.d/feed-digest.conf) ────────────────────
#
#   /var/log/feed-digest.log    644  7  1024  *  JC
