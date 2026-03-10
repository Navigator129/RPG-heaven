"""
Canon Store — the single source of truth for entities, facts, and secrets.
All reads/writes go through this layer; the LLM never touches it directly.
Uses a persistent SQLite connection per instance for efficiency.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from models import (
    Entity,
    EntityType,
    EpistemicStatus,
    Fact,
    RevealCondition,
    Secret,
    Visibility,
)


class CanonStore:
    def __init__(self, db_path: str = "data/rpg.db"):
        self.db_path = db_path
        self._connection: sqlite3.Connection | None = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # ── schema bootstrap ─────────────────────────────────────────────────

    def _init_db(self) -> None:
        c = self.conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                display_name TEXT NOT NULL,
                lore         TEXT NOT NULL DEFAULT '{}',
                state        TEXT NOT NULL DEFAULT '{}',
                tags         TEXT NOT NULL DEFAULT '[]'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                fact_id            TEXT PRIMARY KEY,
                subject_id         TEXT NOT NULL,
                predicate          TEXT NOT NULL,
                object             TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'canon',
                source             TEXT NOT NULL DEFAULT 'llm',
                visibility         TEXT NOT NULL DEFAULT 'public',
                evidence_event_ids TEXT NOT NULL DEFAULT '[]',
                conflicts_with     TEXT NOT NULL DEFAULT '[]'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS secrets (
                secret_id         TEXT PRIMARY KEY,
                description       TEXT NOT NULL,
                fact_ids          TEXT NOT NULL DEFAULT '[]',
                visibility        TEXT NOT NULL DEFAULT 'gm_only',
                status            TEXT NOT NULL DEFAULT 'canon',
                reveal_conditions TEXT NOT NULL DEFAULT '[]',
                revealed          INTEGER NOT NULL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(display_name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_facts_subj_pred ON facts(subject_id, predicate)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_secrets_revealed ON secrets(revealed)")
        c.commit()

    # ── Entity CRUD ──────────────────────────────────────────────────────

    def add_entity(self, entity: Entity) -> None:
        self.conn.execute(
            "INSERT INTO entities (id, type, display_name, lore, state, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entity.id,
                entity.type.value,
                entity.display_name,
                json.dumps(entity.lore, ensure_ascii=False),
                json.dumps(entity.state, ensure_ascii=False),
                json.dumps(entity.tags, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT id, type, display_name, lore, state, tags FROM entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def get_entity_by_name(self, name: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT id, type, display_name, lore, state, tags FROM entities WHERE display_name = ?",
            (name,),
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def update_entity(self, entity_id: str, updates: dict[str, Any]) -> bool:
        row = self.conn.execute(
            "SELECT id, type, display_name, lore, state, tags FROM entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            return False
        entity = self._row_to_entity(row)

        sets: list[str] = []
        params: list[Any] = []
        if "display_name" in updates:
            sets.append("display_name = ?")
            params.append(updates["display_name"])
        if "lore" in updates:
            merged = {**entity.lore, **updates["lore"]}
            sets.append("lore = ?")
            params.append(json.dumps(merged, ensure_ascii=False))
        if "state" in updates:
            merged = {**entity.state, **updates["state"]}
            sets.append("state = ?")
            params.append(json.dumps(merged, ensure_ascii=False))
        if "tags" in updates:
            sets.append("tags = ?")
            params.append(json.dumps(updates["tags"], ensure_ascii=False))
        if not sets:
            return True
        params.append(entity_id)
        self.conn.execute(f"UPDATE entities SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()
        return True

    def list_entities(self, entity_type: EntityType | None = None) -> list[Entity]:
        sql = "SELECT id, type, display_name, lore, state, tags FROM entities"
        params: list[Any] = []
        if entity_type is not None:
            sql += " WHERE type = ?"
            params.append(entity_type.value)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_entity(r) for r in rows]

    def list_entities_at_location(self, location_id: str) -> list[Entity]:
        rows = self.conn.execute(
            "SELECT id, type, display_name, lore, state, tags FROM entities "
            "WHERE json_extract(state, '$.location_id') = ?",
            (location_id,),
        ).fetchall()
        return [self._row_to_entity(r) for r in rows]

    def entity_exists(self, entity_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return row is not None

    def search_entities(self, keyword: str) -> list[Entity]:
        pattern = f"%{keyword}%"
        rows = self.conn.execute(
            "SELECT id, type, display_name, lore, state, tags FROM entities "
            "WHERE display_name LIKE ? OR lore LIKE ? OR tags LIKE ?",
            (pattern, pattern, pattern),
        ).fetchall()
        return [self._row_to_entity(r) for r in rows]

    # ── Fact CRUD ────────────────────────────────────────────────────────

    def add_fact(self, fact: Fact) -> None:
        self.conn.execute(
            "INSERT INTO facts (fact_id, subject_id, predicate, object, status, source, "
            "visibility, evidence_event_ids, conflicts_with) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact.fact_id,
                fact.subject_id,
                fact.predicate,
                fact.object,
                fact.status.value,
                fact.source.value,
                fact.visibility.value,
                json.dumps(fact.evidence_event_ids),
                json.dumps(fact.conflicts_with),
            ),
        )
        self.conn.commit()

    def get_fact(self, fact_id: str) -> Fact | None:
        row = self.conn.execute(
            "SELECT fact_id, subject_id, predicate, object, status, source, "
            "visibility, evidence_event_ids, conflicts_with FROM facts WHERE fact_id = ?",
            (fact_id,),
        ).fetchone()
        return self._row_to_fact(row) if row else None

    def get_facts_for_subject(self, subject_id: str) -> list[Fact]:
        rows = self.conn.execute(
            "SELECT fact_id, subject_id, predicate, object, status, source, "
            "visibility, evidence_event_ids, conflicts_with FROM facts WHERE subject_id = ?",
            (subject_id,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_canon_facts(self, visibility: Visibility | None = None) -> list[Fact]:
        sql = (
            "SELECT fact_id, subject_id, predicate, object, status, source, "
            "visibility, evidence_event_ids, conflicts_with FROM facts WHERE status = 'canon'"
        )
        params: list[Any] = []
        if visibility is not None:
            sql += " AND visibility = ?"
            params.append(visibility.value)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def find_conflicting_facts(self, subject_id: str, predicate: str) -> list[Fact]:
        rows = self.conn.execute(
            "SELECT fact_id, subject_id, predicate, object, status, source, "
            "visibility, evidence_event_ids, conflicts_with "
            "FROM facts WHERE subject_id = ? AND predicate = ? AND status = 'canon'",
            (subject_id, predicate),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def search_facts(self, keyword: str, top_k: int = 20) -> list[Fact]:
        pattern = f"%{keyword}%"
        rows = self.conn.execute(
            "SELECT fact_id, subject_id, predicate, object, status, source, "
            "visibility, evidence_event_ids, conflicts_with "
            "FROM facts WHERE subject_id LIKE ? OR predicate LIKE ? OR object LIKE ? "
            "LIMIT ?",
            (pattern, pattern, pattern, top_k),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    # ── Secret CRUD ──────────────────────────────────────────────────────

    def add_secret(self, secret: Secret) -> None:
        self.conn.execute(
            "INSERT INTO secrets (secret_id, description, fact_ids, visibility, status, "
            "reveal_conditions, revealed) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                secret.secret_id,
                secret.description,
                json.dumps(secret.fact_ids),
                secret.visibility.value,
                secret.status.value,
                json.dumps([rc.model_dump() for rc in secret.reveal_conditions]),
                int(secret.revealed),
            ),
        )
        self.conn.commit()

    def get_secret(self, secret_id: str) -> Secret | None:
        row = self.conn.execute(
            "SELECT secret_id, description, fact_ids, visibility, status, "
            "reveal_conditions, revealed FROM secrets WHERE secret_id = ?",
            (secret_id,),
        ).fetchone()
        return self._row_to_secret(row) if row else None

    def get_unrevealed_secrets(self) -> list[Secret]:
        rows = self.conn.execute(
            "SELECT secret_id, description, fact_ids, visibility, status, "
            "reveal_conditions, revealed FROM secrets WHERE revealed = 0",
        ).fetchall()
        return [self._row_to_secret(r) for r in rows]

    def reveal_secret(self, secret_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE secrets SET revealed = 1, visibility = 'public' WHERE secret_id = ?",
            (secret_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ── Snapshot export / import ─────────────────────────────────────────

    def export_state(self) -> dict[str, Any]:
        return {
            "entities": [e.model_dump() for e in self.list_entities()],
            "facts": [f.model_dump() for f in self.get_canon_facts()],
            "secrets": [s.model_dump() for s in self.get_unrevealed_secrets()],
        }

    def import_state(self, data: dict[str, Any]) -> None:
        self.conn.execute("DELETE FROM entities")
        self.conn.execute("DELETE FROM facts")
        self.conn.execute("DELETE FROM secrets")
        self.conn.commit()
        for raw in data.get("entities", []):
            self.add_entity(Entity.model_validate(raw))
        for raw in data.get("facts", []):
            self.add_fact(Fact.model_validate(raw))
        for raw in data.get("secrets", []):
            self.add_secret(Secret.model_validate(raw))

    # ── row mappers ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_entity(row: tuple) -> Entity:
        return Entity(
            id=row[0],
            type=EntityType(row[1]),
            display_name=row[2],
            lore=json.loads(row[3]),
            state=json.loads(row[4]),
            tags=json.loads(row[5]),
        )

    @staticmethod
    def _row_to_fact(row: tuple) -> Fact:
        return Fact(
            fact_id=row[0],
            subject_id=row[1],
            predicate=row[2],
            object=row[3],
            status=EpistemicStatus(row[4]),
            source=row[5],
            visibility=Visibility(row[6]),
            evidence_event_ids=json.loads(row[7]),
            conflicts_with=json.loads(row[8]),
        )

    @staticmethod
    def _row_to_secret(row: tuple) -> Secret:
        raw_conditions = json.loads(row[5])
        return Secret(
            secret_id=row[0],
            description=row[1],
            fact_ids=json.loads(row[2]),
            visibility=Visibility(row[3]),
            status=EpistemicStatus(row[4]),
            reveal_conditions=[RevealCondition.model_validate(rc) for rc in raw_conditions],
            revealed=bool(row[6]),
        )
