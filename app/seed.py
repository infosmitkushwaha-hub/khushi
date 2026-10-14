"""History from before Khushi existed (Sept–Oct 2026, done by hand in Claude Code), so she never repeats it.

`python -m app seed` is idempotent: run it once on a fresh database (Railway runs it on every start).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.core.db import Action, Contact, Lead, next_id, session, utcnow

# profile path, name, company, state  (state as of 14 Oct 2026)
CONTACTS = [
    ("/in/priya-pathak-222191354/", "Priya Pathak", "Emancipation Edutech", "messaged"),
    ("/in/neha-singh-0865261b0/", "Neha Singh", "Inspire Global Ventures", "requested"),
    ("/in/amisha-bharti-8ba04b251/", "Amisha Bharti", "Virtualize Solutions", "requested"),
    ("/in/amrita-rani-0132a3201/", "Amrita Rani", "Csharptek", "requested"),
    ("/in/aditi-giri-2967a6345/", "Aditi Giri", "366Pi Technologies", "requested"),
    ("/in/hadscraft-tech-573324402/", "Hadscraft Tech", "Hadscraft Tech", "requested"),
    ("/in/peddle-ace-703069204/", "Peddle Ace", "Peddleace (Kamlesh Goyal)", "messaged"),
    ("/in/prerit-kedia-4391b26/", "Prerit Kedia", "Kedia Pipes", "messaged"),
    ("/in/pk15pi/", "Pradeep Prashanna", "Synfowebs Technology", "messaged"),
    ("/in/dr-asjad-khan/", "Asjad Khan", "Sparrow Softech", "requested"),
    ("/in/riya-pandey-489382315/", "Riya Pandey", "Sparrow Softech", "requested"),
    ("/in/tannu-kamari-07185b416/", "Tannu Kamari", "INKHUB Tattoos", "requested"),
    ("/in/kedianitesh/", "Nitesh Kedia", "INKHUB Tattoos", "messaged"),
    ("/in/ACoAADaf3j4BJdKX_XNMFWZwU8xNSpgYyZzcte8/", "Sujit Goswami", "Grownetic Media", "messaged"),
]

# post urn, role, company, poster  (commented on 27 Sep 2026)
COMMENTED = [
    ("urn:li:share:7506234312124465152", "Meta Ads Assistant Intern", "Inspire Global Ventures", "Neha Singh"),
    ("urn:li:share:7502637854553161728", "Meta Ads Assistant Intern", "Inspire Global Ventures", "Roshani Pathak"),
    ("urn:li:share:7509143174293118976", "Content Creator", "Emancipation Edutech", "Priya Pathak"),
    ("urn:li:share:7507776815235502081", "UX/UI Designer & Developer", "Csharptek", "Amrita Rani"),
    ("urn:li:share:7509576268623949824", "Social Media Marketing Intern", "INKHUB Tattoos", "INKHUB Tattoos"),
    ("urn:li:share:7503318619171360768", "Software Developer (walk-in)", "366Pi Technologies", "Aditi Giri"),
    ("urn:li:activity:7507687659373424640", "Social Media Marketer", "Virtualize Solutions", "Amisha Bharti"),
    ("urn:li:share:7505202494424399873", "Branding & Digital Marketing Intern", "Paragon Group", "Ritesh Gupta"),
    ("urn:li:share:7505270250884509696", "Claude Developer (MCP)", "Hadscraft Tech", "Hadscraft Tech"),
    ("urn:li:share:7509488926852550657", "n8n + AI automation freelancer", "Peddleace", "Peddle Ace"),
    ("urn:li:share:7508085563510628352", "Freelance AI developer (WhatsApp)", "Kedia Pipes", "Prerit Kedia"),
    ("urn:li:share:7508162038305705984", "Shopify Developers & Trainers", "Synfowebs Technology", "Pradeep Prashanna"),
    ("urn:li:share:7499167370683322368", "Software developers (Claude Code + MCP)", "Spirax Group", "David Vila"),
]

HAND_DONE_ON = datetime(2026, 9, 27, 12, 0)  # dated in the past so they don't use up today's comment cap

APPLIED = [
    ("job:4467391226", "Paid Media Executive", "Inspire Global Ventures"),
]


def seed() -> str:
    added = {"contacts": 0, "leads": 0}
    with session() as s:
        for path, name, company, state in CONTACTS:
            if not s.scalar(select(Contact.id).where(Contact.profile_url == path)):
                s.add(Contact(profile_url=path, name=name, company=company, state=state,
                              last_sent_at=utcnow() if state == "messaged" else None,
                              followed_up=1 if state == "messaged" else 0))
                added["contacts"] += 1
        for urn, role, company, poster in COMMENTED:
            if s.scalar(select(Lead.id).where(Lead.urn == urn)):
                continue
            lead = Lead(id=next_id(s, "L"), kind="post", urn=urn, role=role, company=company, poster_name=poster,
                        score="HOT", status="commented", why="contacted by hand before Khushi",
                        created_at=HAND_DONE_ON)
            s.add(lead)
            s.add(Action(id=next_id(s, "A"), kind="comment", lead_id=lead.id, target=urn, status="published",
                         body="(posted by hand on 27 Sep 2026)", published_at=HAND_DONE_ON,
                         created_at=HAND_DONE_ON, updated_at=HAND_DONE_ON))
            added["leads"] += 1
        for urn, role, company in APPLIED:
            if not s.scalar(select(Lead.id).where(Lead.urn == urn)):
                s.add(Lead(id=next_id(s, "L"), kind="job", urn=urn, role=role, company=company, score="HOT",
                           status="applied", why="applied by hand before Khushi", created_at=HAND_DONE_ON))
                added["leads"] += 1
    return f"seeded {added['contacts']} contacts and {added['leads']} leads (existing ones kept)"
