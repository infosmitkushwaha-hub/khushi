"""The database is Khushi's memory: every lead, person and LinkedIn action, so nothing is done twice."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Counter(Base):
    __tablename__ = "counters"
    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class KV(Base):
    """Small key/value state: pauses, tokens-expiry dates, last-seen markers."""

    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Lead(Base):
    """One hiring post or job listing Khushi found, scored against Sumit's profile."""

    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # L-0001
    kind: Mapped[str] = mapped_column(String(10), default="post")  # post | job
    urn: Mapped[str] = mapped_column(String(120), unique=True, index=True)  # urn:li:share:… / job:<id>
    url: Mapped[str] = mapped_column(String(500), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(150), default="")
    work_mode: Mapped[str] = mapped_column(String(20), default="")  # on-site | remote | hybrid | freelance
    posted: Mapped[str] = mapped_column(String(60), default="")  # as LinkedIn shows it: "1d", "Sep 24"
    poster_name: Mapped[str] = mapped_column(String(200), default="")
    poster_url: Mapped[str] = mapped_column(String(500), default="")  # only ever taken from MCP results
    poster_role: Mapped[str] = mapped_column(String(30), default="")  # hr | recruiter | founder | manager | company
    score: Mapped[str] = mapped_column(String(10), default="SKIP")  # HOT | WARM | SKIP
    speed: Mapped[str] = mapped_column(String(10), default="")  # FAST | MEDIUM | SLOW
    why: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[str] = mapped_column(String(500), default="")
    text: Mapped[str] = mapped_column(Text, default="")  # the post/job text as read
    status: Mapped[str] = mapped_column(String(20), default="new")
    # new | commented | connected | messaged | applied | replied | interview | hired | rejected | skipped
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Contact(Base):
    """A person Khushi or Sumit reached on LinkedIn, so nobody is contacted twice."""

    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_url: Mapped[str] = mapped_column(String(500), unique=True, index=True)  # /in/<slug>/ from MCP only
    name: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    lead_id: Mapped[str] = mapped_column(String(12), default="")
    state: Mapped[str] = mapped_column(String(20), default="new")
    # new | requested | connected | messaged | replied | closed
    degree: Mapped[str] = mapped_column(String(5), default="")  # 1st | 2nd | 3rd
    pending_dm: Mapped[str] = mapped_column(Text, default="")  # approved text to send once connected
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    followed_up: Mapped[int] = mapped_column(Integer, default=0)
    last_reply: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Action(Base):
    """Something Khushi wants to do on LinkedIn: a comment, a connection request or a DM.

    Comments may run on their own (AUTO_COMMENT); connect and DM always wait for Sumit's Approve.
    """

    __tablename__ = "actions"
    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # A-0001
    kind: Mapped[str] = mapped_column(String(10))  # comment | connect | dm
    lead_id: Mapped[str] = mapped_column(String(12), default="")
    target: Mapped[str] = mapped_column(String(500), default="")  # post urn (comment) or profile url (connect/dm)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    edit_instruction: Mapped[str] = mapped_column(Text, default="")
    guard_flags: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # Telegram ids exceed int32
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Run(Base):
    """One row per agent run: what it ran, what it found, any errors (brief rule 7)."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String(20), index=True)
    job: Mapped[str] = mapped_column(String(50), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="running")  # running|ok|error
    items: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class ToolCall(Base):
    """Every external call (MCP tool, search API) for daily caps and audit."""

    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), index=True)  # e.g. linkedin_reader.search_posts
    agent: Mapped[str] = mapped_column(String(20), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ok: Mapped[int] = mapped_column(Integer, default=1)


_engine = None
_Session: sessionmaker[Session] | None = None


def database_url() -> str:
    """DATABASE_URL from .env (e.g. Supabase Postgres), else a local SQLite file."""
    s = get_settings()
    if s.database_url:
        url = s.database_url
        # Supabase gives postgres:// or postgresql://; use the psycopg (v3) driver
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(s.data_dir / 'khushi.db').as_posix()}"


def _add_missing_columns(engine, schema: str | None = None) -> None:
    """Tiny forward-only migration: add columns that newer code expects (works on SQLite and Postgres)."""
    insp = inspect(engine)
    prefix = f'"{schema}".' if schema else ""
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name, schema=schema):
                continue
            existing = {c["name"]: str(c["type"]) for c in insp.get_columns(table.name, schema=schema)}
            for col in table.columns:
                if col.name not in existing:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {prefix}{table.name} ADD COLUMN {col.name} {ddl}'))
                elif (engine.dialect.name == "postgresql" and isinstance(col.type, BigInteger)
                      and existing[col.name].upper() == "INTEGER"):
                    # widen int32 → int64 (approved_by holds Telegram user ids like 8658172571)
                    conn.execute(text(f'ALTER TABLE {prefix}{table.name} ALTER COLUMN {col.name} TYPE BIGINT'))


def init_db(url: str | None = None) -> None:
    global _engine, _Session
    url = url or database_url()
    is_pg = url.startswith("postgresql")
    connect_args = {"prepare_threshold": None} if url.startswith("postgresql+psycopg") else {}
    eng = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    # Postgres/Supabase: keep the tables in their own schema (default "khushi"), out of Supabase's public REST API.
    schema = get_settings().db_schema if is_pg else None
    if schema:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError(f"DB_SCHEMA must be lowercase letters/digits/underscore, got {schema!r}")
        with eng.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        eng = eng.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(eng)
    _add_missing_columns(eng, schema)
    _engine = eng
    _Session = sessionmaker(_engine, expire_on_commit=False)


def engine():
    if _engine is None:
        init_db()
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    if _Session is None:
        init_db()
    assert _Session is not None
    with _Session() as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise


def next_id(s: Session, prefix: str) -> str:
    c = s.get(Counter, prefix)
    if c is None:
        c = Counter(prefix=prefix, value=0)
        s.add(c)
    c.value += 1
    s.flush()
    return f"{prefix}-{c.value:04d}"


def kv_get(key: str, default: str = "") -> str:
    with session() as s:
        row = s.get(KV, key)
        return row.value if row else default


def kv_set(key: str, value: str) -> None:
    with session() as s:
        row = s.get(KV, key)
        if row:
            row.value = value
        else:
            s.add(KV(key=key, value=value))


def last_runs(limit: int = 10) -> list[Run]:
    with session() as s:
        return list(s.scalars(select(Run).order_by(Run.id.desc()).limit(limit)))
