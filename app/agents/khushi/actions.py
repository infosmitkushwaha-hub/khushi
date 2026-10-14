"""The only code that writes to LinkedIn. Every path starts with approvals.assert_publishable.

Outcomes are recorded on the Action, the Contact and the Lead so nothing is ever done twice. A DM that LinkedIn
didn't confirm is treated as sent and never retried: a retry can deliver it twice (hard rule 8).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.agents.khushi import sources
from app.config import get_settings
from app.core import approvals
from app.core.db import Action, Contact, Lead, session, utcnow
from app.core.mcp_client import McpTools

log = logging.getLogger(__name__)


def upsert_contact(profile_url: str, name: str = "", company: str = "", lead_id: str = "") -> None:
    path = sources.profile_path(profile_url)
    if not path:
        return
    with session() as s:
        c = s.scalar(select(Contact).where(Contact.profile_url == path))
        if c is None:
            s.add(Contact(profile_url=path, name=name, company=company, lead_id=lead_id))
        else:
            c.name = c.name or name
            c.company = c.company or company
            c.lead_id = c.lead_id or lead_id


def _finish(action_id: str, status: str, error: str = "") -> None:
    with session() as s:
        a = s.get(Action, action_id)
        a.status = status
        a.error = error
        if status == "published":
            a.published_at = utcnow()


def _set_lead(lead_id: str, status: str) -> None:
    if not lead_id:
        return
    with session() as s:
        lead = s.get(Lead, lead_id)
        if lead and lead.status in {"new", "commented", "connected", "messaged"}:
            lead.status = status


def _set_contact(profile: str, **fields) -> None:
    with session() as s:
        c = s.scalar(select(Contact).where(Contact.profile_url == sources.profile_path(profile)))
        if c:
            for k, v in fields.items():
                setattr(c, k, v)


async def execute(action_id: str) -> str:
    """Carry out an approved action. Returns a one-line result for Telegram."""
    with session() as s:
        action = s.get(Action, action_id)
        approvals.assert_publishable(action)
        kind, target, body, lead_id = action.kind, action.target, action.body, action.lead_id
        action_snapshot = Action(id=action.id, kind=kind, target=target, body=body, status=action.status,
                                 approved_by=action.approved_by)

    if get_settings().dry_run:
        _finish(action_id, "published", "dry run: not sent")
        return f"🧪 Dry run: the {kind} was not sent (DRY_RUN=true)."

    try:
        if kind == "comment":
            return await _comment(action_snapshot, lead_id)
        if kind == "connect":
            return await _connect(action_snapshot, lead_id)
        if kind == "dm":
            return await _dm(action_snapshot, lead_id)
    except approvals.ApprovalError:
        raise
    except Exception as e:  # a failed write is reported, never retried automatically
        log.exception("%s %s failed", kind, action_id)
        _finish(action_id, "failed", str(e)[:500])
        return f"❌ The {kind} failed: {str(e)[:200]}"
    raise ValueError(f"unknown action kind {kind}")


async def _comment(action: Action, lead_id: str) -> str:
    async with McpTools("linkedin_poster", agent="khushi") as li:
        raw = await li.call("linkedin_create_comment", {"postUrn": action.target, "text": action.body},
                            approved=action)
    data = sources.mcp_status(raw)
    if data.get("success") or "Comment posted" in raw:
        _finish(action.id, "published")
        _set_lead(lead_id, "commented")
        return "💬 Commented."
    _finish(action.id, "failed", raw[:500])
    return f"❌ Comment not posted: {raw[:200]}"


async def _connect(action: Action, lead_id: str) -> str:
    async with McpTools("linkedin_reader", agent="khushi") as li:
        raw = await li.call("connect_with_person", {"linkedin_username": action.target}, approved=action)
    status = sources.mcp_status(raw).get("status", "")
    if status in {"connected", "pending"}:
        _finish(action.id, "published")
        _set_contact(action.target, state="requested", last_sent_at=utcnow())
        _set_lead(lead_id, "connected")
        return "🤝 Connection request sent. I'll show you the DM when they accept."
    if status in {"already_connected", "accepted"}:
        _finish(action.id, "published", status)
        _set_contact(action.target, state="connected", degree="1st")
        return "🤝 You're already connected: tap ✉️ DM on the card to message them."
    _finish(action.id, "failed", raw[:500])
    reason = {"follow_only": "they only allow Follow", "connect_unavailable": "LinkedIn shows no Connect button",
              "custom_note_limit_reached": "the monthly note limit is used up"}.get(status, status or raw[:120])
    return f"❌ Couldn't connect: {reason}."


async def _dm(action: Action, lead_id: str) -> str:
    message = sources.one_line(action.body)
    async with McpTools("linkedin_reader", agent="khushi") as li:
        raw = await li.call("send_message", {"linkedin_username": action.target, "message": message,
                                             "confirm_send": True}, approved=action)
    data = sources.mcp_status(raw)
    if data.get("sent") or data.get("status") == "send_unconfirmed" or data.get("retry_safe") is False:
        # submitted: confirmed, or unconfirmed (never retry, a retry may deliver it twice)
        note = "" if data.get("sent") else "unconfirmed: check the LinkedIn thread"
        _finish(action.id, "published", note)
        _set_contact(action.target, state="messaged", last_sent_at=utcnow(), pending_dm="")
        _set_lead(lead_id, "messaged")
        return "✉️ Sent." if data.get("sent") else "✉️ Submitted (LinkedIn was slow to confirm; I won't resend)."
    _finish(action.id, "failed", raw[:500])
    if data.get("status") in {"message_unavailable", "recipient_resolution_failed"}:
        return ("❌ LinkedIn won't let you message them yet (not connected). Tap 🤝 Connect on the card first; "
                "I'll bring this message back when they accept.")
    return f"❌ Not sent: {data.get('message') or raw[:200]}"
