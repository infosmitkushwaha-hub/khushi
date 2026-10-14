"""Khushi: Sumit's job-search employee.

scan     · 09:00 13:00 18:00  LinkedIn posts + jobs → scored leads → Telegram cards; strong Ranchi/freelance fits get
                              a comment by herself (AUTO_COMMENT, max COMMENTS_PER_DAY)
inbox    · every 2 h          new replies → drafted answer for Approve; accepted requests → the waiting DM for Approve
followup · 11:00              one polite nudge to people who didn't answer in FOLLOWUP_AFTER_DAYS
report   · 20:00              the day in one message

Nothing goes to a person (connect, DM) without Sumit's tap; see app/core/approvals.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.khushi import actions, cards, sources
from app.config import get_settings
from app.core import approvals, guard, knowledge, ratelimit
from app.core.db import Action, Contact, Lead, kv_get, kv_set, next_id, session, utcnow
from app.core.llm import get_llm
from app.core.mcp_client import McpTools
from app.core.notify import esc, notifier
from app.core.runlog import logged_run

NAME = "khushi"
DEFAULT_JOB = "scan"
log = logging.getLogger(__name__)

POST_KEYWORDS = [
    "hiring Ranchi", "urgent hiring Ranchi", "job vacancy Ranchi", "developer Ranchi hiring",
    "digital marketing Ranchi hiring", "social media Ranchi hiring", "internship Ranchi", "Shopify Ranchi",
    "hiring Jharkhand", "hiring AI automation intern", "looking for AI automation freelancer",
    "Shopify developer required", "n8n Claude freelancer", "Meta Ads freelancer needed", "need WhatsApp chatbot",
    "need website developer",
]
JOB_SEARCHES = [
    {"keywords": "digital marketing OR social media OR ecommerce", "location": "Ranchi"},
    {"keywords": "AI automation", "location": "India", "work_type": "remote", "experience_level": "internship,entry"},
    {"keywords": "web developer OR Shopify", "location": "Ranchi"},
    {"keywords": "Shopify OR Meta Ads", "location": "India", "work_type": "remote", "experience_level": "entry"},
]
POSTS_PER_SCAN = 3
JOB_DETAILS_PER_SCAN = 3

SYSTEM = (
    "You are Khushi, the job-search employee of Sumit Kushwaha. You read LinkedIn search results and pick real "
    "opportunities for him, then write short, truthful comments and messages in his voice.\n\n"
    + guard.RULES_FOR_PROMPT + "\n\n" + "{brain}"
)


def _system() -> str:
    return SYSTEM.replace("{brain}", knowledge.brain())


# ───────────────────────────── schemas ─────────────────────────────


class LeadDraft(BaseModel):
    kind: Literal["post", "job"] = "post"
    author_name: str = ""
    author_profile_url: str = Field("", description="copied exactly from the PEOPLE list of the same block, or ''")
    permalink: str = Field("", description="copied exactly from the POSTS list of the same block, or ''")
    job_id: str = ""
    role: str
    company: str = ""
    location: str = ""
    work_mode: Literal["on-site", "hybrid", "remote", "freelance", "unknown"] = "unknown"
    posted: str = ""
    poster_role: Literal["hr", "recruiter", "founder", "manager", "company", "other"] = "other"
    score: Literal["HOT", "WARM", "SKIP"]
    speed: Literal["FAST", "MEDIUM", "SLOW"] = "MEDIUM"
    why: str = Field(description="one line: why Sumit matches, using only his real skills/projects")
    apply_url: str = ""
    post_excerpt: str = Field("", description="the first ~300 characters of the post/job text, verbatim")
    comment: str = Field("", description="public comment for HOT posts only, max 3 sentences, ends with portfolio")
    dm: str = Field("", description="message to the poster, 2-4 sentences, ends with one question")


class LeadBatch(BaseModel):
    leads: list[LeadDraft] = []


class Thread(BaseModel):
    name: str
    from_me: bool = Field(description="true when the last message in the preview was sent by Sumit ('You: …')")
    preview: str
    when: str = ""


class InboxRead(BaseModel):
    threads: list[Thread] = []


class Draft(BaseModel):
    text: str


# ───────────────────────────── helpers ─────────────────────────────


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _today_start_utc() -> datetime:
    tz = _tz()
    start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    return start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _rotation(n_items: int, take: int, key: str) -> list[int]:
    start = int(kv_get(key, "0") or 0)
    kv_set(key, str((start + take) % max(n_items, 1)))
    return [(start + i) % n_items for i in range(min(take, n_items))]


def _dedupe_key(d: LeadDraft, urn: str) -> str:
    if urn:
        return urn
    if d.kind == "job" and d.job_id:
        return f"job:{d.job_id}"
    if pl := sources.parse_permalink(d.permalink):
        return pl.urn
    raw = f"{d.author_name}|{d.role}|{d.company}|{d.post_excerpt[:120]}".lower()
    return "h:" + hashlib.sha1(raw.encode()).hexdigest()[:20]


def _comments_today() -> int:
    with session() as s:
        return s.scalar(select(func.count()).select_from(Action).where(
            Action.kind == "comment", Action.created_at >= _today_start_utc(),
            Action.status.in_(["approved", "published"]))) or 0


def _commented(urn: str) -> bool:
    with session() as s:
        return bool(s.scalar(select(func.count()).select_from(Action).where(
            Action.kind == "comment", Action.target == urn, Action.status.in_(["approved", "published"]))))


def _contact_known(profile: str) -> bool:
    path = sources.profile_path(profile)
    if not path:
        return False
    with session() as s:
        c = s.scalar(select(Contact).where(Contact.profile_url == path))
        return bool(c and c.state != "new")


def _block_prompt(i: int, b: sources.SearchBlock) -> str:
    people = "\n".join(f"- {name or '?'}: {sources.profile_url(p)}" for p, name in b.people.items()) or "- (none)"
    posts = "\n".join(f"- {p.url}" for p in b.permalinks) or "- (none)"
    jobs = f"JOB_IDS: {', '.join(b.job_ids)}\n" if b.job_ids else ""
    return (f"### BLOCK {i}: search '{b.keywords}'\n{jobs}PEOPLE (the only profile URLs you may use):\n{people}\n"
            f"POSTS (the only permalinks you may use):\n{posts}\nTEXT:\n{b.text[:9000]}\n")


# ───────────────────────────── scan ─────────────────────────────


async def _gather() -> list[sources.SearchBlock]:
    blocks: list[sources.SearchBlock] = []
    post_idx = _rotation(len(POST_KEYWORDS), POSTS_PER_SCAN, "rot:posts")
    job_search = JOB_SEARCHES[_rotation(len(JOB_SEARCHES), 1, "rot:jobs")[0]]
    async with McpTools("linkedin_reader", agent=NAME) as li:
        for i in post_idx:
            kw = POST_KEYWORDS[i]
            try:
                raw = await li.call("search_posts", {"keywords": kw, "date_posted": "past-week", "max_pages": 2})
            except ratelimit.RateLimited as e:
                log.info("scan stopped: %s", e)
                return blocks
            blocks.append(sources.parse_result(raw, kw))
        try:
            raw = await li.call("search_jobs", {**job_search, "date_posted": "past_week", "max_pages": 1})
        except ratelimit.RateLimited as e:
            log.info("job search skipped: %s", e)
            return blocks
        listing = sources.parse_result(raw, f"jobs: {job_search['keywords']} · {job_search['location']}")
        with session() as s:
            seen = set(s.scalars(select(Lead.urn).where(Lead.urn.like("job:%"))))
        new_ids = [j for j in listing.job_ids if f"job:{j}" not in seen][:JOB_DETAILS_PER_SCAN]
        for job_id in new_ids:
            try:
                raw = await li.call("get_job_details", {"job_id": job_id})
            except ratelimit.RateLimited:
                break
            block = sources.parse_result(raw, f"job {job_id}")
            block.job_ids = [job_id]
            blocks.append(block)
    return blocks


async def scan() -> None:
    s_ = get_settings()
    async with logged_run(NAME, "scan") as run:
        blocks = await _gather()
        if not blocks:
            run.message = "LinkedIn returned nothing (paused, capped or outside hours)"
            return
        prompt = (
            "Below are LinkedIn search results. Find the real hiring posts, job listings and business-need posts. "
            "For each one that is HOT or WARM for Sumit (see <targets>), return a lead. Skip everything else. "
            "author_profile_url and permalink MUST be copied exactly from that block's PEOPLE / POSTS lists (or ''); "
            "pick the permalink whose URL contains the author's profile slug. For job blocks set kind='job', "
            f"job_id and apply_url=https://www.linkedin.com/jobs/view/<job_id>/. Return at most {s_.max_leads_per_scan}"
            " leads, best first. Write 'comment' only for HOT posts (kind='post').\n\n"
            + "\n".join(_block_prompt(i, b) for i, b in enumerate(blocks))
        )
        batch = await asyncio.to_thread(get_llm().json, prompt, LeadBatch, _system(), True)
        people_all = {p: n for b in blocks for p, n in b.people.items()}
        permalinks_all = [p for b in blocks for p in b.permalinks]
        for d in batch.leads[: s_.max_leads_per_scan]:
            if d.score == "SKIP":
                continue
            poster = sources.known_profile(people_all, d.author_profile_url)
            urn = sources.verified_post_urn(permalinks_all, d.permalink, poster) if d.kind == "post" else ""
            key = _dedupe_key(d, urn)
            with session() as s:
                if s.scalar(select(Lead.id).where(Lead.urn == key)):
                    continue
                lead = Lead(
                    id=next_id(s, "L"), kind=d.kind, urn=key, url=d.permalink if d.kind == "post" else d.apply_url,
                    role=d.role[:200], company=d.company[:200], location=d.location[:150], work_mode=d.work_mode,
                    posted=d.posted[:60], poster_name=d.author_name[:200], poster_url=sources.profile_url(poster),
                    poster_role=d.poster_role, score=d.score, speed=d.speed, why=d.why, apply_url=d.apply_url,
                    text=d.post_excerpt,
                )
                s.add(lead)
            if poster:
                actions.upsert_contact(poster, d.author_name, d.company, lead.id)
                if d.dm and not guard.check(d.dm, public=False, source_text=d.post_excerpt):
                    _store_pending_dm(poster, d.dm)
            run.items += 1
            note = await _maybe_comment(lead, d, urn)
            await notifier.send(cards.lead_card(lead, note, _contact_known(poster)), cards.lead_buttons(lead))
        run.message = f"{run.items} new leads from {len(blocks)} searches"


def _store_pending_dm(profile: str, dm: str) -> None:
    with session() as s:
        c = s.scalar(select(Contact).where(Contact.profile_url == sources.profile_path(profile)))
        if c and not c.pending_dm and c.state in {"new", "requested"}:
            c.pending_dm = dm


async def _maybe_comment(lead: Lead, d: LeadDraft, urn: str) -> str:
    """Comment by herself on a strong fit (Sumit's 14 Oct decision), or hand him the draft."""
    s_ = get_settings()
    if lead.score != "HOT" or not d.comment:
        return ""
    issues = guard.check(d.comment, public=True, source_text=d.post_excerpt)
    if issues:
        log.info("comment for %s dropped by guard: %s", lead.id, issues)
        return ""
    if not urn:
        return f"💬 <i>Suggested comment (post it yourself):</i>\n<blockquote>{esc(d.comment)}</blockquote>"
    if not s_.auto_comment or _commented(urn) or _comments_today() >= s_.comments_per_day:
        return f"💬 <i>Comment draft:</i>\n<blockquote>{esc(d.comment)}</blockquote>"
    with session() as s:
        action = Action(id=next_id(s, "A"), kind="comment", lead_id=lead.id, target=urn, body=d.comment)
        approvals.auto_approve(action)
        s.add(action)
    result = await actions.execute(action.id)
    return f"{result}\n<blockquote>{esc(d.comment)}</blockquote>"


# ───────────────────────────── inbox ─────────────────────────────


def _match_contact(name: str) -> Contact | None:
    want = name.casefold().strip()
    with session() as s:
        for c in s.scalars(select(Contact).where(Contact.name != "")):
            have = c.name.casefold().strip()
            if have == want or have.startswith(want) or want.startswith(have):
                return c
    return None


async def inbox() -> None:
    async with logged_run(NAME, "inbox") as run:
        async with McpTools("linkedin_reader", agent=NAME) as li:
            try:
                raw = await li.call("get_inbox", {"limit": 20})
            except ratelimit.RateLimited as e:
                run.message = str(e)
                return
            read = await asyncio.to_thread(
                get_llm().json,
                "List the conversations in this LinkedIn inbox (newest first) with the last message preview.\n\n"
                + sources.parse_result(raw).text[:12000], InboxRead, "You read LinkedIn inbox pages exactly.", False)
            seen: dict[str, str] = json.loads(kv_get("inbox_seen", "{}") or "{}")
            first_run = not seen
            for t in read.threads:
                if t.from_me or seen.get(t.name) == t.preview:
                    seen[t.name] = t.preview
                    continue
                seen[t.name] = t.preview
                if first_run:
                    continue  # don't flood Telegram with old threads on the very first run
                run.items += 1
                await _on_reply(t)
            kv_set("inbox_seen", json.dumps(seen, ensure_ascii=False))
            run.items += await _check_accepted(li)
        run.message = f"{run.items} inbox updates"


async def _on_reply(t: Thread) -> None:
    contact = _match_contact(t.name)
    if contact is None:
        await notifier.send(f"💬 <b>{esc(t.name)}</b> wrote on LinkedIn:\n<blockquote>{esc(t.preview)}</blockquote>"
                            "\nI don't have them in my list, so reply on LinkedIn yourself.")
        return
    with session() as s:
        c = s.get(Contact, contact.id)
        c.state, c.last_reply = "replied", t.preview
        lead = s.get(Lead, c.lead_id) if c.lead_id else None
        if lead:
            lead.status = "replied"
        context = f"{lead.role} at {lead.company}" if lead else "your earlier message"
    draft = await asyncio.to_thread(
        get_llm().json,
        f"{t.name} replied to Sumit on LinkedIn about {context}. Their message: \"{t.preview}\"\n"
        "Write Sumit's reply following <style> (answer their exact question first). If they ask for a phone "
        "number, CV, salary or call time, say Sumit will share it shortly instead of inventing anything.",
        Draft, _system(), True)
    await _queue_dm(contact.profile_url, contact.name, draft.text, contact.lead_id,
                    f"💬 <b>{esc(t.name)}</b> replied:\n<blockquote>{esc(t.preview)}</blockquote>\nMy suggested answer:")


async def _check_accepted(li: McpTools) -> int:
    with session() as s:
        waiting = list(s.scalars(select(Contact).where(Contact.state == "requested")
                                 .order_by(Contact.updated_at).limit(8)))
    found = 0
    for c in waiting:
        try:
            raw = await li.call("search_people", {"keywords": c.name, "network": ["F"]})
        except ratelimit.RateLimited:
            break
        if c.profile_url not in sources.parse_result(raw).people:
            with session() as s:
                s.get(Contact, c.id).updated_at = utcnow()  # rotate to the back of the queue
            continue
        found += 1
        with session() as s:
            row = s.get(Contact, c.id)
            row.state, row.degree = "connected", "1st"
            dm = row.pending_dm
        if dm:
            await _queue_dm(c.profile_url, c.name, dm, c.lead_id,
                            f"🤝 <b>{esc(c.name)}</b> accepted your request. Send them this?")
        else:
            await notifier.send(f"🤝 <b>{esc(c.name)}</b> accepted your connection request.")
    return found


async def _queue_dm(profile: str, name: str, text: str, lead_id: str, context: str) -> str:
    """A DM waiting for Sumit's Send. Returns the action id."""
    flags = guard.check(text, public=False)
    with session() as s:
        action = Action(id=next_id(s, "A"), kind="dm", lead_id=lead_id, target=sources.profile_url(
            sources.profile_path(profile)), body=text, guard_flags="; ".join(flags))
        approvals.transition(action, "pending")
        s.add(action)
    await notifier.send(cards.action_card(action, name, context), cards.action_buttons(action))
    return action.id


# ───────────────────────────── follow-up ─────────────────────────────


async def followup() -> None:
    s_ = get_settings()
    async with logged_run(NAME, "followup") as run:
        cutoff = utcnow() - timedelta(days=s_.followup_after_days)
        with session() as s:
            due = list(s.scalars(select(Contact).where(
                Contact.state == "messaged", Contact.followed_up == 0, Contact.last_sent_at < cutoff).limit(5)))
        for c in due:
            with session() as s:
                lead = s.get(Lead, c.lead_id) if c.lead_id else None
                about = f"the {lead.role} role at {lead.company}" if lead else "my earlier message"
                s.get(Contact, c.id).followed_up = 1
            draft = await asyncio.to_thread(
                get_llm().json,
                f"Sumit messaged {c.name} about {about} {s_.followup_after_days}+ days ago and got no reply. Write "
                "ONE short, polite follow-up (see <style>, 1-2 sentences).", Draft, _system(), True)
            await _queue_dm(c.profile_url, c.name, draft.text, c.lead_id, "⏰ No reply yet. Follow up once?")
            run.items += 1
        run.message = f"{run.items} follow-ups drafted"


# ───────────────────────────── report ─────────────────────────────


async def report() -> None:
    async with logged_run(NAME, "report") as run:
        since = _today_start_utc()
        with session() as s:
            leads = list(s.scalars(select(Lead).where(Lead.created_at >= since)))
            acts = list(s.scalars(select(Action).where(Action.updated_at >= since)))
            pending = list(s.scalars(select(Action).where(Action.status == "pending")))
            hot_open = list(s.scalars(select(Lead).where(Lead.score == "HOT", Lead.status.in_(["new", "commented"]))
                                      .order_by(Lead.created_at.desc()).limit(5)))
            replies = s.scalar(select(func.count()).select_from(Contact).where(
                Contact.state == "replied", Contact.updated_at >= since)) or 0
            pipeline = dict(s.execute(select(Lead.status, func.count()).group_by(Lead.status)).all())
        done = [a for a in acts if a.status == "published"]
        count = {k: sum(1 for a in done if a.kind == k) for k in ("comment", "connect", "dm")}
        mode = "🧪 DRY RUN" if get_settings().dry_run else "🟢 LIVE"
        paused = " · ⏸ LinkedIn paused today" if ratelimit.is_paused("linkedin_reader") else ""
        lines = [
            f"🌙 <b>Khushi's report</b> · {datetime.now(_tz()):%d %b} · {mode}{paused}",
            "",
            f"🔎 Found today: {len(leads)} ({sum(lead.score == 'HOT' for lead in leads)} 🔥 HOT)",
            f"📤 Done today: 💬 {count['comment']} comments · 🤝 {count['connect']} requests · ✉️ {count['dm']} DMs",
            f"💬 Replies today: {replies}",
            f"⏳ Waiting for your tap: {len(pending)}",
            "📊 Pipeline: " + (" · ".join(f"{k} {v}" for k, v in sorted(pipeline.items())) or "empty"),
        ]
        if hot_open:
            lines += ["", "🔥 <b>Act on these</b>"]
            lines += [f"• {esc(lead.role)} · {esc(lead.company)} ({lead.id})" for lead in hot_open]
        lines += ["", "⏰ <b>Next:</b> " + _next_step(pending, hot_open)]
        await notifier.send("\n".join(lines))
        run.items = len(leads)


def _next_step(pending: list[Action], hot_open: list[Lead]) -> str:
    if pending:
        return f"tap Send/Edit on the {len(pending)} waiting message(s) above."
    if hot_open:
        return f"open {hot_open[0].id} and tap 🤝 Connect, or apply from the link."
    return "nothing waiting. I'll scan again in the morning."


# ───────────────────────────── buttons ─────────────────────────────


async def on_lead_button(lead_id: str, choice: str, user_id: int) -> str:
    with session() as s:
        lead = s.get(Lead, lead_id)
        if lead is None:
            return "That lead no longer exists."
        poster, name, company = lead.poster_url, lead.poster_name, lead.company
        if choice in {"applied", "skip"}:
            lead.status = "applied" if choice == "applied" else "skipped"
            return f"{lead_id} marked {lead.status}."
    if not poster:
        return "No verified LinkedIn profile for this poster, so I can't contact them from here."
    if choice == "connect":
        with session() as s:
            action = Action(id=next_id(s, "A"), kind="connect", lead_id=lead_id, target=poster)
            approvals.transition(action, "pending")
            approvals.transition(action, "approved", by=user_id)  # the tap on Connect is the approval
            s.add(action)
        return await actions.execute(action.id)
    if choice == "dm":
        with session() as s:
            c = s.scalar(select(Contact).where(Contact.profile_url == sources.profile_path(poster)))
            text = c.pending_dm if c else ""
        if not text:
            draft = await asyncio.to_thread(
                get_llm().json, f"Write Sumit's first LinkedIn message to {name} about the opportunity at {company} "
                f"(lead {lead_id}). Follow <style>.", Draft, _system(), True)
            text = draft.text
        await _queue_dm(poster, name, text, lead_id, "Ready to send:")
        return "Draft below: tap ✅ Send, ✏️ Edit or ✖ Reject."
    return "Unknown button."


async def on_action_button(action_id: str, choice: str, user_id: int) -> tuple[str, bool]:
    """(reply, wants_edit_instruction)."""
    with session() as s:
        action = s.get(Action, action_id)
        if action is None:
            return "That draft no longer exists.", False
        try:
            if choice == "send":
                approvals.transition(action, "approved", by=user_id)
            elif choice == "reject":
                approvals.transition(action, "rejected", by=user_id)
            elif choice == "edit":
                approvals.transition(action, "edit_requested", by=user_id)
        except approvals.ApprovalError as e:
            return str(e), False
    if choice == "send":
        result = await actions.execute(action_id)
        if result.startswith("❌ LinkedIn won't let you message"):
            with session() as s:
                a = s.get(Action, action_id)
                c = s.scalar(select(Contact).where(Contact.profile_url == sources.profile_path(a.target)))
                if c:
                    c.pending_dm = a.body
        return result, False
    if choice == "edit":
        return "✏️ What should I change? Reply with a short instruction.", True
    return f"✖ {action_id} rejected.", False


async def on_edit(action_id: str, instruction: str) -> None:
    with session() as s:
        action = s.get(Action, action_id)
        old, target = action.body, action.target
    draft = await asyncio.to_thread(
        get_llm().json, f"Rewrite this LinkedIn message from Sumit.\n\nMessage:\n{old}\n\nSumit's instruction: "
        f"{instruction}\n\nKeep every hard rule.", Draft, _system(), True)
    flags = guard.check(draft.text, public=False)
    with session() as s:
        action = s.get(Action, action_id)
        action.body, action.edit_instruction, action.guard_flags = draft.text, instruction, "; ".join(flags)
        approvals.transition(action, "pending")
    await notifier.send(cards.action_card(action, target, "Redrafted:"), cards.action_buttons(action))


# ───────────────────────────── registry interface ─────────────────────────────

JOBS = {"scan": scan, "inbox": inbox, "followup": followup, "report": report}


def schedule() -> list[tuple[str, str, str]]:
    s = get_settings()
    return ([("scan", t, "daily") for t in s.scan_slots]
            + [("followup", s.followup_time, "daily"), ("report", s.report_time, "daily")])


def repeating() -> dict[str, int]:
    """Jobs that run every N seconds instead of at a clock time."""
    return {"inbox": get_settings().inbox_every_minutes * 60}
