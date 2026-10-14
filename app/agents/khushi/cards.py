"""How Khushi's findings look in Telegram (HTML) and which buttons they carry."""

from __future__ import annotations

from app.core.db import Action, Lead
from app.core.notify import Buttons, esc

SCORE_ICON = {"HOT": "🔥", "WARM": "🟡", "SKIP": "⚪"}
SPEED_ICON = {"FAST": "💰 FAST", "MEDIUM": "🕒 MEDIUM", "SLOW": "🐢 SLOW"}


def lead_card(lead: Lead, comment_note: str = "", known_contact: bool = False) -> str:
    who = esc(lead.poster_name or "unknown")
    if lead.poster_url:
        who = f'<a href="{esc(lead.poster_url)}">{who}</a>'
    role = f" ({esc(lead.poster_role)})" if lead.poster_role else ""
    lines = [
        f"{SCORE_ICON.get(lead.score, '•')} <b>{esc(lead.role or 'Opportunity')}</b> · {esc(lead.company or '?')}",
        f"📍 {esc(lead.location or '?')} · {esc(lead.work_mode or '?')} · posted {esc(lead.posted or '?')}",
        f"👤 {who}{role}" + (" · <i>already in touch</i>" if known_contact else ""),
        f"✅ <b>Why you:</b> {esc(lead.why)}",
        f"{SPEED_ICON.get(lead.speed, esc(lead.speed))}",
    ]
    if lead.url:
        lines.append(f'🔗 <a href="{esc(lead.url)}">Open post</a>')
    if lead.apply_url:
        lines.append(f'📝 <a href="{esc(lead.apply_url)}">Apply</a>')
    if comment_note:
        lines.append(comment_note)
    lines.append(f"<code>{lead.id}</code>")
    return "\n".join(lines)


def lead_buttons(lead: Lead) -> Buttons:
    row = []
    if lead.poster_url:
        row.append(("🤝 Connect", f"lead:{lead.id}:connect"))
        row.append(("✉️ DM", f"lead:{lead.id}:dm"))
    return [r for r in (row, [("✅ Applied", f"lead:{lead.id}:applied"), ("🙈 Skip", f"lead:{lead.id}:skip")]) if r]


ACTION_TITLE = {"dm": "✉️ Message", "connect": "🤝 Connection request", "comment": "💬 Comment"}


def action_card(action: Action, to_name: str = "", context: str = "") -> str:
    head = f"{ACTION_TITLE.get(action.kind, action.kind)} to <b>{esc(to_name or action.target)}</b>"
    parts = [head]
    if context:
        parts.append(context)
    if action.body:
        parts.append(f"<blockquote>{esc(action.body)}</blockquote>")
    if action.guard_flags:
        parts.append(f"⚠️ <i>{esc(action.guard_flags)}</i>")
    parts.append(f"<code>{action.id}</code>")
    return "\n".join(parts)


def action_buttons(action: Action) -> Buttons:
    return [[("✅ Send", f"act:{action.id}:send"), ("✏️ Edit", f"act:{action.id}:edit"),
             ("✖ Reject", f"act:{action.id}:reject")]]
