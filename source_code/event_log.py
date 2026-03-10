"""
Event Sourcing layer — append-only event log backed by SQLite.
All world-state mutations are recorded as events; the canonical state
can be reconstructed by replaying the log from any snapshot.
Uses a persistent connection with WAL mode for efficiency.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from models import CausedBy, GameEvent, Visibility


class EventLog:
    def __init__(self, db_path: str = "data/rpg.db"):
        self.db_path = db_path
        self._connection: sqlite3.Connection | None = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # ── schema bootstrap ─────────────────────────────────────────────────

    def _init_db(self) -> None:
        c = self.conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id   TEXT PRIMARY KEY,
                turn_id    INTEGER NOT NULL,
                ts         TEXT    NOT NULL,
                type       TEXT    NOT NULL,
                payload    TEXT    NOT NULL DEFAULT '{}',
                caused_by  TEXT    NOT NULL,
                visibility TEXT    NOT NULL DEFAULT 'public'
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_turn ON events(turn_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id     INTEGER NOT NULL,
                ts          TEXT    NOT NULL,
                data        TEXT    NOT NULL
            )
        """)
        c.commit()

    # ── write ────────────────────────────────────────────────────────────

    def append(self, event: GameEvent) -> None:
        self.conn.execute(
            "INSERT INTO events (event_id, turn_id, ts, type, payload, caused_by, visibility) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.turn_id,
                event.ts.isoformat(),
                event.type,
                json.dumps(event.payload, ensure_ascii=False),
                event.caused_by.value,
                event.visibility.value,
            ),
        )
        self.conn.commit()

    # ── read ─────────────────────────────────────────────────────────────

    def get_events(
        self,
        from_turn: int = 0,
        to_turn: int | None = None,
        visibility: Visibility | None = None,
    ) -> list[GameEvent]:
        sql = "SELECT event_id, turn_id, ts, type, payload, caused_by, visibility FROM events WHERE turn_id >= ?"
        params: list[Any] = [from_turn]
        if to_turn is not None:
            sql += " AND turn_id <= ?"
            params.append(to_turn)
        if visibility is not None:
            sql += " AND visibility = ?"
            params.append(visibility.value)
        sql += " ORDER BY turn_id, ts"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_events_by_type(self, event_type: str) -> list[GameEvent]:
        rows = self.conn.execute(
            "SELECT event_id, turn_id, ts, type, payload, caused_by, visibility "
            "FROM events WHERE type = ? ORDER BY turn_id, ts",
            (event_type,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_transcript_events(self, window: int = 20) -> list[tuple[int, str, str]]:
        """
        Returns (turn_id, role, text) for PlayerInput and NarrativeProduced.
        Interleaved by turn_id for context rebuilding on load.
        """
        rows = self.conn.execute(
            "SELECT turn_id, type, payload FROM events "
            "WHERE type IN ('PlayerInput', 'NarrativeProduced') "
            "ORDER BY turn_id DESC, ts DESC LIMIT ?",
            (window * 2,),
        ).fetchall()
        out: list[tuple[int, str, str]] = []
        for r in reversed(rows):
            turn_id, etype, payload_raw = r
            payload = json.loads(payload_raw)
            if etype == "PlayerInput":
                out.append((turn_id, "player", payload.get("text", "")))
            elif etype == "NarrativeProduced":
                out.append((turn_id, "gm", payload.get("narrative", "")))
        return out

    def get_session_contract(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload FROM events WHERE type = 'SessionStarted' ORDER BY turn_id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def get_latest_turn_id(self) -> int:
        row = self.conn.execute("SELECT MAX(turn_id) FROM events").fetchone()
        return row[0] if row and row[0] is not None else 0

    # ── snapshots ────────────────────────────────────────────────────────

    def save_snapshot(self, turn_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO snapshots (turn_id, ts, data) VALUES (?, ?, ?)",
            (
                turn_id,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(data, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def load_latest_snapshot(self) -> tuple[int, dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT turn_id, data FROM snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return row[0], json.loads(row[1])

    # ── replay ───────────────────────────────────────────────────────────

    def replay(self, from_turn: int = 0) -> list[GameEvent]:
        return self.get_events(from_turn=from_turn)

    def fork_from(self, turn_id: int) -> list[GameEvent]:
        return self.get_events(from_turn=0, to_turn=turn_id)

    def delete_events_after(self, turn_id: int) -> int:
        cur = self.conn.execute("DELETE FROM events WHERE turn_id > ?", (turn_id,))
        self.conn.commit()
        return cur.rowcount

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_event(row: tuple) -> GameEvent:
        return GameEvent(
            event_id=row[0],
            turn_id=row[1],
            ts=datetime.fromisoformat(row[2]),
            type=row[3],
            payload=json.loads(row[4]),
            caused_by=CausedBy(row[5]),
            visibility=Visibility(row[6]),
        )
