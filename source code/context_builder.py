"""
Context Builder — assembles the prompt context bundle sent to the LLM
each turn, pulling from the canon store, event log, and session state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from canon_store import CanonStore
from event_log import EventLog
from models import Entity, Fact, SessionContract, Visibility


@dataclass
class ContextBundle:
    session_contract: dict[str, Any]
    current_scene: dict[str, Any]
    canon_facts: list[dict[str, Any]]
    active_threads: list[dict[str, Any]]
    recent_transcript: list[dict[str, Any]]
    gm_notes: list[dict[str, Any]]

    def to_prompt_sections(self) -> str:
        sections: list[str] = []

        sections.append("## Session Contract")
        sections.append(json.dumps(self.session_contract, ensure_ascii=False, indent=2))

        sections.append("\n## Current Scene")
        sections.append(json.dumps(self.current_scene, ensure_ascii=False, indent=2))

        if self.canon_facts:
            sections.append("\n## Established Canon Facts")
            for f in self.canon_facts:
                sections.append(f"- [{f['status']}] {f['subject_id']}.{f['predicate']} = {f['object']}")

        if self.active_threads:
            sections.append("\n## Active Threads / Unresolved Mysteries")
            for t in self.active_threads:
                sections.append(f"- {t.get('description', json.dumps(t, ensure_ascii=False))}")

        if self.recent_transcript:
            sections.append("\n## Recent Transcript (last turns)")
            for entry in self.recent_transcript:
                role = entry.get("role", "?")
                text = entry.get("text", "")
                sections.append(f"[{role}] {text}")

        if self.gm_notes:
            sections.append("\n## GM Notes (private — do NOT reveal to player)")
            for note in self.gm_notes:
                sections.append(f"- {note.get('description', json.dumps(note, ensure_ascii=False))}")

        return "\n".join(sections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_contract": self.session_contract,
            "current_scene": self.current_scene,
            "canon_facts": self.canon_facts,
            "active_threads": self.active_threads,
            "recent_transcript": self.recent_transcript,
            "gm_notes": self.gm_notes,
        }


class ContextBuilder:
    def __init__(
        self,
        store: CanonStore,
        event_log: EventLog,
        session_contract: SessionContract | None = None,
        transcript_window: int = 10,
        max_facts: int = 30,
    ):
        self.store = store
        self.event_log = event_log
        self.session_contract = session_contract or SessionContract()
        self.transcript_window = transcript_window
        self.max_facts = max_facts
        self._transcript: list[dict[str, Any]] = []

    def add_transcript_entry(self, role: str, text: str, turn_id: int) -> None:
        self._transcript.append({"role": role, "text": text, "turn_id": turn_id})

    # ── main build ───────────────────────────────────────────────────────

    def build(self, current_turn: int, keywords: list[str] | None = None) -> ContextBundle:
        return ContextBundle(
            session_contract=self.session_contract.model_dump(),
            current_scene=self._build_scene(current_turn),
            canon_facts=self._retrieve_facts(keywords),
            active_threads=self._retrieve_threads(current_turn),
            recent_transcript=self._recent_transcript(),
            gm_notes=self._retrieve_gm_notes(),
        )

    # ── scene ────────────────────────────────────────────────────────────

    def _build_scene(self, current_turn: int) -> dict[str, Any]:
        recent_events = self.event_log.get_events(
            from_turn=max(0, current_turn - 3), to_turn=current_turn
        )

        current_location = self._infer_current_location(recent_events)
        entities_present = self._get_entities_at_location(current_location)

        return {
            "turn": current_turn,
            "location": current_location.model_dump() if current_location else None,
            "entities_present": [e.model_dump() for e in entities_present],
            "recent_event_types": [e.type for e in recent_events[-5:]],
        }

    def _infer_current_location(self, recent_events: list) -> Entity | None:
        for event in reversed(recent_events):
            loc_id = event.payload.get("location_id")
            if loc_id:
                entity = self.store.get_entity(loc_id)
                if entity:
                    return entity
        locations = self.store.list_entities()
        for loc in locations:
            if loc.type.value == "location":
                return loc
        return None

    def _get_entities_at_location(self, location: Entity | None) -> list[Entity]:
        if location is None:
            return []
        all_entities = self.store.list_entities()
        present = []
        for e in all_entities:
            if e.state.get("location_id") == location.id:
                present.append(e)
            elif e.id == location.id:
                continue
        return present

    # ── facts ────────────────────────────────────────────────────────────

    def _retrieve_facts(self, keywords: list[str] | None) -> list[dict[str, Any]]:
        if keywords:
            facts: list[Fact] = []
            seen: set[str] = set()
            for kw in keywords:
                for f in self.store.search_facts(kw, top_k=self.max_facts):
                    if f.fact_id not in seen:
                        facts.append(f)
                        seen.add(f.fact_id)
        else:
            facts = self.store.get_canon_facts(visibility=Visibility.PUBLIC)

        return [f.model_dump() for f in facts[: self.max_facts]]

    # ── threads ──────────────────────────────────────────────────────────

    def _retrieve_threads(self, current_turn: int) -> list[dict[str, Any]]:
        threads: list[dict[str, Any]] = []
        quest_entities = self.store.list_entities()
        for e in quest_entities:
            if e.type.value == "quest" and e.state.get("status") != "completed":
                threads.append({
                    "type": "quest",
                    "id": e.id,
                    "description": e.display_name,
                    "state": e.state,
                })
        return threads

    # ── transcript ───────────────────────────────────────────────────────

    def _recent_transcript(self) -> list[dict[str, Any]]:
        return self._transcript[-self.transcript_window :]

    # ── gm notes (secrets visible only to LLM/GM) ───────────────────────

    def _retrieve_gm_notes(self) -> list[dict[str, Any]]:
        secrets = self.store.get_unrevealed_secrets()
        return [
            {
                "secret_id": s.secret_id,
                "description": s.description,
                "reveal_conditions": [rc.model_dump() for rc in s.reveal_conditions],
            }
            for s in secrets[:10]
        ]
