import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, Boolean, select, text, func
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings
from app.core.state import RunRecord, RunStatus

import structlog
log = structlog.get_logger()


class Base(DeclarativeBase):
    pass


# ── Core run table ────────────────────────────────────────────────────────────

class DBRunRecord(Base):
    __tablename__ = "qa_runs"
    id                      = Column(String, primary_key=True)
    status                  = Column(String, default="planning")
    classification          = Column(String, nullable=True)
    confidence              = Column(Float, nullable=True)
    cost_usd                = Column(Float, default=0.0)
    input_tokens            = Column(Integer, default=0)
    output_tokens           = Column(Integer, default=0)
    consecutive_failures    = Column(Integer, default=0)
    trigger_branch          = Column(String, default="main")
    human_override          = Column(Boolean, default=False)
    team_id                 = Column(String, default="core-platform")
    suite_selection_method  = Column(String, default="fallback_all")
    report_text             = Column(Text, nullable=True)
    langfuse_trace_url      = Column(Text, nullable=True)
    # E1: Prompt auditing
    triage_prompt           = Column(Text, nullable=True)
    triage_response         = Column(Text, nullable=True)
    triage_prompt_hash      = Column(String(64), nullable=True)
    data_json               = Column(Text, default="{}")
    created_at              = Column(DateTime, default=datetime.utcnow)
    updated_at              = Column(DateTime, default=datetime.utcnow)
    # Rework: five-signal confidence gate (queryable columns — replaces model-generated confidence)
    p_class                 = Column(Float, nullable=True)    # logprob of winning class
    logprob_margin          = Column(Float, nullable=True)    # top1 − top2 logprob
    nli_entailment          = Column(Float, nullable=True)    # DeBERTa entailment score
    fix_grounded            = Column(Boolean, nullable=True)  # proposed_fix.old exists in file
    dom_corroboration       = Column(Float, nullable=True)    # inspector best candidate confidence
    gate_route              = Column(String, nullable=True)   # auto_fix | human_review
    gate_held_checks        = Column(Text, nullable=True)     # JSON: which signals failed
    # Rework: latency breakdown (promoted from data_json for queryability)
    triage_ttft_ms          = Column(Integer, nullable=True)
    triage_total_ms         = Column(Integer, nullable=True)
    nli_latency_ms          = Column(Integer, nullable=True)
    embedding_latency_ms    = Column(Integer, nullable=True)
    # Rework: suite selection scores + DOM snapshot GCS path
    suite_selection_scores  = Column(Text, nullable=True)     # JSON: {suite: cosine_score}
    dom_snapshot_gcs_path   = Column(Text, nullable=True)


# ── System events (circuit breakers, alerts) ──────────────────────────────────

class DBSystemEvent(Base):
    __tablename__ = "system_events"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String)
    severity   = Column(String, default="info")   # info | warning | critical
    run_id     = Column(String, nullable=True)
    team_id    = Column(String, nullable=True)
    message    = Column(Text, default="")
    meta_json  = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Auth: users + teams ───────────────────────────────────────────────────────

class DBTeam(Base):
    __tablename__ = "teams"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name       = Column(String, unique=True)
    slug       = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DBUser(Base):
    __tablename__ = "users"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email           = Column(String, unique=True)
    hashed_password = Column(String)
    full_name       = Column(String, default="")
    role            = Column(String, default="qa_engineer")  # super_admin | qa_manager | qa_engineer | developer | system_agent
    team_id         = Column(String, nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)


# ── Tickets ───────────────────────────────────────────────────────────────────

class DBTicket(Base):
    __tablename__ = "tickets"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key             = Column(String, unique=True)         # BUG-001, ENV-001
    ticket_type     = Column(String, default="bug")       # bug | env
    classification  = Column(String, nullable=True)
    severity        = Column(String, default="medium")
    status          = Column(String, default="open")      # open | in_progress | resolved
    title           = Column(Text, default="")
    body            = Column(Text, default="")
    run_id          = Column(String, nullable=True)
    team_id         = Column(String, nullable=True)
    jira_remote_id  = Column(String, nullable=True)       # real Jira key if pushed
    jira_url        = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow)


# ── Notifications ─────────────────────────────────────────────────────────────

class DBNotification(Base):
    __tablename__ = "notifications"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    channel     = Column(String, default="inapp")         # slack | inapp
    event_type  = Column(String)
    run_id      = Column(String, nullable=True)
    team_id     = Column(String, nullable=True)
    title       = Column(Text, default="")
    message     = Column(Text, default="")
    status      = Column(String, default="sent")          # sent | failed
    payload_json = Column(Text, default="{}")
    created_at  = Column(DateTime, default=datetime.utcnow)


