"""Stand-ins for Claude and the MCP servers, so Khushi's flows run in tests without any network."""

from __future__ import annotations

import json
from typing import Any

from app.core import approvals


class FakeLLM:
    """Returns a canned object per requested schema (or a callable taking the prompt)."""

    mode = "fake"

    def __init__(self, by_schema: dict, text: str = "ok"):
        self.by_schema = by_schema
        self._text = text
        self.prompts: list[str] = []

    def json(self, prompt, schema, system="", smart=True, web_search=False):
        self.prompts.append(prompt)
        obj = self.by_schema[schema]
        if callable(obj) and not hasattr(obj, "model_copy"):
            return obj(prompt)
        return obj.model_copy(deep=True)

    def text(self, prompt, system="", smart=True):
        self.prompts.append(prompt)
        return self._text


class FakeTools:
    """Drop-in for McpTools. `responses[tool]` is a str, a dict (sent as JSON) or a callable(args)."""

    responses: dict[str, Any] = {}
    calls: list[tuple[str, str, dict]] = []
    WRITES = {"connect_with_person", "send_message", "linkedin_create_comment"}

    def __init__(self, server: str, agent: str):
        self.server = server

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def call(self, tool: str, args: dict, approved=None) -> str:
        if tool in self.WRITES:
            if approved is None:
                raise AssertionError(f"{tool} called without an approved action")
            approvals.assert_publishable(approved)
        FakeTools.calls.append((self.server, tool, args))
        r = FakeTools.responses.get(tool, {})
        r = r(args) if callable(r) else r
        return r if isinstance(r, str) else json.dumps(r)


def search_result(text: str, people: dict[str, str] | None = None, permalinks: list[str] | None = None,
                  job_ids: list[str] | None = None) -> dict:
    """What the reader's search_posts/search_jobs return, in its JSON shape."""
    refs = [{"kind": "person", "url": url, "text": name} for url, name in (people or {}).items()]
    refs += [{"kind": "feed_post", "url": url, "context": "search_results"} for url in permalinks or []]
    out: dict[str, Any] = {"url": "https://www.linkedin.com/search/", "sections": {"search_results": text},
                           "references": {"search_results": refs}}
    if job_ids is not None:
        out["job_ids"] = job_ids
    return out
