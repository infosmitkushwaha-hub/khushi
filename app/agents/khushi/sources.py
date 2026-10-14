"""LinkedIn reads through the reader MCP, and the code-side checks that keep Khushi honest.

The reader returns page text plus a list of links it actually saw. The LLM reads the text, but every profile URL
and post id it hands back is checked here against those links: a URL that LinkedIn didn't return is dropped, and a
post id is only trusted when the post's own permalink carries the author's profile slug. That is what stops a
comment landing on the wrong post or a request going to a guessed profile.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

PROFILE_PATH = re.compile(r"^/in/([^/?#]+)/?$")
PERMALINK = re.compile(r"^/posts/(?P<slug>[^/]+?)-(?P<kind>share|activity|ugcPost)-(?P<id>\d+)(?:-[^/]*)?/?$")
FEED_UPDATE = re.compile(r"^/feed/update/(urn:li:(?:share|activity|ugcPost):\d+)/?$")


def profile_path(url: str) -> str:
    """'/in/<slug>/' for any LinkedIn profile URL or path, '' otherwise."""
    path = re.sub(r"^https?://(?:www\.)?linkedin\.com", "", (url or "").strip())
    path = path.split("?")[0].split("#")[0]
    if not path.endswith("/"):
        path += "/"
    m = PROFILE_PATH.match(path)
    # LinkedIn's opaque member ids (/in/ACoAA…) are fine to message but not to show or dedupe on
    return f"/in/{m.group(1)}/" if m else ""


def profile_url(path: str) -> str:
    return f"https://www.linkedin.com{path}" if path else ""


@dataclass
class Permalink:
    url: str
    urn: str
    author_slug: str  # '' for company posts like /posts/hiring-share-…


def parse_permalink(url: str) -> Permalink | None:
    path = re.sub(r"^https?://(?:www\.)?linkedin\.com", "", url or "").split("?")[0]
    if m := PERMALINK.match(path):
        slug = m.group("slug")
        author = slug.split("_", 1)[0] if "_" in slug else ""
        return Permalink(url=f"https://www.linkedin.com{path}", urn=f"urn:li:{m.group('kind')}:{m.group('id')}",
                         author_slug=author)
    return None


@dataclass
class SearchBlock:
    """One search result page as the LLM will see it, plus the links LinkedIn really showed."""

    keywords: str
    text: str
    people: dict[str, str] = field(default_factory=dict)  # '/in/slug/' -> name shown
    permalinks: list[Permalink] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)


def parse_result(raw: str, keywords: str = "") -> SearchBlock:
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return SearchBlock(keywords=keywords, text=raw)
    text = "\n\n".join(str(v) for v in (data.get("sections") or {}).values())
    block = SearchBlock(keywords=keywords, text=text, job_ids=[str(j) for j in data.get("job_ids") or []])
    for refs in (data.get("references") or {}).values():
        for ref in refs or []:
            url = ref.get("url", "")
            if ref.get("kind") == "person" and (p := profile_path(url)):
                block.people.setdefault(p, (ref.get("text") or "").strip())
            elif ref.get("kind") == "feed_post" and (pl := parse_permalink(url)):
                if all(x.urn != pl.urn for x in block.permalinks):
                    block.permalinks.append(pl)
    return block


def verified_post_urn(block_permalinks: list[Permalink], chosen_url: str, author_path: str) -> str:
    """The post's URN only when LinkedIn returned that permalink AND it belongs to the chosen author."""
    chosen = parse_permalink(chosen_url)
    if not chosen or not author_path:
        return ""
    if not any(p.urn == chosen.urn for p in block_permalinks):
        return ""
    author_slug = author_path.strip("/").split("/")[-1]
    return chosen.urn if chosen.author_slug and chosen.author_slug == author_slug else ""


def known_profile(block_people: dict[str, str], url: str) -> str:
    """The profile path only if LinkedIn returned it in this result; never a guessed one."""
    p = profile_path(url)
    return p if p and p in block_people else ""


def mcp_status(raw: str) -> dict[str, Any]:
    """The JSON a write tool returned ({} if it wasn't JSON)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def one_line(text: str) -> str:
    """send_message rejects control characters, including newlines: join paragraphs with a space."""
    return re.sub(r"\s*[\r\n\t]+\s*", " ", text).strip()
