"""Khushi's brain: the markdown files in knowledge/, read fresh on every run so Sumit can edit them any time."""

from __future__ import annotations

from app.config import ROOT

DIR = ROOT / "knowledge"
FILES = ["profile", "projects", "targets", "style", "hard_rules"]


def read(name: str) -> str:
    path = DIR / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def brain(*names: str) -> str:
    """The named files (default: all) as one block for a system prompt."""
    return "\n\n".join(f"<{n}>\n{read(n)}\n</{n}>" for n in (names or FILES))