# ── Selector index (foundation for shift-left) ────────────────────────────────

class DBSelectorIndex(Base):
    __tablename__ = "selector_test_index"
    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    test_file      = Column(String)
    selector_kind  = Column(String)      # css | text | role | testid | url
    selector_value = Column(String)
    page_path      = Column(String, default="")
    line_number    = Column(Integer, default=0)


# ── Schema-only placeholder tables (future scope) ─────────────────────────────

class DBFlakinesScore(Base):
    __tablename__ = "flakiness_scores"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    test_id    = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class DBFailurePattern(Base):
    """C3: Human-verified failure patterns — training signal for future RAG (I4, Horizon 2)."""
    __tablename__ = "failure_patterns"
    id                  = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id              = Column(String, nullable=True)
    original_class      = Column(String)          # what the LLM classified
    verified_class      = Column(String)          # what the human confirmed/corrected
    evidence_json       = Column(Text, default="{}") # failures + dom_report snapshot
    triage_prompt_hash  = Column(String(64), nullable=True)
    verified            = Column(Boolean, default=True)  # False = auto-confirmed, True = human-verified
    created_at          = Column(DateTime, default=datetime.utcnow)


class DBConfigOverride(Base):
    """M2: Runtime config overrides — key/value pairs that shadow Settings env vars."""
    __tablename__ = "config_overrides"
    key         = Column(String, primary_key=True)
    value       = Column(Text)
    updated_by  = Column(String, default="system")
    updated_at  = Column(DateTime, default=datetime.utcnow)


class DBSyntheticRun(Base):
    __tablename__ = "synthetic_runs"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status     = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Engine / session ──────────────────────────────────────────────────────────

_engine = None
_session_factory = None


