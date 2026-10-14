"""Failure alerts with a plain-language cause and one-tap actions (Retry / Pause / Ignore).

Every failed run lands here (via runlog). Secrets are masked before anything is sent to Telegram or Claude,
and the same failure is not re-alerted for a few hours, so a crash loop can't flood the chat.
Pausing Khushi only stops her *scheduled* jobs; commands still work, and `/resume` undoes it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta

from app.core.db import kv_get, kv_set
from app.core.notify import esc, notifier

log = logging.getLogger(__name__)

REALERT_AFTER = timedelta(hours=6)

_SECRETS = [
    (re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+@"), r"\1[hidden]@"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), "sk-ant-[hidden]"),
    (re.compile(r"sb_secret_[A-Za-z0-9_-]+"), "sb_secret_[hidden]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_.-]+"), "[hidden-jwt]"),
    (re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}"), "bot[hidden]"),
    (re.compile(r"(access_token=)[^&\s]+"), r"\1[hidden]"),
]

# Known failures → cause + fix, so the common cases need no LLM call (and still work when Claude is the problem).
_KNOWN = [
    ("password authentication failed", "The database password is wrong.",
     "Reset it in Supabase and update DATABASE_URL in Railway."),
    ("Invalid bearer token", "The Claude token is invalid.", "Run `claude setup-token`, update CLAUDE_CODE_OAUTH_TOKEN."),
    ("OAuth access token is invalid", "The Claude token is invalid or expired.",
     "Run `claude setup-token`, update CLAUDE_CODE_OAUTH_TOKEN."),
    ("disabled Claude subscription access", "The Claude account's organization turned off Claude Code for subscriptions.",
     "Set LLM_AUTH=api_key + ANTHROPIC_API_KEY in Railway (pay per use), or the Claude org admin re-enables "
     "Claude Code access."),
    ("usage limit", "The Claude plan hit its usage limit.", "Wait for the limit to reset, then Retry."),
    ("No valid LinkedIn session", "LinkedIn isn't logged in on the server yet.", "Send /linkedin_login and log in."),
    ("Session expired", "LinkedIn logged Khushi out.", "Send /linkedin_login and log in again."),
    ("Authentication not found", "LinkedIn isn't logged in on the server yet.", "Send /linkedin_login and log in."),
    ("Rate limit detected", "LinkedIn asked to slow down.", "Nothing to do: LinkedIn is paused until tomorrow."),
    ("RateLimited", "A daily safety cap was reached.", "Nothing to fix; it resets at midnight IST."),
    ("Not authenticated", "The comment login (LinkedIn app) is missing or expired.", "Send /comment_auth."),
    ("timed out", "A service took too long to answer.", "Usually temporary: tap Retry."),
]


def redact(text: str) -> str:
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def known_cause(error: str) -> tuple[str, str] | None:
    low = error.lower()
    for needle, cause, fix in _KNOWN:
        if needle.lower() in low:
            return cause, fix
    return None


async def diagnose(agent: str, job: str, error: str) -> str:
    """One or two plain sentences: what broke and what to do. Never raises."""
    if hit := known_cause(error):
        return f"{hit[0]} {hit[1]}"
    try:
        from app.core.llm import get_llm

        reply = await asyncio.to_thread(
            get_llm().text,
            f"Agent '{agent}' (job '{job}') of a Python Telegram bot on Railway failed with:\n\n{error[-2500:]}\n\n"
            "In at most 2 short sentences for a non-programmer: the likely cause, then the fix. No code.",
            "You are a calm site-reliability engineer. Be specific and brief.", False)
        return redact(reply)[:400]
    except Exception:
        log.exception("diagnose failed")
        return "Couldn't work out the cause automatically: check the Railway logs."


def _signature(agent: str, job: str, error: str) -> str:
    last = (error.strip().splitlines() or [""])[-1]
    last = re.sub(r"\d+", "#", last)[:200]  # ignore ids/timestamps so repeats match
    return hashlib.sha1(f"{agent}|{job}|{last}".encode()).hexdigest()[:16]


def should_alert(agent: str, job: str, error: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    key = f"incident:{_signature(agent, job, error)}"
    last = kv_get(key)
    if last and now - datetime.fromisoformat(last) < REALERT_AFTER:
        return False
    kv_set(key, now.isoformat())
    return True


def buttons(run_id: int) -> list[list[tuple[str, str]]]:
    return [[("🔁 Retry", f"inc:{run_id}:retry"), ("⏸ Pause Khushi", f"inc:{run_id}:pause"),
             ("🙈 Ignore", f"inc:{run_id}:ignore")]]


async def report_failure(agent: str, job: str, run_id: int, error: str) -> None:
    """Alert the group about a failed run. Must never raise (it runs inside error handling)."""
    try:
        error = redact(error)
        if not should_alert(agent, job, error):
            log.info("incident for %s/%s already alerted recently", agent, job)
            return
        last_line = (error.strip().splitlines() or ["?"])[-1][:300]
        cause = await diagnose(agent, job, error)
        await notifier.send(
            f"🚨 <b>{esc(agent.title())} · {esc(job or 'run')} failed</b> (run {run_id})\n\n"
            f"<b>Error:</b> <code>{esc(last_line)}</code>\n"
            f"<b>Likely cause:</b> {esc(cause)}",
            buttons(run_id))
    except Exception:
        log.exception("failed to report incident")
        await notifier.alert(f"{agent.title()} ({job or 'run'}) failed (run {run_id})")


# ───────────────────────────── pause switch ─────────────────────────────

def _pause_key(agent: str) -> str:
    return f"agent_paused:{agent}"


def is_paused(agent: str) -> bool:
    return kv_get(_pause_key(agent)) == "1"


def pause(agent: str) -> None:
    kv_set(_pause_key(agent), "1")


def resume(agent: str) -> None:
    kv_set(_pause_key(agent), "")


def paused_agents() -> list[str]:
    from app.agents import registry

    return [name for name in registry.AGENTS if is_paused(name)]
