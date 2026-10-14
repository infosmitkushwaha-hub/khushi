# Vendored: LinkedIn reader MCP (Sumit's fork)

Snapshot of `infosmitkushwaha-hub/sumit-linkdin-mcp-latest-` at commit
`592f63a2476aa9bcc9c24c513411b573c10c3a24` (upstream: stickerdaniel/linkedin-mcp-server 4.24.3, Apache-2.0).
Vendored because the fork is private and Railway's build can't clone it.

Fork-only fixes in this snapshot:
- `fix(connect)`: the note editor's Premium link is no longer read as an exhausted note quota (19c656e).
- `fix(message)`: `send_message` resolves the Message action on LinkedIn's current top card (592f63a).

To update: commit + push in `D:\linkdin mcp latest`, then re-run the `git archive` from `CLAUDE.md` with the new sha.
