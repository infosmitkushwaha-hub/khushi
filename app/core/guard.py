"""Hard-rules checker. Every comment, connection note and DM passes through here before it can go out.

The rules are Sumit's (knowledge/hard_rules.md), enforced in code so a model mistake can't slip through:
no invented degrees or experience, no phone number in public, no email (LinkedIn only), no pay figures the
post didn't state, no invented results, and no links except his portfolio and LinkedIn.
"""

from __future__ import annotations

import re

PORTFOLIO = "sumit-my-portfolio-rho.vercel.app"
TRUE_YEARS = {3}  # "about 3 years of freelance work"

DEGREE = re.compile(
    r"\b(graduate[ds]?|graduation|degree|b\.? ?tech|btech|bca|mca|b\.? ?com|bcom|mba|bba|bachelor'?s?|master'?s?"
    r"|engineering student|cse student)\b",
    re.I,
)
YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
PHONE = re.compile(r"(?:\+?91[\s-]?)?(?<!\d)[6-9]\d{4}[\s-]?\d{5}(?!\d)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PAY = re.compile(r"(₹\s?\d[\d,.]*|\d[\d,.]*\s?(?:lpa|lakh|lakhs|k/month|/month|per month)|\$\s?\d[\d,.]*|ctc)", re.I)
RESULTS = re.compile(r"\b(\d+(?:\.\d+)?x\b|\d+\s?%|fortune 500|\d+\s?(?:crore|cr|million|mn)\b)", re.I)
# Only things that are clearly web addresses: a scheme, "www.", or a common TLD. "Next.js" is not a link.
URL = re.compile(
    r"(?:https?://|www\.)([a-z0-9.-]+\.[a-z]{2,})\S*"
    r"|\b([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|io|app|co|net|org|ai|dev|me|xyz|site))\b(?:/\S*)?",
    re.I,
)
ALLOWED_HOSTS = {PORTFOLIO, "linkedin.com", "lnkd.in"}
HYPE = ["rockstar", "ninja", "guru", "world-class", "best in the world", "guarantee", "100% results"]
MAX_COMMENT_CHARS = 600


def check(text: str, *, public: bool, source_text: str = "") -> list[str]:
    """Human-readable violations (empty = clean).

    public=True for comments (anyone can read them). source_text is the post being answered: a figure the post
    itself states (e.g. its salary) may be echoed back.
    """
    issues: list[str] = []
    src = source_text.lower()
    if m := DEGREE.search(text):
        issues.append(f"degree claim '{m.group(0)}': Sumit is studying 12th Commerce, no degree")
    for m in YEARS.finditer(text):
        if int(m.group(1)) not in TRUE_YEARS:
            issues.append(f"'{m.group(0)}' of experience: the true figure is about 3 years of freelance work")
    if public and (m := PHONE.search(text)):
        issues.append(f"phone number in a public comment: '{m.group(0)}'")
    if m := EMAIL.search(text):
        issues.append(f"email address '{m.group(0)}': outreach is LinkedIn only")
    for m in PAY.finditer(text):
        if m.group(0).lower() not in src:
            issues.append(f"pay figure '{m.group(0)}' the post didn't state")
    for m in RESULTS.finditer(text):
        if m.group(0).lower() not in src:
            issues.append(f"unverified result/number '{m.group(0)}'")
    for m in URL.finditer(text):
        host = (m.group(1) or m.group(2)).lower().removeprefix("www.")
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
            issues.append(f"link '{m.group(0)}': only the portfolio and LinkedIn may be linked")
    t = text.lower()
    issues += [f"hype word '{w}'" for w in HYPE if w in t]
    if public and len(text) > MAX_COMMENT_CHARS:
        issues.append(f"comment is {len(text)} characters (max {MAX_COMMENT_CHARS})")
    return issues


RULES_FOR_PROMPT = (
    "Hard rules (enforced in code, a draft that breaks one is thrown away): Sumit is studying 12th Commerce and "
    "has about 3 years of freelance work; never claim a degree, graduation or any other number of years. Never "
    "invent clients, companies, revenue, percentages, multipliers or results. Never put a phone number in a "
    "public comment. Never mention email: contact is LinkedIn only. Never state a salary or pay figure the post "
    "itself didn't give. The only link allowed is " + PORTFOLIO + ". No hype words. Short, human, direct."
)
