"""Discord webhook alerting.

Design rule: **notification must never break collection.** Every failure path
here swallows and reports rather than raising. A webhook outage, a revoked
URL, or a Discord 500 must not turn a successful collection run into a failed
one — the alerting exists to protect the data, so it is not allowed to become
a reason the data stops.

Set DISCORD_WEBHOOK_URL in .env (locally) or repository secrets (CI). Unset is
a supported configuration and silently disables alerting.
"""
from __future__ import annotations

import os

MAX_LEN = 1900          # Discord's limit is 2000; leave room for formatting


def webhook_url() -> str | None:
    """Read at CALL time, not import time.

    Reading module-level config at import ran before .env was loaded and cost
    a full debugging session once already. Do not hoist this.
    """
    from . import db
    db._load_env()
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    return url or None


def send(title: str, body: str, level: str = "error") -> bool:
    """Post a message. Returns True if delivered, False otherwise.

    Never raises.
    """
    url = webhook_url()
    if not url:
        return False

    icon = {"error": "\U0001F534", "warn": "\U0001F7E1",
            "ok": "\U0001F7E2"}.get(level, "ℹ️")
    text = f"{icon} **{title}**\n```\n{body[:MAX_LEN]}\n```"

    try:
        import httpx
        r = httpx.post(url, json={"content": text}, timeout=10.0)
        return r.status_code in (200, 204)
    except Exception as exc:                                     # noqa: BLE001
        print(f"  [notify] delivery failed, continuing: {exc}")
        return False


def redact(text: str) -> str:
    """Strip credentials out of anything headed for a chat channel.

    An exception string routinely contains the whole DSN, and a Discord
    channel is not a secret store. We have already burned one Neon password
    by pasting it somewhere it did not belong.
    """
    import re
    text = re.sub(r"(postgres(?:ql)?://[^:]+:)[^@]+(@)", r"\1REDACTED\2", text)
    text = re.sub(r"(https://discord\.com/api/webhooks/)\S+", r"\1REDACTED",
                  text)
    return text
