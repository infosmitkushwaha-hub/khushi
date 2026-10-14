"""The rules that keep Sumit's account and reputation safe: approvals, tool gates, link checks, session alerts."""

import pytest

from app.agents.khushi import sources
from app.core import approvals, mcp_client, ratelimit
from app.core.db import Action


def _action(kind: str, status: str = "draft") -> Action:
    return Action(id="A-0001", kind=kind, target="x", body="hi", status=status)


@pytest.mark.parametrize("kind", ["connect", "dm"])
def test_connect_and_dm_can_never_approve_themselves(kind):
    with pytest.raises(approvals.ApprovalError):
        approvals.auto_approve(_action(kind))


def test_comment_auto_approves_only_with_auto_comment(monkeypatch):
    a = _action("comment")
    approvals.auto_approve(a)
    approvals.assert_publishable(a)

    from app.config import get_settings

    monkeypatch.setenv("AUTO_COMMENT", "false")
    get_settings.cache_clear()
    with pytest.raises(approvals.ApprovalError):
        approvals.assert_publishable(a)


def test_dm_needs_sumit_not_anyone():
    a = _action("dm", "pending")
    with pytest.raises(approvals.ApprovalError):
        approvals.transition(a, "approved", by=7)  # not an approver
    approvals.transition(a, "approved", by=42)
    approvals.assert_publishable(a)


def test_unapproved_dm_is_not_publishable():
    with pytest.raises(approvals.ApprovalError):
        approvals.assert_publishable(_action("dm", "pending"))


def test_write_tools_need_an_approved_action():
    cfg = mcp_client.load_config()["linkedin_reader"]
    with pytest.raises(mcp_client.ToolNotAllowed):
        mcp_client.check_allowed(cfg, "linkedin_reader", "khushi", "send_message")
    with pytest.raises(approvals.ApprovalError):
        mcp_client.check_allowed(cfg, "linkedin_reader", "khushi", "send_message", _action("dm", "pending"))
    ok = _action("dm", "pending")
    approvals.transition(ok, "approved", by=42)
    mcp_client.check_allowed(cfg, "linkedin_reader", "khushi", "send_message", ok)
    mcp_client.check_allowed(cfg, "linkedin_reader", "khushi", "search_posts")


def test_conversation_reads_are_blocked():
    cfg = mcp_client.load_config()["linkedin_reader"]
    with pytest.raises(mcp_client.ToolNotAllowed):
        mcp_client.check_allowed(cfg, "linkedin_reader", "khushi", "get_conversation")


def test_guessed_profile_urls_are_rejected():
    people = {"/in/priya-pathak-222191354/": "Priya Pathak"}
    assert sources.known_profile(people, "https://www.linkedin.com/in/priya-pathak-222191354/") \
        == "/in/priya-pathak-222191354/"
    assert sources.known_profile(people, "https://www.linkedin.com/in/riya-pandey/") == ""


def test_post_urn_only_when_the_permalink_belongs_to_the_author():
    links = [sources.parse_permalink(u) for u in (
        "/posts/priya-pathak-222191354_urgent-hiring-share-7509143174293118976-1rTZ",
        "/posts/hiring-share-7509576268623949824--Kx4",
    )]
    priya = "/in/priya-pathak-222191354/"
    assert sources.verified_post_urn(links, links[0].url, priya) == "urn:li:share:7509143174293118976"
    assert sources.verified_post_urn(links, links[1].url, priya) == ""  # company post, can't tie it to her
    assert sources.verified_post_urn(links, links[0].url, "/in/someone-else/") == ""
    assert sources.verified_post_urn(links, "/posts/priya-pathak-222191354_x-share-1-ab", priya) == ""  # not shown


async def test_session_expired_pauses_linkedin_and_alerts(monkeypatch):
    sent = []

    async def fake_alert(text):
        sent.append(text)

    monkeypatch.setattr(mcp_client.notifier, "alert", fake_alert)
    tools = mcp_client.McpTools("linkedin_reader", agent="khushi")
    await tools._handle_error("Session expired. Run with --login to create a new browser profile.")
    assert ratelimit.is_paused("linkedin_reader")
    assert sent and "/linkedin_login" in sent[0]


def test_one_line_strips_newlines_for_send_message():
    assert sources.one_line("Hi Priya,\n\nI saw your post.\r\nThanks") == "Hi Priya, I saw your post. Thanks"
