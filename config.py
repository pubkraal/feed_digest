"""
config.py — Load configuration from config.yaml or environment variables.
"""

import os
from pathlib import Path

import yaml


def load_config(path: str | None = None) -> dict:
    cfg_path = Path(
        path or os.environ.get("DIGEST_CONFIG", Path(__file__).parent / "config.yaml")
    )

    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    # Allow env var overrides for secrets (useful in production)
    _env_override(cfg, "anthropic.api_key", "ANTHROPIC_API_KEY")
    _env_override(cfg, "mailgun.api_key", "MAILGUN_API_KEY")
    _env_override(cfg, "mailgun.domain", "MAILGUN_DOMAIN")

    return cfg


def _env_override(cfg: dict, dotted_key: str, env_var: str):
    val = os.environ.get(env_var)
    if not val:
        return
    keys = dotted_key.split(".")
    node = cfg
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = val
