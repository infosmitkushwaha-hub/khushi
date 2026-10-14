# CLAUDE.md: Khushi

Khushi is Sumit's AI job-search employee (Telegram + LinkedIn MCP + Claude, on Railway). Read `README.md` first.

## Rules for working in this repo

- **Never put secrets in chat, code, commits or logs.** Values live in `.env` (git-ignored) and Railway variables.
  Check a secret by using it (`python -m app doctor --live`), which prints only ✅/❌.
- **The safety model is in code; don't weaken it.**
  - `app/core/approvals.py`: connect/DM always need Sumit's tap; only comments may self-approve.
  - `app/core/guard.py`: Sumit's truth rules.
  - `config/mcp_servers.yaml`: `approval_only`, `deny_always`, caps, hours.
  - `app/agents/khushi/sources.py`: profile URLs and post URNs only from what LinkedIn returned.
  - `DRY_RUN=true` until Sumit says go.
- **LinkedIn only:** never add email or any other outreach channel.
- **Never retry an uncertain LinkedIn send** (`send_unconfirmed` / `retry_safe: false`): it can deliver twice.
- **LinkedIn reader:** it's Sumit's fork, pinned by sha in the `Dockerfile` (`LINKEDIN_MCP_REF`). Fix bugs there
  (`D:\linkdin mcp latest`), push, then bump the sha.
- Before committing: `ruff check .` and `pytest -q` must pass (CI runs both). One commit per coherent change,
  ending with a `Co-Authored-By` trailer.
- Match the existing style: small modules, typed, comments only where they explain *why*.

## Map

- `app/core/`:
  - `llm`: OAuth/API switch.
  - `db`: Lead · Contact · Action · Run · ToolCall.
  - `approvals`, `guard`, `mcp_client` (gates, caps, session alerts, one lock per server).
  - `ratelimit`, `runlog`, `incidents`, `notify`, `telegram_bot`, `chat`, `viewer` (`/linkedin_login`),
    `linkedin_login` (`/comment_auth`), `knowledge`.
- `app/agents/khushi/`:
  - `agent`: scan · inbox · followup · report + button logic.
  - `sources`: parsing + link verification.
  - `actions`: the only LinkedIn writes.
  - `cards`.
- `knowledge/`: Khushi's brain (profile, projects, targets, style, hard rules).
- `app/seed.py`: history done by hand before Khushi, so nobody is contacted twice.
