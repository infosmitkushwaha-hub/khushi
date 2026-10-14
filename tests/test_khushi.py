"""Khushi's jobs end to end with fake LinkedIn + Claude: what gets saved, sent and held back."""

import pytest
from sqlalchemy import select

from app.agents.khushi import actions
from app.agents.khushi import agent as khushi
from app.config import get_settings
from app.core.db import Action, Contact, Lead, session
from tests.fakes import FakeLLM, FakeTools, search_result

PRIYA = "https://www.linkedin.com/in/priya-pathak-222191354/"
PRIYA_POST = "/posts/priya-pathak-222191354_urgent-hiring-share-7509143174293118976-1rTZ"
POST_TEXT = "Priya Pathak · HR Assistant at Emancipation Edutech · 1d\nURGENT HIRING – Content Creator, Ranchi"


def lead(**kw) -> khushi.LeadDraft:
    base = dict(kind="post", author_name="Priya Pathak", author_profile_url=PRIYA, permalink=PRIYA_POST,
                role="Content Creator", company="Emancipation Edutech", location="Ranchi", work_mode="on-site",
                posted="1d", poster_role="hr", score="HOT", speed="MEDIUM", why="content workflows",
                post_excerpt="URGENT HIRING – Content Creator, Ranchi",
                comment="Interested in the Content Creator role. I'm from Ranchi. Portfolio: "
                        "https://sumit-my-portfolio-rho.vercel.app",
                dm="Hi Priya, I saw your Content Creator post. Is the role still open?")
    base.update(kw)
    return khushi.LeadDraft(**base)


@pytest.fixture
def fakes(monkeypatch):
    FakeTools.calls = []
    FakeTools.responses = {
        "search_posts": search_result(POST_TEXT, {"/in/priya-pathak-222191354/": "Priya Pathak"}, [PRIYA_POST]),
        "search_jobs": search_result("no jobs", job_ids=[]),
        "linkedin_create_comment": {"success": True, "commentId": "1"},
        "connect_with_person": {"status": "pending", "note_sent": False},
        "send_message": {"sent": True, "status": "sent"},
    }
    monkeypatch.setattr(khushi, "McpTools", FakeTools)
    monkeypatch.setattr(actions, "McpTools", FakeTools)

    def use(by_schema):
        llm = FakeLLM(by_schema)
        monkeypatch.setattr(khushi, "get_llm", lambda: llm)
        return llm

    return use


