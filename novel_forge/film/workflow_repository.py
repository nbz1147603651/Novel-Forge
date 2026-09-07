"""SQLite repository for graph revisions, executions and durable events."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .node_catalog import default_film_graph
from .prompting import ensure_node_prompt
from .schemas import utc_now_iso
from .workflow_models import FilmGraphDefinition, FilmGraphEvent, FilmGraphRun


class FilmWorkflowRevisionConflict(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"工作流版本冲突：期望 {expected}，当前为 {actual}")


class FilmWorkflowRepository:
    def __init__(self, film_dir: Path, project_id: str) -> None:
        self.film_dir = film_dir
        self.project_id = project_id
        self.db_path = film_dir / "workflow_v2.sqlite3"
        self.film_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS graph_revisions (
                    revision INTEGER PRIMARY KEY,
                    graph_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    graph_revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_updated
                    ON workflow_runs(updated_at DESC);
                """
            )

    def get_graph(self, revision: int | None = None) -> FilmGraphDefinition:
        with self._connect() as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT graph_json FROM graph_revisions ORDER BY revision DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT graph_json FROM graph_revisions WHERE revision = ?",
                    (revision,),
                ).fetchone()
        if row is not None:
            graph = FilmGraphDefinition.model_validate_json(str(row["graph_json"]))
            return graph.model_copy(
                update={"nodes": [ensure_node_prompt(node) for node in graph.nodes]}
            )
        if revision is not None:
            raise ValueError(f"Unknown film graph revision: {revision}")
        graph = default_film_graph(self.project_id)
        self._insert_graph(graph)
        return graph

    def save_graph(
        self,
        graph: FilmGraphDefinition,
        *,
        expected_revision: int,
    ) -> FilmGraphDefinition:
        current = self.get_graph()
        if current.revision != expected_revision:
            raise FilmWorkflowRevisionConflict(expected_revision, current.revision)
        updated = graph.model_copy(
            update={
                "project_id": self.project_id,
                "graph_id": current.graph_id,
                "revision": current.revision + 1,
                "updated_at": utc_now_iso(),
            }
        )
        self._insert_graph(updated)
        return updated

    def bind_source_signatures(self, source_signatures: dict[str, str]) -> FilmGraphDefinition:
        """Create a graph revision only when an upstream source identity changes."""

        current = self.get_graph()
        normalized = {str(key): str(value) for key, value in sorted(source_signatures.items())}
        if current.source_signatures == normalized:
            return current
        return self.save_graph(
            current.model_copy(update={"source_signatures": normalized}),
            expected_revision=current.revision,
        )

    def _insert_graph(self, graph: FilmGraphDefinition) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO graph_revisions(revision, graph_json, created_at) VALUES (?, ?, ?)",
                (graph.revision, graph.model_dump_json(), utc_now_iso()),
            )

    def list_runs(self, *, limit: int = 50) -> list[FilmGraphRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_json FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [FilmGraphRun.model_validate_json(str(row["run_json"])) for row in rows]

    def get_run(self, run_id: str) -> FilmGraphRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return None if row is None else FilmGraphRun.model_validate_json(str(row["run_json"]))

    def save_run(self, run: FilmGraphRun) -> FilmGraphRun:
        updated = run.model_copy(update={"updated_at": utc_now_iso()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_runs(run_id, graph_revision, status, run_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    run_json = excluded.run_json,
                    updated_at = excluded.updated_at
                """,
                (
                    updated.run_id,
                    updated.graph_revision,
                    updated.status.value,
                    updated.model_dump_json(),
                    updated.created_at,
                    updated.updated_at,
                ),
            )
        return updated

    def append_event(self, event: FilmGraphEvent) -> FilmGraphEvent:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO workflow_events(run_id, sequence, event_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event.run_id, event.sequence, event.model_dump_json(), event.created_at),
            )
        return event

    def next_event_sequence(self, run_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS value "
                "FROM workflow_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return int(row["value"]) if row is not None else 1

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[FilmGraphEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_json FROM workflow_events
                WHERE run_id = ? AND sequence > ? ORDER BY sequence ASC
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [FilmGraphEvent.model_validate_json(str(row["event_json"])) for row in rows]
