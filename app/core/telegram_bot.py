"""The always-on process: Khushi's Telegram bot (cards, buttons, commands, chat) + her scheduled jobs.

Only the chat in TELEGRAM_CHAT_ID is served, and only APPROVER_IDS can tap anything that acts on LinkedIn.
"""

from __future__ import annotations

import logging
from datetime import time as dtime
from zoneinfo import ZoneInfo

from telegram import ForceReply, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.agents import registry
from app.agents.khushi import agent as khushi
from app.config import get_settings
from app.core import approvals, chat, incidents, ratelimit, viewer
from app.core.db import Run, last_runs, session
from app.core.notify import esc, notifier
from app.core.schedules import DAYS

log = logging.getLogger(__name__)

HELP = (
    "👋 <b>Khushi</b> here, your job-search employee.\n\n"
    "I scan LinkedIn at {scans}, check your inbox every {inbox} min, and send a report at {report}.\n\n"
    "/scan: search now\n/inbox: check replies and accepted requests now\n/report: today's report now\n"
    "/pipeline: every open lead\n/status: my last runs\n"
    "/linkedin_login: log LinkedIn in on the server (when I ask)\n"
    "/comment_auth: connect the LinkedIn app I comment with (~every 60 days)\n"
    "/pause · /resume: stop or restart my scheduled work\n/llm: which Claude login I use\n/myid: your ids\n\n"
    "Or just talk to me: <i>what did you find today?</i>"
)


def _ok_chat(update: Update) -> bool:
    return str(update.effective_chat.id) == str(get_settings().telegram_chat_id)


async def _guarded(update: Update) -> bool:
    """Only Sumit's chat, and only approvers, may make Khushi act."""
    if not _ok_chat(update):
        return False
    if not approvals.is_approver(update.effective_user.id):
        await update.effective_message.reply_text("Only Sumit can do that.")
        return False
    return True


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_settings()
    await update.effective_message.reply_html(
        HELP.format(scans=", ".join(s.scan_slots), inbox=s.inbox_every_minutes, report=s.report_time))


async def cmd_myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"user id: {update.effective_user.id}\nchat id: {update.effective_chat.id}")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ok_chat(update):
        return
    icon = {"ok": "✅", "error": "❌", "running": "⏳"}
    lines = [f"{icon.get(r.status, '•')} {r.job or 'run'} · {r.started_at:%d %b %H:%M} UTC · {r.items} · {esc(r.message)}"
             for r in last_runs(10)]
    s = get_settings()
    flags = ["🧪 DRY RUN" if s.dry_run else "🟢 LIVE"]
    if incidents.is_paused("khushi"):
        flags.append("⏸ paused (/resume)")
    if ratelimit.is_paused("linkedin_reader"):
        flags.append("🔒 LinkedIn paused today")
    await update.effective_message.reply_html(f"<b>Status</b> ({' · '.join(flags)})\n" + ("\n".join(lines) or "No runs yet."))


def _job_command(job: str):
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _guarded(update):
            return
        await update.effective_message.reply_text(
            {"scan": "🔎 Scanning LinkedIn (takes a few minutes, I go slowly to keep your account safe)…",
             "inbox": "📥 Checking your inbox and pending requests…", "report": "🌙 Writing the report…"}[job])
        ctx.application.create_task(khushi.JOBS[job]())

    return handler


async def cmd_pipeline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ok_chat(update):
        return
    from sqlalchemy import select

    from app.core.db import Lead

    open_states = ["new", "commented", "connected", "messaged", "replied", "interview"]
    with session() as s:
        leads = list(s.scalars(select(Lead).where(Lead.status.in_(open_states), Lead.score != "SKIP")
                               .order_by(Lead.created_at.desc()).limit(25)))
    if not leads:
        await update.effective_message.reply_text("No open leads yet. Try /scan.")
        return
    lines = [f"{'🔥' if lead.score == 'HOT' else '🟡'} {lead.id} · {esc(lead.role)} · {esc(lead.company)} · "
             f"<i>{lead.status}</i>" for lead in leads]
    await update.effective_message.reply_html("<b>Open leads</b>\n" + "\n".join(lines))


async def cmd_linkedin_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guarded(update):
        return
    await update.effective_message.reply_text("🔐 Starting the LinkedIn login browser on the server…")
    try:
        url = await viewer.start()
    except Exception as e:
        log.exception("login viewer failed")
        await update.effective_message.reply_text(f"Couldn't start it: {incidents.redact(str(e))[:300]}")
        return
    await update.effective_message.reply_html(
        "🔐 <b>Log LinkedIn in</b>\n1. Open this private link (valid 30 min, don't share it):\n"
        f"{esc(url)}\n2. You'll see a browser: log in to <b>your</b> LinkedIn, do any security check.\n"
        "3. When you see your feed, just close the page. I'll confirm here.")


async def cmd_comment_auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guarded(update):
        return
    from app.core import linkedin_login

    try:
        url = await linkedin_login.start()
    except Exception as e:
        log.exception("comment auth start failed")
        await update.effective_message.reply_text(f"Couldn't start: {incidents.redact(str(e))[:300]}")
        return
    ctx.chat_data["awaiting_comment_auth"] = update.effective_user.id
    await update.effective_message.reply_html(
        "💬 <b>Connect commenting</b>\n1. Open this link, log in to your LinkedIn and tap <b>Allow</b>:\n"
        f"{esc(url)}\n2. The next page won't load (it goes to <code>localhost</code>). That's expected.\n"
        "3. Copy the whole address from the browser bar and send it here within 30 minutes.",
        reply_markup=ForceReply(selective=True))