def live(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()


def writes() -> list[str]:
    return [t for _, t, _ in FakeTools.calls if t in FakeTools.WRITES]


async def test_scan_saves_a_scored_lead_and_comments_in_dry_run(fakes):
    fakes({khushi.LeadBatch: khushi.LeadBatch(leads=[lead()])})
    await khushi.scan()
    with session() as s:
        saved = s.scalars(select(Lead)).all()
        comment = s.scalars(select(Action)).one()
        contact = s.scalars(select(Contact)).one()
    assert [(x.urn, x.poster_url) for x in saved] == [("urn:li:share:7509143174293118976", PRIYA)]
    assert (comment.kind, comment.status, comment.error) == ("comment", "published", "dry run: not sent")
    assert writes() == []  # dry run: nothing reached LinkedIn
    assert contact.pending_dm.startswith("Hi Priya")


async def test_live_scan_comments_once_and_never_twice(fakes, monkeypatch):
    live(monkeypatch)
    fakes({khushi.LeadBatch: khushi.LeadBatch(leads=[lead()])})
    await khushi.scan()
    await khushi.scan()  # same post again
    assert writes() == ["linkedin_create_comment"]
    with session() as s:
        assert s.scalar(select(Lead.status)) == "commented"
        assert len(s.scalars(select(Lead)).all()) == 1


async def test_guessed_profile_gets_no_contact_and_no_comment(fakes, monkeypatch):
    live(monkeypatch)
    fakes({khushi.LeadBatch: khushi.LeadBatch(leads=[lead(author_profile_url="https://www.linkedin.com/in/riya-pandey/")])})
    await khushi.scan()
    with session() as s:
        saved = s.scalars(select(Lead)).one()
        assert s.scalars(select(Contact)).all() == []
    assert saved.poster_url == ""
    assert writes() == []  # the permalink can't be tied to an unknown author, so no auto-comment


async def test_comment_that_breaks_a_rule_is_dropped(fakes, monkeypatch):
    live(monkeypatch)
    fakes({khushi.LeadBatch: khushi.LeadBatch(leads=[lead(comment="I'm a B.Tech graduate, call +91 9876543210")])})
    await khushi.scan()
    assert writes() == []


async def test_daily_comment_cap(fakes, monkeypatch):
    live(monkeypatch)
    monkeypatch.setenv("COMMENTS_PER_DAY", "0")
    get_settings.cache_clear()
    fakes({khushi.LeadBatch: khushi.LeadBatch(leads=[lead()])})
    await khushi.scan()
    assert writes() == []


async def test_connect_then_accept_then_dm_waits_for_send(fakes, monkeypatch):
    live(monkeypatch)
    fakes({khushi.LeadBatch: khushi.LeadBatch(leads=[lead()]),
           khushi.InboxRead: khushi.InboxRead(threads=[])})
    await khushi.scan()
    with session() as s:
        lead_id = s.scalar(select(Lead.id))

    assert "Connection request sent" in await khushi.on_lead_button(lead_id, "connect", 42)
    with session() as s:
        assert s.scalar(select(Contact.state)) == "requested"

    FakeTools.responses["get_inbox"] = search_result("inbox")
    FakeTools.responses["search_people"] = search_result("Priya", {"/in/priya-pathak-222191354/": "Priya Pathak"})
    await khushi.inbox()
    with session() as s:
        assert s.scalar(select(Contact.state)) == "connected"
        dm = s.scalars(select(Action).where(Action.kind == "dm")).one()
    assert dm.status == "pending"
    assert "send_message" not in writes()  # nothing sent until Sumit taps Send

    reply, _ = await khushi.on_action_button(dm.id, "send", 42)
    assert reply == "✉️ Sent."
    with session() as s:
        assert s.scalar(select(Contact.state)) == "messaged"


async def test_unconfirmed_dm_is_recorded_as_sent_and_never_retried(fakes, monkeypatch):
    live(monkeypatch)
    FakeTools.responses["send_message"] = {"sent": False, "status": "send_unconfirmed", "retry_safe": False}
    fakes({khushi.Draft: khushi.Draft(text="Hi Priya, is the role still open?")})
    actions.upsert_contact(PRIYA, "Priya Pathak")
    action_id = await khushi._queue_dm(PRIYA, "Priya Pathak", "Hi Priya, is the role still open?", "", "")
    reply, _ = await khushi.on_action_button(action_id, "send", 42)
    assert "won't resend" in reply
    with session() as s:
        assert s.get(Action, action_id).status == "published"
    assert writes().count("send_message") == 1


async def test_only_sumit_can_send(fakes, monkeypatch):
    live(monkeypatch)
    fakes({})
    action_id = await khushi._queue_dm(PRIYA, "Priya Pathak", "Hi", "", "")
    reply, _ = await khushi.on_action_button(action_id, "send", 7)
    assert "not an approver" in reply
    assert writes() == []


async def test_inbox_reply_is_drafted_for_approval_not_sent(fakes, monkeypatch):
    live(monkeypatch)
    actions.upsert_contact(PRIYA, "Priya Pathak", "Emancipation Edutech")
    threads = {"n": 0}

    def inbox_read(_prompt):
        threads["n"] += 1
        preview = "You: Hi Priya" if threads["n"] == 1 else "Yes it's open, can you share your CV?"
        return khushi.InboxRead(threads=[khushi.Thread(name="Priya Pathak", from_me=threads["n"] == 1,
                                                       preview=preview)])

    fakes({khushi.InboxRead: inbox_read, khushi.Draft: khushi.Draft(text="Thanks Priya! I'll share my CV shortly.")})
    FakeTools.responses["get_inbox"] = search_result("inbox")
    await khushi.inbox()   # first look: just remembers the threads
    await khushi.inbox()   # Priya replied
    with session() as s:
        dm = s.scalars(select(Action).where(Action.kind == "dm")).one()
        assert s.scalar(select(Contact.state)) == "replied"
    assert dm.status == "pending" and "CV" in dm.body
    assert writes() == []


async def test_seed_history_blocks_repeat_comments(fakes, monkeypatch):
    from app.seed import seed

    live(monkeypatch)
    seed()
    seed()  # idempotent
    fakes({khushi.LeadBatch: khushi.LeadBatch(leads=[lead()])})
    await khushi.scan()
    assert writes() == []  # Priya's post was already commented on by hand
    with session() as s:
        assert len(s.scalars(select(Contact)).all()) == 14
    assert khushi._comments_today() == 0  # history doesn't eat today's cap


async def test_report_runs_on_an_empty_day(fakes):
    fakes({})
    await khushi.report()
