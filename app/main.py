"""Command line.

    python -m app serve                         # always-on: Telegram bot + scheduled jobs
    python -m app run [scan|inbox|followup|report] [--console]   # one job now (--console = print, no Telegram)
    python -m app llm-check                     # which Claude login is active + one test call
    python -m app comment-auth                  # one-time LinkedIn app login for commenting
    python -m app seed                          # load the people/posts already contacted by hand (no repeats)
    python -m app status                        # last runs
    python -m app doctor [--live]               # what's configured / missing, and test connections
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.config import get_settings
from app.core.db import init_db, last_runs


def _setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # emojis on Windows consoles
    s = get_settings()
    (s.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(s.data_dir / "logs" / "khushi.log", encoding="utf-8")],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_llm_check() -> None:
    from app.core.llm import get_llm

    s = get_settings()
    llm = get_llm()
    print(f"Claude login: {llm.mode}  (switch with LLM_AUTH=oauth|api_key)")
    print(f"Test call with {s.llm_model_fast}…")
    print("Reply:", llm.text("Reply with exactly: Khushi online", smart=False))


async def cmd_run(job: str, console: bool) -> None:
    from app.agents.khushi import agent
    from app.core.notify import notifier

    if console:
        orig = notifier.send

        async def console_send(text_html, buttons=None, console_only=False):
            return await orig(text_html, buttons, console_only=True)

        notifier.send = console_send  # type: ignore[method-assign]
    await agent.JOBS[job]()


async def cmd_comment_auth() -> None:
    from app.core import linkedin_login

    print("Open this link, log in to your LinkedIn and tap Allow:\n\n" + await linkedin_login.start())
    redirected = input("\nPaste the FULL address you were redirected to:\n> ")
    print(await linkedin_login.finish(redirected))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    r = sub.add_parser("run")
    r.add_argument("job", nargs="?", default="scan", choices=["scan", "inbox", "followup", "report"])
    r.add_argument("--console", action="store_true", help="print cards to the console instead of Telegram")
    sub.add_parser("llm-check")
    sub.add_parser("comment-auth")
    sub.add_parser("seed")
    sub.add_parser("status")
    d = sub.add_parser("doctor")
    d.add_argument("--live", action="store_true", help="also test each connection")
    args = p.parse_args(argv)

    _setup_logging()
    init_db()
    if args.cmd == "serve":
        from app.core.telegram_bot import serve
        from app.doctor import startup_report

        logging.getLogger("startup").info(startup_report())
        serve()
    elif args.cmd == "run":
        asyncio.run(cmd_run(args.job, args.console))
    elif args.cmd == "llm-check":
        cmd_llm_check()
    elif args.cmd == "comment-auth":
        asyncio.run(cmd_comment_auth())
    elif args.cmd == "seed":
        from app.seed import seed

        print(seed())
    elif args.cmd == "doctor":
        from app.doctor import doctor

        doctor(args.live)
    elif args.cmd == "status":
        for r_ in last_runs(15):
            print(f"{r_.started_at:%Y-%m-%d %H:%M} {r_.job:9} {r_.status:7} {r_.items:3} {r_.message}")
