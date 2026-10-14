# Khushi: AI job-search employee

Khushi works for **Sumit Kushwaha** (AI Generalist, Ranchi). She lives in Telegram, searches LinkedIn through the
LinkedIn MCP, and works the job pipeline every day:

| Job | When (IST) | What she does | Your part |
|---|---|---|---|
| **scan** | 09:00 · 13:00 · 18:00 | Searches LinkedIn posts + jobs (Ranchi first, then Jharkhand, then remote), scores each against your profile, sends you a card | none; strong fits get a comment by herself (max 10/day) |
| **inbox** | every 2 h, 08:00–21:00 | Spots new replies and accepted connection requests, drafts the next message | tap **Send / Edit / Reject** |
| **followup** | 11:00 | One polite nudge to people who didn't answer in 3 days | tap **Send** |
| **report** | 20:00 | The day in one message: found, done, replies, what to do next | read it |

On each lead card: **🤝 Connect** sends a request (your tap is the approval), **✉️ DM** drafts a message,
**✅ Applied** and **🙈 Skip** keep the pipeline tidy.

## Rules she can't break (enforced in code)

- **LinkedIn only.** Never email.
- **Truth:** you're studying 12th Commerce with about 3 years of freelance work. No degree, years, results or pay
  figures that aren't true. See `app/core/guard.py`.
- **No phone number in public comments.**
- **Only profile links LinkedIn itself returned.** A comment only goes on a post whose permalink carries the
  author's profile slug.
- **Connect and DMs need your tap** (`app/core/approvals.py`). A DM LinkedIn didn't confirm is never re-sent.
- **Daily caps and quiet hours** on every LinkedIn action (`config/mcp_servers.yaml`).
- `DRY_RUN=true` until you say go.

## Her brain: `knowledge/`

Plain markdown she reads on every run. Edit it any time:

- `profile.md`: the true facts.
- `projects.md`: proof she may cite.
- `targets.md`: roles, places, skip list, scoring, search keywords.
- `style.md`: how messages sound.
- `hard_rules.md`: the rules above.

## How it fits together

```
JobQueue (IST) ─► scan / inbox / followup / report
                    │
                    ├─► LinkedIn reader MCP (your fork, browser on Xvfb, logged in via /linkedin_login)
                    ├─► Claude (`claude -p` OAuth, or API key)  ─► guard (hard rules)
                    ├─► SQLite on the volume (leads · contacts · actions · runs)
                    └─► Telegram cards + buttons ─► approvals.assert_publishable ─► LinkedIn write
                                                     (comment: posting MCP · connect/DM: reader MCP)
```

## Commands

```bash
python -m app doctor --live      # what's configured, and does each connection work
python -m app run scan --console # one scan now, printed instead of sent
python -m app seed               # load the history done by hand (runs on every container start)
python -m app serve              # always on: Telegram + schedule
```

Telegram: `/scan` · `/inbox` · `/report` · `/pipeline` · `/status` · `/linkedin_login` · `/comment_auth` ·
`/pause` · `/resume` · `/llm` · `/myid`. Or just ask her: *what did you find today?*

## Local development

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
cp .env.example .env
pytest -q && ruff check .
```

Deploy: see [docs/DEPLOY.md](docs/DEPLOY.md).
