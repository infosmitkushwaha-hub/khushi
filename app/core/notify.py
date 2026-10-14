"""Delivery to Sumit's Telegram chat (or the console when Telegram isn't configured / dry-run CLI)."""

from __future__ import annotations

import html
import logging
from contextvars import ContextVar

from app.config import get_settings

log = logging.getLogger(__name__)

Buttons = list[list[tuple[str, str]]]  # rows of (label, callback_data)

# Which job is speaking (set by logged_run); kept so log lines and alerts can say who failed.
speaker: ContextVar[str | None] = ContextVar("speaker", default=None)


def esc(text: str | None) -> str:
    return html.escape(text or "")


class Notifier:
    def __init__(self) -> None:
        self.bot = None  # set by the Telegram app in serve mode, or lazily created
        self._ready = False

    async def _get_bot(self):
        s = get_settings()
        if not s.telegram_enabled:
            return None
        if self.bot is None:
            from telegram import Bot

            self.bot = Bot(s.telegram_bot_token)
        if not self._ready:
            await self.bot.initialize()
            self._ready = True
        return self.bot

    def attach(self, bot) -> None:
        self.bot, self._ready = bot, True

    async def send(self, text_html: str, buttons: Buttons | None = None, console_only: bool = False) -> int | None:
        s = get_settings()
        if s.dry_run:
            text_html = "🧪 <i>[DRY RUN]</i>\n" + text_html
        bot = None if console_only else await self._get_bot()
        if bot is None:
            print("\n" + "─" * 60 + "\n" + _strip_html(text_html))
            if buttons:
                print("   " + "  ".join(f"[{label}]" for row in buttons for label, _ in row))
            return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        markup = (
            InlineKeyboardMarkup([[InlineKeyboardButton(lbl, callback_data=data) for lbl, data in row] for row in buttons])
            if buttons
            else None
        )
        msg = await bot.send_message(
            chat_id=s.telegram_chat_id, text=text_html[:4096], parse_mode="HTML",
            reply_markup=markup, disable_web_page_preview=True,
        )
        return msg.message_id

    async def alert(self, text: str) -> None:
        try:
            await self.send(f"⚠️ <b>Alert</b>\n{esc(text)}")
        except Exception:  # alerts must never crash the caller
            log.exception("failed to send alert")


def _strip_html(t: str) -> str:
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", t))


notifier = Notifier()
