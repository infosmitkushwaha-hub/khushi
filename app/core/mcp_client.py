"""Client for the MCP servers in config/mcp_servers.yaml, with allow/deny lists, daily caps and pacing.

    async with McpTools("linkedin_reader", agent="khushi") as li:
        result = await li.call("search_posts", {"keywords": "hiring Ranchi", "date_posted": "past-week"})

Tools listed under a server's `approval_only` (connect, DM) can only be called with an approved Action, so an LLM
or a bug elsewhere can never message anyone on its own.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from app.config import ROOT, get_settings
from app.core import ratelimit
from app.core.db import Action
from app.core.notify import notifier

CONFIG_FILE = ROOT / "config" / "mcp_servers.yaml"

# The LinkedIn reader's fixed error texts (linkedin_mcp_server/error_handler.py, bootstrap.py) plus the generic
# words a login wall or challenge page uses.
SESSION_PROBLEM = re.compile(
    r"(authentication not found|session expired|authentication failed|no valid linkedin session|--login"
    r"|authwall|checkpoint|captcha|challenge|not authenticated)",
    re.I,
)
RATE_LIMITED = re.compile(r"rate limit detected", re.I)
STILL_SETTING_UP = re.compile(r"(setup is not complete yet|browser setup was not ready|still in progress)", re.I)
SETUP_WAIT_SECONDS = 30
SETUP_MAX_WAITS = 20


class ToolNotAllowed(PermissionError):
    pass


# One live session per server: two browser processes on the same LinkedIn login would fight over the profile.
_LOCKS: dict[str, asyncio.Lock] = {}


def _lock(server: str) -> asyncio.Lock:
    return _LOCKS.setdefault(server, asyncio.Lock())


def _env_map() -> dict[str, str]:
    s = get_settings()
    values = {k.upper(): str(v) for k, v in s.model_dump().items()}
    values["LINKEDIN_PROFILE_DIR"] = s.linkedin_profile_dir
    values.update(os.environ)
    return values


def _subst(value: str, env: dict[str, str]) -> str:
    # ${A} or ${A|B} (first non-empty of A, B)
    return re.sub(r"\$\{([\w|]+)\}",
                  lambda m: next((env[k] for k in m.group(1).split("|") if env.get(k)), ""), value)


def load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))["servers"]


def check_allowed(cfg: dict[str, Any], server: str, agent: str, tool: str,
                  approved: Action | None = None) -> None:
    if tool in cfg.get("deny_always", []):
        raise ToolNotAllowed(f"{server}.{tool} is blocked for all agents")
    allowed = cfg.get("agents", {}).get(agent, {}).get("allow", [])
    if tool not in allowed:
        raise ToolNotAllowed(f"{agent} may not call {server}.{tool}")
    if tool in cfg.get("approval_only", []):
        from app.core.approvals import assert_publishable

        if approved is None:
            raise ToolNotAllowed(f"{server}.{tool} needs an approved action")
        assert_publishable(approved)


def result_text(result: Any) -> tuple[str, bool]:
    """(text, is_error) from a CallToolResult. The mcp SDK renamed fields in 2.x (isError → is_error, …)."""
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured:
        text = json.dumps(structured, ensure_ascii=False)
    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", False)
    return text, bool(is_error)


class McpTools:
    def __init__(self, server: str, agent: str):
        self.server, self.agent = server, agent
        self.cfg = load_config()[server]
        self._stack = None
        self.session = None

    async def __aenter__(self) -> McpTools:
        await _lock(self.server).acquire()
        try:
            await self._open()
        except BaseException:
            _lock(self.server).release()
            raise
        return self

    async def _open(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = _env_map()
        extra = {k: _subst(str(v), env) for k, v in self.cfg.get("env", {}).items()}
        params = StdioServerParameters(
            command=_subst(self.cfg["command"], env),
            args=[_subst(a, env) for a in self.cfg.get("args", [])],
            # unset values ("" after substitution) must not override the server's own defaults
            env={**os.environ, **{k: v for k, v in extra.items() if v}},
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()

    async def __aexit__(self, *exc) -> None:
        try:
            if self._stack:
                await self._stack.aclose()
        finally:
            _lock(self.server).release()

    def _check_hours(self) -> None:
        hours = self.cfg.get("active_hours")
        if hours:
            h = datetime.now(ZoneInfo(get_settings().timezone)).hour
            if not hours[0] <= h < hours[1]:
                raise ratelimit.RateLimited(f"{self.server} only runs {hours[0]}:00–{hours[1]}:00 IST")

    async def call(self, tool: str, args: dict[str, Any], approved: Action | None = None) -> str:
        check_allowed(self.cfg, self.server, self.agent, tool, approved)
        self._check_hours()
        key = f"{self.server}.{tool}"
        ratelimit.check(key, self.cfg.get("daily_caps", {}).get(tool))
        lo, hi = self.cfg.get("delay_seconds", [0, 0])
        await ratelimit.polite_sleep(lo, hi)

        for _ in range(SETUP_MAX_WAITS):
            result = await self.session.call_tool(tool, args)
            text, is_error = result_text(result)
            # First start on a fresh volume: the reader downloads its browser in the background. Waiting is safe;
            # nothing was attempted on LinkedIn yet.
            if is_error and STILL_SETTING_UP.search(text):
                await asyncio.sleep(SETUP_WAIT_SECONDS)
                continue
            break
        ratelimit.record(key, self.agent, ok=not is_error)
        if is_error:
            await self._handle_error(text)
            raise RuntimeError(f"{key} error: {text[:500]}")
        return text

    async def _handle_error(self, text: str) -> None:
        alert = self.cfg.get("session_alert")
        if not alert:
            return
        if SESSION_PROBLEM.search(text):
            ratelimit.pause_for_today(self.server)
            await notifier.alert(alert)
        elif RATE_LIMITED.search(text):
            ratelimit.pause_for_today(self.server)
            await notifier.alert("LinkedIn says too many requests. I've paused LinkedIn for today to keep your "
                                 "account safe; I'll start again tomorrow.")
