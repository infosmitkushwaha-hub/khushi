"""Free-text chat with Khushi in Telegram: she answers from her own records, read-only.

Chat never acts on LinkedIn. Actions only happen through buttons (which pass approvals) or commands.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.core import knowledge, ratelimit
from app.core.db import Action, Contact, Lead, last_runs, session

SYSTEM = (
    "You are Khushi, Sumit Kushwaha's job-search employee on Telegram. Answer in 1-6 short lines, in the language "
    "he uses (English or Hinglish). Use only the records below; if something isn't there, say so. You can't act from "
    "chat: point him to /scan, /inbox, /report, /pipeline or the buttons on your cards.\n\n"
)


def context() -> str:
    with session() as s:
        leads = list(s.scalars(select(Lead).order_by(Lead.created_at.desc()).limit(20)))
        contacts = list(s.scalars(select(Contact).where(Contact.state != "new")
                                  .order_by(Contact.updated_at.desc()).limit(20)))
        pending = list(s.scalars(select(Action).where(Action.status == "pending")))
    lines = ["RECENT LEADS:"]
    lines += [f"- {lead.id} {lead.score} {lead.role} @ {lead.company} ({lead.location}, {lead.status}) by "
              f"{lead.poster_name}" for lead in leads] or ["- none"]
    lines += ["PEOPLE CONTACTED:"]
    lines += [f"- {c.name} ({c.company}): {c.state}" + (f", replied: {c.last_reply[:120]}" if c.last_reply else "")
              for c in contacts] or ["- none"]
    lines += [f"WAITING FOR SUMIT'S TAP: {len(pending)}"]
    lines += ["LAST RUNS:"] + [f"- {r.job} {r.status} {r.items} {r.message}" for r in last_runs(5)]
    return "\n".join(lines)


async def answer(question: str, is_approver: bool) -> str:
    ratelimit.check("chat.answer", get_settings().chat_daily_cap)
    ratelimit.record("chat.answer", "khushi")
    from app.core.llm import get_llm

    prompt = f"{context()}\n\nSumit asks: {question}"
    return await asyncio.to_thread(get_llm().text, prompt, SYSTEM + knowledge.brain("profile"), False)
