"""Alerting must never leak a credential and never raise."""
from __future__ import annotations

from edgedesk.notify import redact, send


def test_redacts_postgres_password():
    dsn = "postgresql://neondb_owner:npg_SuperSecret123@ep-x.neon.tech/edgedesk"
    out = redact(f"connection failed: {dsn}")
    assert "npg_SuperSecret123" not in out
    assert "REDACTED" in out
    assert "neondb_owner" in out          # user is useful, password is not
    assert "ep-x.neon.tech" in out


def test_redacts_discord_webhook():
    out = redact("posting to https://discord.com/api/webhooks/12345/abcdefTOKEN")
    assert "abcdefTOKEN" not in out
    assert "REDACTED" in out


def test_redact_leaves_ordinary_text_alone():
    assert redact("258 rows, 0 books") == "258 rows, 0 books"


def test_send_without_webhook_is_a_no_op(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr("edgedesk.db._load_env", lambda: None)
    assert send("title", "body") is False


def test_send_never_raises_on_transport_failure(monkeypatch):
    """A webhook outage must not turn a good collection run into a failed
    one. This is the whole reason send() returns a bool instead of raising."""
    monkeypatch.setattr("edgedesk.db._load_env", lambda: None)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/hook")

    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", boom)
    assert send("title", "body") is False