async def _finish_comment_auth(update: Update) -> None:
    from app.core import linkedin_login

    msg = update.effective_message
    try:
        await msg.delete()  # it carries a one-time login code
    except Exception:
        pass
    try:
        await linkedin_login.finish(msg.text or "")
    except Exception as e:
        await msg.chat.send_message(f"❌ Didn't finish: {incidents.redact(str(e))[:300]}\nSend /comment_auth again.")
        return
    await msg.chat.send_message("✅ Commenting connected. I'll comment on strong-fit posts (max "
                                f"{get_settings().comments_per_day}/day).")


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if await _guarded(update):
        incidents.pause("khushi")
        await update.effective_message.reply_text("⏸ Paused. My scheduled work stops until /resume.")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if await _guarded(update):
        incidents.resume("khushi")
        ratelimit.resume("linkedin_reader")
        await update.effective_message.reply_text("▶️ Back on. Scheduled scans, inbox checks and reports resume.")


async def cmd_llm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"Claude login: {get_settings().llm_auth}")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    user_id = update.effective_user.id
    if not approvals.is_approver(user_id):
        await q.answer("Only Sumit can do this.", show_alert=True)
        return
    kind, record_id, choice = q.data.split(":", 2)

    if kind == "inc":
        with session() as s:
            run = s.get(Run, int(record_id))
            job = run.job if run else None
        await q.edit_message_reply_markup(None)
        if choice == "retry" and job in khushi.JOBS:
            await q.answer("Retrying…")
            ctx.application.create_task(khushi.JOBS[job]())
        elif choice == "pause":
            incidents.pause("khushi")
            await q.answer("Paused")
            await q.message.reply_text("⏸ Paused until /resume.")
        else:
            await q.answer("Ignored")
        return

    await q.answer("On it…")
    await q.edit_message_reply_markup(None)
    if kind == "lead":
        reply = await khushi.on_lead_button(record_id, choice, user_id)
        await q.message.reply_html(esc(reply))
    elif kind == "act":
        reply, wants_edit = await khushi.on_action_button(record_id, choice, user_id)
        if wants_edit:
            ctx.chat_data.setdefault("awaiting_edit", {})[user_id] = record_id
            await q.message.reply_text(reply, reply_markup=ForceReply(selective=True))
        else:
            await q.message.reply_html(esc(reply))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ok_chat(update):
        return
    msg, user_id = update.effective_message, update.effective_user.id
    if ctx.chat_data.get("awaiting_comment_auth") == user_id and "code=" in (msg.text or ""):
        ctx.chat_data.pop("awaiting_comment_auth")
        ctx.application.create_task(_finish_comment_auth(update))
        return
    pending = ctx.chat_data.get("awaiting_edit", {})
    if user_id in pending:
        action_id = pending.pop(user_id)
        await msg.reply_text(f"Redrafting {action_id}…")
        ctx.application.create_task(khushi.on_edit(action_id, msg.text or ""))
        return
    try:
        await ctx.bot.send_chat_action(msg.chat_id, "typing")
        answer = await chat.answer(msg.text or "", approvals.is_approver(user_id))
    except ratelimit.RateLimited:
        answer = "I've chatted a lot today and hit my daily limit. Ask me again tomorrow."
    except Exception:
        log.exception("chat failed")
        answer = "Sorry, I couldn't answer that right now. Try again in a minute."
    await msg.reply_text(answer[:4000])


async def _post_init(app: Application) -> None:
    notifier.attach(app.bot)
    s = get_settings()
    tz = ZoneInfo(s.timezone)
    for name, agent in registry.AGENTS.items():
        for job_name, hhmm, days in agent.schedule():
            h, m = map(int, hhmm.split(":"))

            async def _job(_ctx, fn=agent.JOBS[job_name], name=name):
                if not incidents.is_paused(name):
                    await fn()

            app.job_queue.run_daily(_job, time=dtime(h, m, tzinfo=tz), days=DAYS[days], name=f"{name}.{job_name}")
            log.info("scheduled %s.%s at %s IST (%s)", name, job_name, hhmm, days)
        for job_name, every_s in agent.repeating().items():

            async def _rep(_ctx, fn=agent.JOBS[job_name], name=name):
                if not incidents.is_paused(name):
                    await fn()

            app.job_queue.run_repeating(_rep, interval=every_s, first=300, name=f"{name}.{job_name}")
    from app.doctor import startup_report

    await notifier.send(startup_report(html=True))


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Any handler failure: tell Sumit once, log the traceback."""
    import traceback

    err = ctx.error
    log.error("handler failed", exc_info=err)
    reason = incidents.redact(f"{type(err).__name__}: {err}")[:180]
    q = getattr(update, "callback_query", None) if isinstance(update, Update) else None
    try:
        if q:
            await q.answer(f"⚠️ That didn't work: {reason}", show_alert=True)
        elif isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(f"⚠️ That didn't work: {reason}")
    except Exception:
        log.exception("could not report the error")
    tb = "".join(traceback.format_exception(err)) if err else reason
    if incidents.should_alert("telegram", "handler", incidents.redact(tb)):
        await notifier.alert(f"A button/command failed: {reason}")


def build_app() -> Application:
    s = get_settings()
    app = Application.builder().token(s.telegram_bot_token).post_init(_post_init).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("llm", cmd_llm))
    for job in ("scan", "inbox", "report"):
        app.add_handler(CommandHandler(job, _job_command(job)))
    app.add_handler(CommandHandler("pipeline", cmd_pipeline))
    app.add_handler(CommandHandler("linkedin_login", cmd_linkedin_login))
    app.add_handler(CommandHandler("comment_auth", cmd_comment_auth))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def serve() -> None:
    s = get_settings()
    if not s.telegram_enabled:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to run `serve`.")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)
