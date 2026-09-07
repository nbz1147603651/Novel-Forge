"""SQLite engine factory for StoryKernel.

Provides a thin wrapper around SQLAlchemy's ``create_engine`` with
SQLite-specific optimizations (WAL journal mode, foreign key enforcement).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event


def create_sqlite_engine(
    db_path: str | Path = "story_kernel.db",
    *,
    echo: bool = False,
    wal_mode: bool = True,
    **engine_kwargs: Any,
) -> Engine:
    """Create a SQLAlchemy engine for a SQLite database.

    Parameters
    ----------
    db_path:
        SQLite database file path. Pass ``":memory:"`` for an in-memory database.
    echo:
        If ``True``, emit all SQL to stderr (useful for debugging).
    wal_mode:
        Enable SQLite WAL journal mode for file-backed databases.
    **engine_kwargs:
        Extra keyword arguments forwarded to ``create_engine``.

    Returns
    -------
    Engine
        A configured SQLAlchemy ``Engine`` instance.
    """
    raw_path = str(db_path or "").strip()
    if not raw_path:
        raise ValueError("StoryKernel database path must not be empty.")
    database_url = (
        "sqlite:///:memory:"
        if raw_path == ":memory:"
        else f"sqlite:///{Path(raw_path).expanduser()}"
    )

    engine = create_engine(database_url, echo=echo, **engine_kwargs)

    # Enable WAL journal mode and foreign key enforcement for file-based SQLite
    if raw_path != ":memory:":
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(
            dbapi_connection: Any, connection_record: Any
        ) -> None:
            cursor = dbapi_connection.cursor()
            if wal_mode:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


__all__ = ["create_sqlite_engine"]
