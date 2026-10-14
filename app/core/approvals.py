"""Approval state machine. Code that acts on LinkedIn MUST call `assert_publishable` and nothing else decides.

draft -> pending -> approved -> published
                 -> edit_requested -> pending (redrafted)
                 -> rejected
approved -> failed -> approved (retry)
draft -> approved                             (comments only, when AUTO_COMMENT is on: Sumit's 14 Oct decision)
"""

from datetime import UTC, datetime

from app.config import get_settings
from app.core.db import Action

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending", "approved", "rejected"},
    "pending": {"approved", "rejected", "edit_requested"},
    "edit_requested": {"pending", "rejected"},
    "approved": {"published", "failed", "rejected"},
    "failed": {"approved", "rejected"},
    "published": set(),
    "rejected": set(),
}

# Connection requests and DMs go to a real person in Sumit's name: always his tap.
NEEDS_HUMAN = {"connect", "dm"}


class ApprovalError(RuntimeError):
    pass


def is_approver(user_id: int | None) -> bool:
    return user_id is not None and user_id in get_settings().approvers


def _status(action: Action) -> str:
    # a row that isn't flushed yet has no column defaults applied: it is a draft
    return action.status or "draft"


def transition(action: Action, new: str, by: int | None = None) -> None:
    if new not in TRANSITIONS.get(_status(action), set()):
        raise ApprovalError(f"{action.id}: cannot go {_status(action)} -> {new}")
    if new == "approved" and _status(action) == "draft":
        auto_approve(action)
        return
    if new in {"approved", "rejected", "edit_requested"} and not is_approver(by):
        raise ApprovalError(f"{action.id}: user {by} is not an approver")
    if new == "approved":
        action.approved_by = by
        action.approved_at = datetime.now(UTC).replace(tzinfo=None)
    action.status = new


def auto_approve(action: Action) -> None:
    """Let Khushi approve her own comment. Never a connection request or a DM."""
    if action.kind in NEEDS_HUMAN:
        raise ApprovalError(f"{action.id}: a {action.kind} always needs Sumit's Approve")
    if action.kind != "comment" or not get_settings().auto_comment:
        raise ApprovalError(f"{action.id}: auto-approve is only for comments with AUTO_COMMENT on")
    if _status(action) != "draft":
        raise ApprovalError(f"{action.id}: cannot auto-approve from '{action.status}'")
    action.approved_by = None
    action.approved_at = datetime.now(UTC).replace(tzinfo=None)
    action.status = "approved"


def assert_publishable(action: Action) -> None:
    """The single gate in front of every LinkedIn write."""
    if action.status != "approved":
        raise ApprovalError(f"{action.id} is '{action.status}', not approved")
    if action.approved_by is None:
        if action.kind in NEEDS_HUMAN or action.kind != "comment" or not get_settings().auto_comment:
            raise ApprovalError(f"{action.id}: {action.kind} was not approved by Sumit")
        return
    if not is_approver(action.approved_by):
        raise ApprovalError(f"{action.id} approved by non-approver {action.approved_by}")
