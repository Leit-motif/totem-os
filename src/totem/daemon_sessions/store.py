from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(text: str) -> Any:
    return json.loads(text) if text else None


@dataclass(frozen=True)
class DaemonSession:
    session_id: str
    created_at: str
    updated_at: str
    topic_tags: list[str]
    last_n_queries: list[dict[str, str]]
    last_n_selected_sources: list[dict[str, Any]]
    retrieval_budget_snapshot: dict[str, Any]

    def to_snapshot_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "topic_tags": list(self.topic_tags),
            "last_n_queries": list(self.last_n_queries),
            "last_n_selected_sources": list(self.last_n_selected_sources),
            "retrieval_budget_snapshot": dict(self.retrieval_budget_snapshot),
        }


class DaemonSessionStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta(
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions(
                  session_id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  topic_tags_json TEXT NOT NULL,
                  last_n_queries_json TEXT NOT NULL,
                  last_n_selected_sources_json TEXT NOT NULL,
                  retrieval_budget_snapshot_json TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def get_current_session_id(self) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'current_session_id'").fetchone()
            return str(row["value"]) if row is not None else None
        finally:
            conn.close()

    def set_current_session_id(self, session_id: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('current_session_id', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (session_id,),
                )
        finally:
            conn.close()

    def create_session(self, *, retrieval_budget_snapshot: dict[str, Any]) -> DaemonSession:
        # Deterministic-ish (no randomness): timestamp + monotonic counter.
        created_at = _iso_now()
        conn = self._connect()
        try:
            with conn:
                n = conn.execute("SELECT COUNT(1) AS n FROM sessions").fetchone()
                seq = int(n["n"]) + 1 if n is not None else 1
                sid = f"s_{created_at.replace(':','').replace('-','')}_{seq}"

                conn.execute(
                    """
                    INSERT INTO sessions(
                      session_id, created_at, updated_at,
                      topic_tags_json, last_n_queries_json, last_n_selected_sources_json,
                      retrieval_budget_snapshot_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        created_at,
                        created_at,
                        _json_dumps([]),
                        _json_dumps([]),
                        _json_dumps([]),
                        _json_dumps(retrieval_budget_snapshot),
                    ),
                )
            self.set_current_session_id(sid)
        finally:
            conn.close()

        s = self.get_session(sid)
        assert s is not None
        return s

    def get_session(self, session_id: str) -> Optional[DaemonSession]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            return DaemonSession(
                session_id=str(row["session_id"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                topic_tags=list(_json_loads(str(row["topic_tags_json"])) or []),
                last_n_queries=list(_json_loads(str(row["last_n_queries_json"])) or []),
                last_n_selected_sources=list(_json_loads(str(row["last_n_selected_sources_json"])) or []),
                retrieval_budget_snapshot=dict(_json_loads(str(row["retrieval_budget_snapshot_json"])) or {}),
            )
        finally:
            conn.close()

    def append_query(
        self,
        *,
        session_id: str,
        query: str,
        ts_utc: Optional[str],
        cap: int,
    ) -> dict[str, Any]:
        ts = ts_utc or _iso_now()
        entry = {"ts_utc": ts, "query": query}
        conn = self._connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT last_n_queries_json FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown session_id: {session_id}")
                items = list(_json_loads(str(row["last_n_queries_json"])) or [])
                items.append(entry)
                if cap > 0:
                    items = items[-cap:]
                conn.execute(
                    "UPDATE sessions SET last_n_queries_json = ?, updated_at = ? WHERE session_id = ?",
                    (_json_dumps(items), ts, session_id),
                )
        finally:
            conn.close()
        return {"op": "append_query", "session_id": session_id, "ts_utc": ts, "query_hash": _stable_text_hash(query)}

    def ensure_session(self, *, session_id: str, retrieval_budget_snapshot: dict[str, Any]) -> DaemonSession:
        """Ensure a session exists, creating it deterministically if missing.

        Deterministic creation here means: no randomness; uses current time + seq like create_session.
        Intended for recovery paths (e.g., pointer refers to missing session).
        """
        s = self.get_session(session_id)
        if s is not None:
            return s
        created = self.create_session(retrieval_budget_snapshot=retrieval_budget_snapshot)
        return created

    def set_selected_sources(
        self,
        *,
        session_id: str,
        selected_sources: list[dict[str, Any]],
        ts_utc: Optional[str],
        cap: int,
    ) -> dict[str, Any]:
        ts = ts_utc or _iso_now()
        items = list(selected_sources)
        if cap > 0:
            items = items[-cap:]
        conn = self._connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown session_id: {session_id}")
                conn.execute(
                    "UPDATE sessions SET last_n_selected_sources_json = ?, updated_at = ? WHERE session_id = ?",
                    (_json_dumps(items), ts, session_id),
                )
        finally:
            conn.close()
        return {
            "op": "set_selected_sources",
            "session_id": session_id,
            "ts_utc": ts,
            "sources_hash": _stable_text_hash(_json_dumps(items)),
        }
