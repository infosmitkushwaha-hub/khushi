"""Run logging: every job run records start, end, items found and errors."""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.core.db import Run, session, utcnow
from app.core.notify import speaker

log = logging.getLogger(__name__)


@dataclass
class RunHandle:
    id: int
    items: int = 0
    message: str = ""


@asynccontextmanager
async def logged_run(agent: str, job: str = "") -> AsyncIterator[RunHandle]:
    with session() as s:
        row = Run(agent=agent, job=job)
        s.add(row)
        s.flush()
        handle = RunHandle(id=row.id)
    log.info("run %s %s/%s started", handle.id, agent, job)
    token = speaker.set(agent)
    try:
        yield handle
    except Exception as e:
        with session() as s:
            row = s.get(Run, handle.id)
            row.status, row.finished_at = "error", utcnow()
            row.items, row.message = handle.items, handle.message
            row.error = "".join(traceback.format_exception(e))[-4000:]
        log.exception("run %s failed", handle.id)
        from app.core import incidents

        await incidents.report_failure(agent, job, handle.id, "".join(traceback.format_exception(e)))
        return  # swallow: one failing agent must not stop the scheduler
    finally:
        speaker.reset(token)
    with session() as s:
        row = s.get(Run, handle.id)
        row.status, row.finished_at = "ok", utcnow()
        row.items, row.message = handle.items, handle.message
    log.info("run %s ok: %s items", handle.id, handle.items)
