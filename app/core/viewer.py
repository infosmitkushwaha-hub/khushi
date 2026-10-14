"""/linkedin_login: log Sumit's LinkedIn in on the server itself, through a temporary noVNC page.

LinkedIn raises a security check when a session moves to a new IP, so the session is created on Railway's IP
rather than copied from the PC. The reader MCP has a built-in viewer for exactly this (`--login --login-viewer`):
it shows the login browser over noVNC on port 6080 behind a one-time token and closes itself after 30 minutes.
Railway maps LOGIN_VIEWER_URL to that port; we swap the loopback address it prints for the public one.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from app.config import get_settings
from app.core.mcp_client import _env_map, _lock
from app.core.notify import esc, notifier

log = logging.getLogger(__name__)

LOOPBACK = re.compile(r"http://127\.0\.0\.1:6080(/\S+)")
URL_WAIT_SECONDS = 180
LOGIN_BUDGET_SECONDS = 31 * 60
_task: asyncio.Task | None = None


class ViewerError(RuntimeError):
    pass


def public_url(printed: str) -> str | None:
    """The viewer link with the server's public address instead of 127.0.0.1, or None if not printed."""
    base = get_settings().login_viewer_url.rstrip("/")
    m = LOOPBACK.search(printed)
    if not m or not base:
        return None
    return base + m.group(1)


def running() -> bool:
    return _task is not None and not _task.done()


async def start() -> str:
    """Start the login browser and return the link for Sumit. The process keeps running in the background."""
    global _task
    s = get_settings()
    if not s.login_viewer_url:
        raise ViewerError("LOGIN_VIEWER_URL isn't set: add a Railway domain on port 6080 and put its https URL "
                          "in that variable.")
    if running():
        raise ViewerError("A login is already open. Finish it, or wait until it closes (30 minutes).")
    lock = _lock("linkedin_reader")
    await lock.acquire()  # no scans while the login browser owns the profile
    try:
        env = {**os.environ, "USER_DATA_DIR": s.linkedin_profile_dir, "LINKEDIN_MCP_CONTAINER": "true"}
        for key in ("PROXY_SERVER", "PROXY_USERNAME", "PROXY_PASSWORD"):
            if value := _env_map().get(key):
                env[key] = value
        proc = await asyncio.create_subprocess_exec(
            "mcp-server-linkedin", "--login", "--login-viewer",
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        url = await asyncio.wait_for(_read_until_url(proc), URL_WAIT_SECONDS)
    except BaseException:
        lock.release()
        raise
    _task = asyncio.create_task(_watch(proc, lock))
    return url


async def _read_until_url(proc: asyncio.subprocess.Process) -> str:
    seen: list[str] = []
    assert proc.stdout is not None
    while line := (await proc.stdout.readline()).decode(errors="replace"):
        seen.append(line.rstrip())
        if url := public_url(line):
            return url
    raise ViewerError("The login browser stopped before showing a link: " + " / ".join(seen[-5:])[:400])


async def _watch(proc: asyncio.subprocess.Process, lock: asyncio.Lock) -> None:
    lines: list[str] = []
    try:
        assert proc.stdout is not None

        async def drain() -> None:
            while line := (await proc.stdout.readline()).decode(errors="replace"):
                lines.append(line.rstrip())

        await asyncio.wait_for(drain(), LOGIN_BUDGET_SECONDS)
        code = await proc.wait()
        tail = " / ".join(lines[-4:])
        if code == 0 and any("Profile saved" in line or "Cookies exported" in line for line in lines):
            from app.core import ratelimit

            ratelimit.resume("linkedin_reader")
            await notifier.send("✅ <b>LinkedIn connected.</b> I'll use it for the next scan.")
        else:
            await notifier.send(f"❌ LinkedIn login didn't finish (exit {code}).\n<code>{esc(tail[:400])}</code>\n"
                                "Send /linkedin_login to try again.")
    except TimeoutError:
        proc.kill()
        await notifier.send("⌛ The LinkedIn login page closed after 30 minutes. Send /linkedin_login to try again.")
    except Exception:
        log.exception("login viewer watch failed")
    finally:
        lock.release()
