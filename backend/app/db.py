import json
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, String, Text, DateTime, text
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings
from app.models import QARun


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "qa_runs"
    id         = Column(String, primary_key=True)
    status     = Column(String, default="planning")
    data_json  = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


_engine = None
_session_factory = None


async def init_db() -> None:
    global _engine, _session_factory
    settings = get_settings()
    _engine = create_async_engine(settings.database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _session() -> AsyncSession:
    return _session_factory()


async def create_run(run: QARun) -> None:
    async with _session_factory() as s:
        rec = RunRecord(
            id=run.id,
            status=run.status,
            data_json=json.dumps(run.to_dict()),
        )
        s.add(rec)
        await s.commit()


async def update_run(run: QARun) -> None:
    async with _session_factory() as s:
        rec = await s.get(RunRecord, run.id)
        if rec:
            rec.status = run.status
            rec.data_json = json.dumps(run.to_dict())
            rec.updated_at = datetime.utcnow()
            await s.commit()


async def get_run(run_id: str) -> Optional[QARun]:
    async with _session_factory() as s:
        rec = await s.get(RunRecord, run_id)
        if not rec:
            return None
        data = json.loads(rec.data_json)
        return QARun(**data)


async def list_runs(limit: int = 20) -> list[QARun]:
    async with _session_factory() as s:
        result = await s.execute(
            text("SELECT data_json FROM qa_runs ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit}
        )
        rows = result.fetchall()
        return [QARun(**json.loads(r[0])) for r in rows]
