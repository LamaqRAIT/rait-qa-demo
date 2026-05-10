import json
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, Boolean, text
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings
from app.core.state import RunRecord, RunStatus


class Base(DeclarativeBase):
    pass


class DBRunRecord(Base):
    __tablename__ = "qa_runs"
    id                   = Column(String, primary_key=True)
    status               = Column(String, default="planning")
    classification       = Column(String, nullable=True)
    confidence           = Column(Float, nullable=True)
    cost_usd             = Column(Float, default=0.0)
    input_tokens         = Column(Integer, default=0)
    output_tokens        = Column(Integer, default=0)
    consecutive_failures = Column(Integer, default=0)
    trigger_branch       = Column(String, default="main")
    human_override       = Column(Boolean, default=False)
    data_json            = Column(Text, default="{}")
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow)


class DBSystemEvent(Base):
    __tablename__ = "system_events"
    id         = Column(String, primary_key=True)
    event_type = Column(String)
    run_id     = Column(String, nullable=True)
    message    = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


_engine = None
_session_factory = None


async def init_db() -> None:
    global _engine, _session_factory
    settings = get_settings()
    _engine = create_async_engine(settings.database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_run(run: RunRecord) -> None:
    async with _session_factory() as s:
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
            data_json=json.dumps(run.to_dict()),
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
            rec.data_json = json.dumps(run.to_dict())
            rec.updated_at = datetime.utcnow()
            await s.commit()


async def get_run(run_id: str) -> Optional[RunRecord]:
    async with _session_factory() as s:
        rec = await s.get(DBRunRecord, run_id)
        if not rec:
            return None
        return RunRecord.from_dict(json.loads(rec.data_json))


async def list_runs(limit: int = 20) -> list[RunRecord]:
    async with _session_factory() as s:
        result = await s.execute(
            text("SELECT data_json FROM qa_runs ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        )
        return [RunRecord.from_dict(json.loads(r[0])) for r in result.fetchall()]


async def get_metrics_summary(days: int = 7) -> dict:
    async with _session_factory() as s:
        result = await s.execute(text("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as success,
                AVG(cost_usd) as avg_cost,
                SUM(cost_usd) as total_cost,
                AVG(confidence) as avg_confidence
            FROM qa_runs
            WHERE created_at > NOW() - INTERVAL ':days days'
        """).bindparams(days=days))
        row = result.fetchone()
        if not row:
            return {}
        return {
            "total_runs": row[0] or 0,
            "success_runs": row[1] or 0,
            "success_rate": round((row[1] or 0) / max(row[0] or 1, 1), 3),
            "avg_cost_usd": round(row[2] or 0, 4),
            "total_cost_usd": round(row[3] or 0, 4),
            "avg_confidence": round(row[4] or 0, 3),
        }


async def get_classification_distribution(days: int = 30) -> list[dict]:
    async with _session_factory() as s:
        result = await s.execute(text("""
            SELECT classification, COUNT(*) as cnt
            FROM qa_runs
            WHERE classification IS NOT NULL
              AND created_at > NOW() - INTERVAL ':days days'
            GROUP BY classification
        """).bindparams(days=days))
        return [{"classification": r[0], "count": r[1]} for r in result.fetchall()]


async def get_override_rate(days: int = 30) -> dict:
    async with _session_factory() as s:
        result = await s.execute(text("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN human_override THEN 1 ELSE 0 END) as overrides
            FROM qa_runs
            WHERE classification IS NOT NULL
              AND created_at > NOW() - INTERVAL ':days days'
        """).bindparams(days=days))
        row = result.fetchone()
        total = row[0] or 0
        overrides = row[1] or 0
        return {
            "total": total,
            "overrides": overrides,
            "override_rate": round(overrides / max(total, 1), 3),
        }


async def count_consecutive_failures(test_name: str) -> int:
    async with _session_factory() as s:
        result = await s.execute(text("""
            SELECT consecutive_failures FROM qa_runs
            WHERE data_json LIKE :pattern
            ORDER BY created_at DESC LIMIT 1
        """), {"pattern": f"%{test_name}%"})
        row = result.fetchone()
        return row[0] if row else 0
