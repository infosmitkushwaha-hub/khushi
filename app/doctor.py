"""`python -m app doctor [--live]`: what's configured, and (with --live) does each connection really work.

Secrets are only ever used, never printed: each line shows ✅/❌ and what to set.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from app.config import ROOT, get_settings

OK, MISSING, OPTIONAL, FAIL = "✅", "❌", "➖", "💥"


def _row(icon: str, name: str, detail: str) -> str:
    return f"  {icon} {name:<24} {detail}"


def static_checks() -> list[str]:
    s = get_settings()
    claude_ok = bool(s.claude_code_oauth_token) if s.llm_auth == "oauth" else bool(s.anthropic_api_key)
    poster_dist = Path(s.linkedin_posting_mcp_path or ROOT / "vendor/linkedin-posting-mcp") / "dist" / "index.js"
    try:
        from app.core.db import kv_get

        comment_auth = kv_get("linkedin_comment_authed_on")
    except Exception:
        comment_auth = ""
    return [
        _row(OK if claude_ok else MISSING, "Claude", f"LLM_AUTH={s.llm_auth} "
             + ("(CLAUDE_CODE_OAUTH_TOKEN)" if s.llm_auth == "oauth" else "(ANTHROPIC_API_KEY)")),
        _row(OK if s.telegram_enabled else MISSING, "Telegram", "TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"),
        _row(OK if s.approvers else MISSING, "Approver (Sumit)", f"APPROVER_IDS={s.approver_ids or '—'}"),
        _row(OK if shutil.which("mcp-server-linkedin") else MISSING, "LinkedIn reader",
             "mcp-server-linkedin installed (in the Docker image)"),
        _row(OK if s.login_viewer_url else MISSING, "Login viewer", "LOGIN_VIEWER_URL (Railway domain → port 6080)"),
        _row(OK if s.proxy_server else OPTIONAL, "Proxy", "PROXY_SERVER (optional, lowers LinkedIn checks)"),
        _row(OK if s.linkedin_client_id and s.linkedin_client_secret else MISSING, "Comment app",
             "LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET"),
        _row(OK if poster_dist.is_file() else OPTIONAL, "Posting MCP build",
             str(poster_dist) if poster_dist.is_file() else "built in Docker automatically"),
        _row(OK if comment_auth else OPTIONAL, "Comment login",
             f"connected {comment_auth}" if comment_auth else "Telegram: /comment_auth"),
        _row(OK, "Database", "Postgres (DATABASE_URL)" if s.database_url else "SQLite (data/khushi.db)"),
        _row("💬" if s.auto_comment else "✋", "Comments", f"auto, max {s.comments_per_day}/day" if s.auto_comment
             else "drafts only (AUTO_COMMENT=false)"),
        _row("🧪" if s.dry_run else "🟢", "Mode", "DRY_RUN=true (nothing is sent)" if s.dry_run
             else "LIVE (comments auto, connect/DM after Approve)"),
    ]


async def live_checks() -> list[str]:
    s = get_settings()
    out: list[str] = []

    async def run(name, fn):
        try:
            out.append(_row(OK, name, (await fn()) or "ok"))
        except Exception as e:  # report, never crash
            from app.core.incidents import redact

            out.append(_row(FAIL, name, redact(f"{type(e).__name__}: {str(e)[:300]}")))

    async def claude():
        from app.core.llm import get_llm

        llm = get_llm()
        reply = await asyncio.to_thread(llm.text, "Reply with exactly: OK", "Be terse.", False)
        return f"{llm.mode}: {reply[:30]}"

    async def telegram():
        from telegram import Bot

        async with Bot(s.telegram_bot_token) as bot:
            me = await bot.get_me()
            chat = await bot.get_chat(s.telegram_chat_id)
            return f"@{me.username} → {chat.title or chat.first_name or chat.id}"

    async def database():
        from sqlalchemy import text

        from app.core.db import engine

        with engine().connect() as c:
            c.execute(text("select 1"))
        return "connected"

    async def reader():
        proc = await asyncio.create_subprocess_exec(
            "mcp-server-linkedin", "--status", env={**__import__("os").environ,
                                                    "USER_DATA_DIR": s.linkedin_profile_dir},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        text = (await asyncio.wait_for(proc.communicate(), 120))[0].decode(errors="replace")
        if proc.returncode:
            raise RuntimeError(text.strip().splitlines()[-1] if text.strip() else f"exit {proc.returncode}")
        return text.strip().splitlines()[-1][:120] if text.strip() else "session ok"

    async def commenter():
        from app.core.mcp_client import McpTools

        async with McpTools("linkedin_poster", agent="khushi") as li:
            return (await li.call("linkedin_get_auth_status", {}))[:120]

    await run("Database", database)
    if s.claude_code_oauth_token or s.anthropic_api_key:
        await run("Claude", claude)
    if s.telegram_enabled:
        await run("Telegram", telegram)
    if shutil.which("mcp-server-linkedin"):
        await run("LinkedIn session", reader)
    if s.linkedin_client_id:
        await run("Comment login", commenter)
    return out


def doctor(live: bool = False) -> None:
    print("Khushi doctor\n")
    print("\n".join(static_checks()))
    if live:
        print("\nLive checks:")
        print("\n".join(asyncio.run(live_checks())))


def startup_report(html: bool = False) -> str:
    s = get_settings()
    mode = "🧪 DRY RUN" if s.dry_run else "🟢 LIVE"
    claude_ok = bool(s.claude_code_oauth_token) if s.llm_auth == "oauth" else bool(s.anthropic_api_key)
    missing = [line.split("  ")[2].strip() for line in static_checks() if f"  {MISSING} " in line]
    head = f"{'<b>' if html else ''}Khushi started{'</b>' if html else ''} · {mode} · Claude: {s.llm_auth}"
    head += "" if claude_ok else " (token missing!)"
    if missing:
        head += "\nStill to set up: " + ", ".join(m.split()[0] for m in missing)
    return head
