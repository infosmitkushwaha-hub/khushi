"""All settings come from .env (see .env.example). One place, typed, with safe defaults."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Claude: switch auth with LLM_AUTH only
    llm_auth: Literal["oauth", "api_key"] = "oauth"
    claude_code_oauth_token: str = ""
    anthropic_api_key: str = ""
    claude_bin: str = "claude"
    llm_model_smart: str = "claude-sonnet-5"
    llm_model_fast: str = "claude-haiku-4-5-20251001"
    llm_timeout: int = 300

    # Safety: nothing leaves LinkedIn-side until this is false
    dry_run: bool = True

    # Telegram (Khushi talks to Sumit in one chat; only APPROVER_IDS can approve)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    approver_ids: str = ""
    chat_daily_cap: int = 80
    groq_api_key: str = ""                 # optional: voice notes -> text
    groq_stt_model: str = "whisper-large-v3-turbo"
    voice_daily_cap: int = 60

    # LinkedIn reader MCP (browser, logged in as Sumit): runs inside this container
    linkedin_user_data_dir: str = ""       # empty = <DATA_DIR>/linkedin/profile
    proxy_server: str = ""                 # optional sticky residential proxy, e.g. http://host:port
    proxy_username: str = ""
    proxy_password: str = ""
    login_viewer_url: str = ""             # public https URL Railway maps to port 6080 (noVNC), for /linkedin_login

    # LinkedIn posting MCP (official API): only used to comment on hiring posts
    linkedin_posting_mcp_path: str = ""
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:3000/callback"

    # Khushi's job search
    scan_times: str = "09:00,13:00,18:00"
    inbox_every_minutes: int = 120
    followup_time: str = "11:00"
    report_time: str = "20:00"
    auto_comment: bool = True              # comment by herself on strong-fit posts (decided 14 Oct)
    comments_per_day: int = 10
    followup_after_days: int = 3
    max_leads_per_scan: int = 12

    # Ops
    healthcheck_url: str = ""              # healthchecks.io ping URL (alerts if the whole server dies)

    # Database: empty = local SQLite (data/khushi.db). Supabase later: paste its Postgres connection string.
    database_url: str = ""
    db_schema: str = "khushi"

    # General
    timezone: str = "Asia/Kolkata"
    data_dir: Path = ROOT / "data"

    @field_validator("data_dir")
    @classmethod
    def _abs_data_dir(cls, v: Path) -> Path:
        return v if v.is_absolute() else ROOT / v

    @field_validator("linkedin_posting_mcp_path")
    @classmethod
    def _abs_poster_path(cls, v: str) -> str:
        p = Path(v) if v else ROOT / "vendor" / "linkedin-posting-mcp"
        return str(p if p.is_absolute() else ROOT / p)

    @property
    def approvers(self) -> set[int]:
        return {int(x) for x in self.approver_ids.replace(" ", "").split(",") if x}

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def linkedin_profile_dir(self) -> str:
        return self.linkedin_user_data_dir or str(self.data_dir / "linkedin" / "profile")

    @property
    def scan_slots(self) -> list[str]:
        return [t.strip() for t in self.scan_times.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