async def init_db() -> None:
    global _engine, _session_factory
    settings = get_settings()
    db_url = settings.get_db_url()

    connect_args = {}
    if not settings.is_postgres():
        connect_args = {"check_same_thread": False}

    _engine = create_async_engine(
        db_url,
        echo=False,
        connect_args=connect_args,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _migrate_schema()
    await _seed_initial_data()


async def _migrate_schema() -> None:
    """Add missing columns to existing tables (safe for repeated runs via IF NOT EXISTS)."""
    settings = get_settings()
    if not settings.is_postgres():
        return  # SQLite: create_all handles everything

    migrations = [
        # qa_runs — new columns added in v2
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS classification     VARCHAR",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS confidence         FLOAT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS cost_usd           FLOAT DEFAULT 0",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS input_tokens       INTEGER DEFAULT 0",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS output_tokens      INTEGER DEFAULT 0",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER DEFAULT 0",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS trigger_branch     VARCHAR DEFAULT 'main'",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS human_override     BOOLEAN DEFAULT FALSE",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS team_id            VARCHAR DEFAULT 'core-platform'",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS suite_selection_method VARCHAR DEFAULT 'fallback_all'",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS report_text        TEXT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS langfuse_trace_url TEXT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS data_json          TEXT DEFAULT '{}'",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMP",
        # system_events — severity column added in v2
        "ALTER TABLE system_events ADD COLUMN IF NOT EXISTS severity   VARCHAR DEFAULT 'info'",
        "ALTER TABLE system_events ADD COLUMN IF NOT EXISTS team_id    VARCHAR",
        "ALTER TABLE system_events ADD COLUMN IF NOT EXISTS meta_json  TEXT DEFAULT '{}'",
        # tickets — jira columns added in v2
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS jira_remote_id   VARCHAR",
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS jira_url         TEXT",
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMP",
        # qa_runs — E1 prompt auditing columns added in v3
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS triage_prompt       TEXT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS triage_response     TEXT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS triage_prompt_hash  VARCHAR(64)",
        # failure_patterns — C3 full schema migration
        "ALTER TABLE failure_patterns ADD COLUMN IF NOT EXISTS run_id             VARCHAR",
        "ALTER TABLE failure_patterns ADD COLUMN IF NOT EXISTS original_class     VARCHAR",
        "ALTER TABLE failure_patterns ADD COLUMN IF NOT EXISTS verified_class     VARCHAR",
        "ALTER TABLE failure_patterns ADD COLUMN IF NOT EXISTS evidence_json      TEXT DEFAULT '{}'",
        "ALTER TABLE failure_patterns ADD COLUMN IF NOT EXISTS triage_prompt_hash VARCHAR(64)",
        "ALTER TABLE failure_patterns ADD COLUMN IF NOT EXISTS verified           BOOLEAN DEFAULT TRUE",
        # config_overrides — M2 new table (create_all handles it; migration here is idempotent)
        """CREATE TABLE IF NOT EXISTS config_overrides (
            key        VARCHAR PRIMARY KEY,
            value      TEXT,
            updated_by VARCHAR DEFAULT 'system',
            updated_at TIMESTAMP DEFAULT NOW()
        )""",
        # Rework v4: five-signal confidence gate columns
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS p_class               FLOAT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS logprob_margin        FLOAT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS nli_entailment        FLOAT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS fix_grounded          BOOLEAN",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS dom_corroboration     FLOAT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS gate_route            VARCHAR",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS gate_held_checks      TEXT",
        # Rework v4: latency breakdown columns
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS triage_ttft_ms       INTEGER",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS triage_total_ms      INTEGER",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS nli_latency_ms       INTEGER",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS embedding_latency_ms INTEGER",
        # Rework v4: suite scores + GCS snapshot
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS suite_selection_scores TEXT",
        "ALTER TABLE qa_runs ADD COLUMN IF NOT EXISTS dom_snapshot_gcs_path  TEXT",
    ]

    async with _engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception as exc:
                # Log but don't abort — column may already exist under old name
                log.warning("db.migrate.skip", sql=sql[:60], error=str(exc)[:80])

    log.info("db.migrate.done")


# ── Seed data ─────────────────────────────────────────────────────────────────

SEED_TEAMS = [
    {"id": "core-platform", "name": "Core Platform", "slug": "core-platform"},
    {"id": "growth", "name": "Growth", "slug": "growth"},
]

SEED_USERS = [
    {"email": "admin@rait.ai",   "full_name": "Super Admin",    "role": "super_admin",  "team_id": "core-platform", "password": "admin123"},
    {"email": "manager@rait.ai", "full_name": "QA Manager",     "role": "qa_manager",   "team_id": "core-platform", "password": "manager123"},
    {"email": "qa@rait.ai",      "full_name": "QA Engineer",    "role": "qa_engineer",  "team_id": "core-platform", "password": "qa123"},
    {"email": "dev@rait.ai",     "full_name": "Developer",      "role": "developer",    "team_id": "growth",        "password": "dev123"},
    {"email": "system@rait.ai",  "full_name": "System Agent",   "role": "system_agent", "team_id": "core-platform", "password": "system-agent-no-login"},
]


async def _seed_initial_data() -> None:
    try:
        from passlib.context import CryptContext
        pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    except ImportError:
        log.warning("db.seed.passlib_missing — users not seeded")
        return

    async with _session_factory() as s:
        for t in SEED_TEAMS:
            existing = await s.get(DBTeam, t["id"])
            if not existing:
                s.add(DBTeam(**t))

        for u in SEED_USERS:
            result = await s.execute(select(DBUser).where(DBUser.email == u["email"]))
            if result.scalar_one_or_none() is None:
                s.add(DBUser(
                    id=str(uuid.uuid4()),
                    email=u["email"],
                    full_name=u["full_name"],
                    role=u["role"],
                    team_id=u["team_id"],
                    hashed_password=pwd.hash(u["password"]),
                ))
        await s.commit()
        log.info("db.seed.done")


# ── Run CRUD ──────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.utcnow()


async def create_run(run: RunRecord) -> None:
    async with _session_factory() as s:
        d = run.to_dict()
        rec = DBRunRecord(
            id=run.id,
            status=run.status.value if isinstance(run.status, RunStatus) else run.status,
            classification=run.triage.classification or None,
            confidence=run.triage.confidence or None,
            cost_usd=run.cost_usd,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            consecutive_failures=run.consecutive_failures,
            trigger_branch=run.trigger_branch,
            human_override=run.human_override,
            team_id=getattr(run, "team_id", "core-platform"),
            suite_selection_method=getattr(run, "suite_selection_method", "fallback_all"),
            report_text=getattr(run, "report_text", None),
            langfuse_trace_url=getattr(run, "langfuse_trace_url", None),
            data_json=json.dumps(d),
        )
        s.add(rec)
        await s.commit()


async def update_run(run: RunRecord) -> None:
    async with _session_factory() as s:
        rec = await s.get(DBRunRecord, run.id)
        if rec:
            rec.status = run.status.value if isinstance(run.status, RunStatus) else run.status
            rec.classification = run.triage.classification or None
            rec.confidence = run.triage.confidence or None
            rec.cost_usd = run.cost_usd
            rec.input_tokens = run.input_tokens
            rec.output_tokens = run.output_tokens
            rec.consecutive_failures = run.consecutive_failures
            rec.human_override = run.human_override
            rec.team_id = getattr(run, "team_id", "core-platform")
            rec.suite_selection_method = getattr(run, "suite_selection_method", "fallback_all")
            rec.report_text = getattr(run, "report_text", None)
            rec.langfuse_trace_url = getattr(run, "langfuse_trace_url", None)
            rec.data_json = json.dumps(run.to_dict())
            rec.updated_at = _utcnow()
            await s.commit()


async def store_suite_selection_scores(run_id: str, scores: dict[str, float]) -> None:
    """Store embedding cosine scores for the given run."""
    async with _session_factory() as s:
        rec = await s.get(DBRunRecord, run_id)
        if rec:
            rec.suite_selection_scores = json.dumps(scores)
            rec.updated_at = _utcnow()
            await s.commit()


async def store_gate_signals(
    run_id: str,
    p_class: float,
    logprob_margin: float,
    fix_grounded: "bool | None",
    dom_corroboration: float,
    gate_route: str,
    gate_held_checks: list[str],
) -> None:
    async with _session_factory() as s:
        rec = await s.get(DBRunRecord, run_id)
        if rec:
            rec.p_class = p_class
            rec.logprob_margin = logprob_margin
            rec.fix_grounded = fix_grounded
            rec.dom_corroboration = dom_corroboration
            rec.gate_route = gate_route
            rec.gate_held_checks = json.dumps(gate_held_checks)
            rec.updated_at = _utcnow()
            await s.commit()


async def get_run(run_id: str) -> Optional[RunRecord]:
    async with _session_factory() as s:
        rec = await s.get(DBRunRecord, run_id)
        if not rec:
            return None
        return RunRecord.from_dict(json.loads(rec.data_json))


async def list_runs(limit: int = 20, team_id: Optional[str] = None) -> list[RunRecord]:
    async with _session_factory() as s:
        if team_id:
            result = await s.execute(
                text("SELECT data_json FROM qa_runs WHERE team_id = :tid ORDER BY created_at DESC LIMIT :limit"),
                {"tid": team_id, "limit": limit},
            )
        else:
            result = await s.execute(
                text("SELECT data_json FROM qa_runs ORDER BY created_at DESC LIMIT :limit"),
                {"limit": limit},
            )
        return [RunRecord.from_dict(json.loads(r[0])) for r in result.fetchall()]


# ── Ticket CRUD ───────────────────────────────────────────────────────────────

async def _next_ticket_key(ticket_type: str) -> str:
    """Generate BUG-001 / ENV-001 sequentially."""
    prefix = "BUG" if ticket_type == "bug" else "ENV"
    async with _session_factory() as s:
        result = await s.execute(
            text("SELECT COUNT(*) FROM tickets WHERE ticket_type = :t"),
            {"t": ticket_type},
        )
        count = (result.scalar() or 0) + 1
    return f"{prefix}-{count:03d}"


async def create_ticket(
    run_id: str,
    ticket_type: str,
    classification: str,
    severity: str,
    title: str,
    body: str,
    team_id: str = "core-platform",
) -> dict:
    key = await _next_ticket_key(ticket_type)
    ticket = DBTicket(
        id=str(uuid.uuid4()),
        key=key,
        ticket_type=ticket_type,
        classification=classification,
        severity=severity,
        title=title,
        body=body,
        run_id=run_id,
        team_id=team_id,
    )
    async with _session_factory() as s:
        s.add(ticket)
        await s.commit()
        await s.refresh(ticket)
    return _ticket_to_dict(ticket)


async def update_ticket_jira(ticket_id: str, jira_remote_id: str, jira_url: str) -> None:
    async with _session_factory() as s:
        t = await s.get(DBTicket, ticket_id)
        if t:
            t.jira_remote_id = jira_remote_id
            t.jira_url = jira_url
            t.updated_at = _utcnow()
            await s.commit()


async def list_tickets(team_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    async with _session_factory() as s:
        if team_id:
            result = await s.execute(
                text("SELECT * FROM tickets WHERE team_id = :tid ORDER BY created_at DESC LIMIT :limit"),
                {"tid": team_id, "limit": limit},
            )
        else:
            result = await s.execute(
                text("SELECT * FROM tickets ORDER BY created_at DESC LIMIT :limit"),
                {"limit": limit},
            )
        return [dict(r._mapping) for r in result.fetchall()]


async def get_ticket(ticket_id: str) -> Optional[dict]:
    async with _session_factory() as s:
        t = await s.get(DBTicket, ticket_id)
        if not t:
            return None
        return _ticket_to_dict(t)


def _ticket_to_dict(t: DBTicket) -> dict:
    return {
        "id": t.id,
        "key": t.key,
        "ticket_type": t.ticket_type,
        "classification": t.classification,
        "severity": t.severity,
        "status": t.status,
        "title": t.title,
        "body": t.body,
        "run_id": t.run_id,
        "team_id": t.team_id,
        "jira_remote_id": t.jira_remote_id,
        "jira_url": t.jira_url,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ── Notification CRUD ─────────────────────────────────────────────────────────

async def create_notification(
    channel: str,
    event_type: str,
    title: str,
    message: str,
    run_id: Optional[str] = None,
    team_id: Optional[str] = None,
    status: str = "sent",
    payload: Optional[dict] = None,
) -> dict:
    n = DBNotification(
        id=str(uuid.uuid4()),
        channel=channel,
        event_type=event_type,
        run_id=run_id,
        team_id=team_id,
        title=title,
        message=message,
        status=status,
        payload_json=json.dumps(payload or {}),
    )
    async with _session_factory() as s:
        s.add(n)
        await s.commit()
    return {
        "id": n.id,
        "channel": n.channel,
        "event_type": n.event_type,
        "title": n.title,
        "message": n.message,
        "status": n.status,
        "run_id": n.run_id,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


async def list_notifications(team_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    async with _session_factory() as s:
        if team_id:
            result = await s.execute(
                text("SELECT id, channel, event_type, title, message, status, run_id, created_at FROM notifications WHERE team_id = :tid ORDER BY created_at DESC LIMIT :limit"),
                {"tid": team_id, "limit": limit},
            )
        else:
            result = await s.execute(
                text("SELECT id, channel, event_type, title, message, status, run_id, created_at FROM notifications ORDER BY created_at DESC LIMIT :limit"),
                {"limit": limit},
            )
        rows = result.fetchall()
        keys = ["id", "channel", "event_type", "title", "message", "status", "run_id", "created_at"]
        return [dict(zip(keys, r)) for r in rows]


# ── System event CRUD ─────────────────────────────────────────────────────────

async def record_system_event(
    event_type: str,
    message: str,
    severity: str = "info",
    run_id: Optional[str] = None,
    team_id: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    ev = DBSystemEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        severity=severity,
        run_id=run_id,
        team_id=team_id,
        message=message,
        meta_json=json.dumps(meta or {}),
    )
    async with _session_factory() as s:
        s.add(ev)
        await s.commit()


async def list_system_events(limit: int = 50) -> list[dict]:
    async with _session_factory() as s:
        result = await s.execute(
            text("SELECT id, event_type, severity, run_id, team_id, message, created_at FROM system_events ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        )
        rows = result.fetchall()
        keys = ["id", "event_type", "severity", "run_id", "team_id", "message", "created_at"]
        return [dict(zip(keys, r)) for r in rows]


# ── Selector index CRUD ───────────────────────────────────────────────────────

async def upsert_selector_index(entries: list[dict]) -> None:
    async with _session_factory() as s:
        await s.execute(text("DELETE FROM selector_test_index"))
        for e in entries:
            s.add(DBSelectorIndex(
                id=str(uuid.uuid4()),
                test_file=e["test_file"],
                selector_kind=e["selector_kind"],
                selector_value=e["selector_value"],
                page_path=e.get("page_path", ""),
                line_number=e.get("line_number", 0),
            ))
        await s.commit()


async def query_selector_index_by_path(page_path: str) -> list[dict]:
    async with _session_factory() as s:
        result = await s.execute(
            text("SELECT test_file, selector_kind, selector_value, page_path FROM selector_test_index WHERE page_path LIKE :p"),
            {"p": f"%{page_path}%"},
        )
        rows = result.fetchall()
        return [{"test_file": r[0], "selector_kind": r[1], "selector_value": r[2], "page_path": r[3]} for r in rows]


async def query_selector_index_by_value(selector_fragment: str) -> list[dict]:
    async with _session_factory() as s:
        result = await s.execute(
            text("SELECT test_file, selector_kind, selector_value, page_path FROM selector_test_index WHERE selector_value LIKE :v"),
            {"v": f"%{selector_fragment}%"},
        )
        rows = result.fetchall()
        return [{"test_file": r[0], "selector_kind": r[1], "selector_value": r[2], "page_path": r[3]} for r in rows]


# ── User CRUD (auth) ──────────────────────────────────────────────────────────

async def get_user_by_email(email: str) -> Optional[DBUser]:
    async with _session_factory() as s:
        result = await s.execute(select(DBUser).where(DBUser.email == email))
        return result.scalar_one_or_none()


async def get_user_by_id(user_id: str) -> Optional[DBUser]:
    async with _session_factory() as s:
        return await s.get(DBUser, user_id)


# ── Metrics queries ───────────────────────────────────────────────────────────

def _days_ago_sql(days: int, is_pg: bool) -> str:
    if is_pg:
        return f"NOW() - INTERVAL '{days} days'"
    else:
        return f"datetime('now', '-{days} days')"


async def get_metrics_summary(days: int = 7) -> dict:
    settings = get_settings()
    pg = settings.is_postgres()
    cutoff = _days_ago_sql(days, pg)
    async with _session_factory() as s:
        result = await s.execute(text(f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as success,
                AVG(cost_usd) as avg_cost,
                SUM(cost_usd) as total_cost,
                AVG(confidence) as avg_confidence
            FROM qa_runs
            WHERE created_at > {cutoff}
        """))
        row = result.fetchone()
        if not row:
            return {}
        total = row[0] or 0
        success = row[1] or 0
        return {
            "total_runs": total,
            "success_runs": success,
            "success_rate": round(success / max(total, 1), 3),
            "avg_cost_usd": round(row[2] or 0, 4),
            "total_cost_usd": round(row[3] or 0, 4),
            "avg_confidence": round(row[4] or 0, 3),
        }


async def get_classification_distribution(days: int = 30) -> list[dict]:
    settings = get_settings()
    pg = settings.is_postgres()
    cutoff = _days_ago_sql(days, pg)
    async with _session_factory() as s:
        result = await s.execute(text(f"""
            SELECT classification, COUNT(*) as cnt
            FROM qa_runs
            WHERE classification IS NOT NULL
              AND created_at > {cutoff}
            GROUP BY classification
        """))
        return [{"classification": r[0], "count": r[1]} for r in result.fetchall()]


async def get_override_rate(days: int = 30) -> dict:
    settings = get_settings()
    pg = settings.is_postgres()
    cutoff = _days_ago_sql(days, pg)
    async with _session_factory() as s:
        result = await s.execute(text(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN human_override THEN 1 ELSE 0 END) as overrides
            FROM qa_runs
            WHERE classification IS NOT NULL
              AND created_at > {cutoff}
        """))
        row = result.fetchone()
        total = row[0] or 0
        overrides = row[1] or 0
        return {
            "total": total,
            "overrides": overrides,
            "override_rate": round(overrides / max(total, 1), 3),
        }


async def get_cost_stats(days: int = 30) -> dict:
    settings = get_settings()
    pg = settings.is_postgres()
    cutoff = _days_ago_sql(days, pg)
    async with _session_factory() as s:
        result = await s.execute(text(f"""
            SELECT cost_usd FROM qa_runs
            WHERE cost_usd > 0 AND created_at > {cutoff}
            ORDER BY cost_usd
        """))
        costs = [r[0] for r in result.fetchall()]
    if not costs:
        return {"total": 0, "p50": 0, "p95": 0, "count": 0}
    n = len(costs)
    p50 = costs[n // 2]
    p95 = costs[int(n * 0.95)] if n > 1 else costs[-1]
    return {
        "total": round(sum(costs), 4),
        "p50": round(p50, 4),
        "p95": round(p95, 4),
        "count": n,
        "days": days,
    }


async def get_confidence_stats(days: int = 7) -> dict:
    settings = get_settings()
    pg = settings.is_postgres()
    cutoff_7 = _days_ago_sql(days, pg)
    cutoff_30 = _days_ago_sql(30, pg)
    async with _session_factory() as s:
        r7 = await s.execute(text(f"SELECT AVG(confidence) FROM qa_runs WHERE confidence IS NOT NULL AND created_at > {cutoff_7}"))
        r30 = await s.execute(text(f"SELECT AVG(confidence) FROM qa_runs WHERE confidence IS NOT NULL AND created_at > {cutoff_30}"))
        mean_7d = r7.scalar() or 0.0
        mean_30d = r30.scalar() or 0.0
        dist_q = await s.execute(text(f"""
            SELECT classification, AVG(confidence), COUNT(*)
            FROM qa_runs WHERE confidence IS NOT NULL AND created_at > {cutoff_7}
            GROUP BY classification
        """))
        distribution = [{"classification": r[0], "avg_confidence": round(r[1], 3), "count": r[2]} for r in dist_q.fetchall()]
    shift = round(abs(mean_7d - mean_30d) * 100, 1)
    return {
        "mean_7d": round(mean_7d, 3),
        "mean_30d": round(mean_30d, 3),
        "shift_pp": shift,
        "distribution": distribution,
    }


async def get_circuit_breaker_events(limit: int = 20) -> list[dict]:
    return await list_system_events(limit=limit)


async def get_recent_error_rate(window: int = 10) -> float:
    async with _session_factory() as s:
        result = await s.execute(text("""
            SELECT COUNT(*),
                   SUM(CASE WHEN status = 'failed' AND (classification IS NULL OR classification = '') THEN 1 ELSE 0 END)
            FROM (SELECT status, classification FROM qa_runs ORDER BY created_at DESC LIMIT :w)
        """), {"w": window})
        row = result.fetchone()
        total, failures = row[0] or 0, row[1] or 0
        return failures / max(total, 1)


async def count_consecutive_failures(test_name: str) -> int:
    async with _session_factory() as s:
        result = await s.execute(text("""
            SELECT consecutive_failures FROM qa_runs
            WHERE data_json LIKE :pattern
            ORDER BY created_at DESC LIMIT 1
        """), {"pattern": f"%{test_name}%"})
        row = result.fetchone()
        return row[0] if row else 0


# ── E1: Prompt auditing ───────────────────────────────────────────────────────

async def store_triage_audit(
    run_id: str,
    triage_prompt: str,
    triage_response: str,
    prompt_hash: str,
) -> None:
    """Write prompt/response/hash to qa_runs columns for full LLM auditability."""
    async with _session_factory() as s:
        await s.execute(
            text("""
                UPDATE qa_runs
                SET triage_prompt = :prompt,
                    triage_response = :response,
                    triage_prompt_hash = :hash,
                    updated_at = :now
                WHERE id = :id
            """),
            {
                "prompt": triage_prompt[:50000],   # cap at 50k chars to avoid DB bloat
                "response": triage_response[:5000],
                "hash": prompt_hash,
                "now": _utcnow(),
                "id": run_id,
            },
        )
        await s.commit()


async def get_run_triage_prompt(run_id: str) -> tuple[str, str, str]:
    """Returns (triage_prompt, triage_response, triage_prompt_hash) for E2 replay."""
    async with _session_factory() as s:
        result = await s.execute(
            text("SELECT triage_prompt, triage_response, triage_prompt_hash FROM qa_runs WHERE id = :id"),
            {"id": run_id},
        )
        row = result.fetchone()
        if not row:
            return "", "", ""
        return row[0] or "", row[1] or "", row[2] or ""


# ── C3: Failure pattern CRUD ──────────────────────────────────────────────────

async def store_failure_pattern(
    run_id: str,
    original_class: str,
    verified_class: str,
    evidence: dict,
    prompt_hash: str = "",
    verified: bool = True,
) -> None:
    """Record a human-verified (or auto-confirmed) triage outcome for future RAG."""
    async with _session_factory() as s:
        s.add(DBFailurePattern(
            id=str(uuid.uuid4()),
            run_id=run_id,
            original_class=original_class,
            verified_class=verified_class,
            evidence_json=json.dumps(evidence),
            triage_prompt_hash=prompt_hash,
            verified=verified,
        ))
        await s.commit()


async def list_failure_patterns(limit: int = 50, verified_only: bool = True) -> list[dict]:
    async with _session_factory() as s:
        if verified_only:
            result = await s.execute(
                text("SELECT id, run_id, original_class, verified_class, verified, created_at FROM failure_patterns WHERE verified = TRUE ORDER BY created_at DESC LIMIT :limit"),
                {"limit": limit},
            )
        else:
            result = await s.execute(
                text("SELECT id, run_id, original_class, verified_class, verified, created_at FROM failure_patterns ORDER BY created_at DESC LIMIT :limit"),
                {"limit": limit},
            )
        keys = ["id", "run_id", "original_class", "verified_class", "verified", "created_at"]
        return [dict(zip(keys, r)) for r in result.fetchall()]


# ── M2: Config overrides CRUD ─────────────────────────────────────────────────

async def get_config_overrides() -> dict[str, str]:
    """Return all active config overrides as {key: value}."""
    async with _session_factory() as s:
        result = await s.execute(text("SELECT key, value FROM config_overrides"))
        return {r[0]: r[1] for r in result.fetchall()}


async def set_config_override(key: str, value: str, updated_by: str = "system") -> None:
    settings = get_settings()
    pg = settings.is_postgres()
    async with _session_factory() as s:
        if pg:
            await s.execute(
                text("INSERT INTO config_overrides (key, value, updated_by, updated_at) VALUES (:k, :v, :u, :now) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at"),
                {"k": key, "v": value, "u": updated_by, "now": _utcnow()},
            )
        else:
            # SQLite upsert
            await s.execute(
                text("INSERT OR REPLACE INTO config_overrides (key, value, updated_by, updated_at) VALUES (:k, :v, :u, :now)"),
                {"k": key, "v": value, "u": updated_by, "now": _utcnow()},
            )
        await s.commit()


async def delete_config_override(key: str) -> None:
    async with _session_factory() as s:
        await s.execute(text("DELETE FROM config_overrides WHERE key = :k"), {"k": key})
        await s.commit()


# ── M3: Trends time-series ────────────────────────────────────────────────────

async def get_metrics_trends(days: int = 30, bucket: str = "day") -> list[dict]:
    """
    Returns daily/weekly time-series of run counts and auto-fix vs HITL split.
    bucket: 'day' | 'week'
    """
    settings = get_settings()
    pg = settings.is_postgres()
    cutoff = _days_ago_sql(days, pg)

    if pg:
        trunc_fn = "DATE_TRUNC('day', created_at)" if bucket == "day" else "DATE_TRUNC('week', created_at)"
        sql = f"""
            SELECT
                {trunc_fn} as period,
                COUNT(*) as run_count,
                SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as complete_count,
                SUM(CASE WHEN human_override THEN 1 ELSE 0 END) as hitl_count,
                AVG(confidence) as avg_confidence,
                AVG(cost_usd) as avg_cost,
                SUM(cost_usd) as total_cost
            FROM qa_runs
            WHERE created_at > {cutoff}
            GROUP BY {trunc_fn}
            ORDER BY period ASC
        """
    else:
        # SQLite date truncation
        trunc_fn = "strftime('%Y-%m-%d', created_at)"
        sql = f"""
            SELECT
                {trunc_fn} as period,
                COUNT(*) as run_count,
                SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as complete_count,
                SUM(CASE WHEN human_override THEN 1 ELSE 0 END) as hitl_count,
                AVG(confidence) as avg_confidence,
                AVG(cost_usd) as avg_cost,
                SUM(cost_usd) as total_cost
            FROM qa_runs
            WHERE created_at > {cutoff}
            GROUP BY {trunc_fn}
            ORDER BY period ASC
        """

    async with _session_factory() as s:
        result = await s.execute(text(sql))
        rows = result.fetchall()

    return [
        {
            "period": str(r[0]),
            "run_count": r[1],
            "complete_count": r[2],
            "hitl_count": r[3],
            "auto_fix_count": (r[2] or 0) - (r[3] or 0),
            "avg_confidence": round(r[4] or 0, 3),
            "avg_cost_usd": round(r[5] or 0, 5),
            "total_cost_usd": round(r[6] or 0, 4),
        }
        for r in rows
    ]


# ── M4: Group-by on summary metrics ──────────────────────────────────────────

async def get_recent_runs_since(cutoff: "datetime", limit: int = 50) -> list[dict]:
    """
    Return lightweight run rows since cutoff for test_history assembly.
    Only fetches columns needed for the history summary — avoids loading data_json.
    """
    async with _session_factory() as s:
        result = await s.execute(
            text("""
                SELECT classification, confidence, human_override, status, created_at,
                       data_json
                FROM qa_runs
                WHERE created_at >= :cutoff
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"cutoff": cutoff, "limit": limit},
        )
        rows = result.fetchall()

    records = []
    for r in rows:
        row = {
            "classification": r[0],
            "confidence": r[1],
            "human_override": bool(r[2]),
            "status": r[3],
            "created_at": r[4],
            "pr_url": None,
        }
        # Extract pr_url from data_json without loading the whole RunRecord
        try:
            d = json.loads(r[5] or "{}")
            row["pr_url"] = d.get("pr_url")
        except Exception:
            pass
        records.append(row)
    return records


async def get_metrics_summary_grouped(days: int = 7, group_by: str = "team_id") -> list[dict]:
    """
    Returns metrics summary broken down by group_by column.
    group_by: 'team_id' | 'classification' | 'suite_selection_method'
    """
    allowed_groups = {"team_id", "classification", "suite_selection_method"}
    if group_by not in allowed_groups:
        raise ValueError(f"group_by must be one of {allowed_groups}")

    settings = get_settings()
    pg = settings.is_postgres()
    cutoff = _days_ago_sql(days, pg)

    sql = f"""
        SELECT
            {group_by} as group_key,
            COUNT(*) as total_runs,
            SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as success_runs,
            AVG(cost_usd) as avg_cost_usd,
            SUM(cost_usd) as total_cost_usd,
            AVG(confidence) as avg_confidence
        FROM qa_runs
        WHERE created_at > {cutoff}
        GROUP BY {group_by}
        ORDER BY total_runs DESC
    """

    async with _session_factory() as s:
        result = await s.execute(text(sql))
        rows = result.fetchall()

    return [
        {
            "group_by": group_by,
            "group_key": r[0],
            "total_runs": r[1],
            "success_runs": r[2],
            "success_rate": round((r[2] or 0) / max(r[1] or 1, 1), 3),
            "avg_cost_usd": round(r[3] or 0, 4),
            "total_cost_usd": round(r[4] or 0, 4),
            "avg_confidence": round(r[5] or 0, 3),
        }
        for r in rows
    ]
