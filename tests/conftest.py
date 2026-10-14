import pytest

from app.config import get_settings
from app.core import db, ratelimit


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    """Every test: in-memory DB, console instead of Telegram, dry run, approver 42, no real secrets or waits."""
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
                "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET", "PROXY_SERVER", "LOGIN_VIEWER_URL", "DATABASE_URL",
                "GROQ_API_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("APPROVER_IDS", "42")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("AUTO_COMMENT", "true")
    monkeypatch.setenv("COMMENTS_PER_DAY", "10")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    async def no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(ratelimit, "polite_sleep", no_sleep)
    get_settings.cache_clear()
    db.init_db("sqlite://")
    yield
    get_settings.cache_clear()
