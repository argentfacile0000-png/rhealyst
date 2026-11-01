"""Accès base de données (SQLite async par défaut) + micro-migrations automatiques."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .models import KV, Base, Event, utcnow

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_db(url: str) -> None:
    """Crée le moteur, les tables, et ajoute les colonnes manquantes (SQLite)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = create_async_engine(url, future=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        if url.startswith("sqlite"):
            await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(sync_conn: Any) -> None:
    """Migration légère : ajoute les nouvelles colonnes déclarées dans les modèles."""
    insp = sa_inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            col_type = col.type.compile(dialect=sync_conn.dialect)
            default_sql = ""
            default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
            if isinstance(default, bool):
                default_sql = f" DEFAULT {1 if default else 0}"
            elif isinstance(default, (int, float)):
                default_sql = f" DEFAULT {default}"
            elif isinstance(default, str):
                escaped = default.replace("'", "''")
                default_sql = f" DEFAULT '{escaped}'"
            log.info("Migration: ajout de la colonne %s.%s", table.name, col.name)
            sync_conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{default_sql}'))


async def close_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Base de données non initialisée (init_db)")
    async with _sessionmaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


# --- Petites aides -----------------------------------------------------------


async def kv_get(key: str, default: Any = None) -> Any:
    async with session() as s:
        row = await s.get(KV, key)
        return default if row is None else row.value


async def kv_set(key: str, value: Any) -> None:
    async with session() as s:
        row = await s.get(KV, key)
        if row is None:
            s.add(KV(key=key, value=value))
        else:
            row.value = value


async def log_event(text_: str, level: str = "info", channel_id: int | None = None) -> None:
    log.log(logging.ERROR if level == "error" else logging.INFO, "[event] %s", text_)
    try:
        async with session() as s:
            s.add(Event(text=text_[:2000], level=level, channel_id=channel_id, ts=utcnow()))
    except Exception:  # le journal ne doit jamais casser le pipeline
        log.exception("Impossible d'écrire l'événement")


async def recent_events(limit: int = 15) -> list[Event]:
    async with session() as s:
        res = await s.execute(select(Event).order_by(Event.id.desc()).limit(limit))
        return list(res.scalars())
