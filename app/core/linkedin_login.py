"""One-time login for commenting (OAuth via the vendored LinkedIn posting MCP), from the CLI or Telegram.

The MCP keeps the OAuth state on disk for 30 minutes, so start() and finish() can run in separate sessions:
Sumit gets the link from /comment_auth, logs in, and pastes back the localhost URL LinkedIn redirected to.
This is the official API (comments only); the browser login for searching is /linkedin_login (see viewer.py).
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, urlparse

SERVER = "linkedin_poster"


class LoginError(RuntimeError):
    pass


def parse_redirect(url: str) -> tuple[str, str]:
    """(code, state) from the redirected URL. Raises LoginError with a plain message when they're missing."""
    q = parse_qs(urlparse(url.strip()).query)
    if "error" in q:
        raise LoginError(f"LinkedIn said: {q.get('error_description', q['error'])[0]}")
    if not q.get("code") or not q.get("state"):
        raise LoginError("That doesn't look like the redirected address: it must contain ?code=…&state=…")
    return q["code"][0], q["state"][0]


async def start() -> str:
    """The LinkedIn login URL for Sumit to open."""
    from app.core.mcp_client import McpTools

    async with McpTools(SERVER, agent="khushi") as li:
        text = await li.call("linkedin_auth_start", {})
    m = re.search(r"https://www\.linkedin\.com/oauth/\S+", text)
    if not m:
        raise LoginError(f"Couldn't get a login link: {text[:200]}")
    return m.group(0).rstrip(").,'\"")


async def finish(redirected_url: str) -> str:
    """Complete the login; records the date so the evening report can warn before the ~60-day expiry."""
    from app.core.db import kv_set
    from app.core.mcp_client import McpTools

    code, state = parse_redirect(redirected_url)
    async with McpTools(SERVER, agent="khushi") as li:
        result = await li.call("linkedin_auth_callback", {"code": code, "state": state})
    if "Authentication successful" not in result:
        raise LoginError(result[:300])
    kv_set("linkedin_comment_authed_on", date.today().isoformat())
    return result
