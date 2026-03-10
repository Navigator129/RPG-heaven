"""
Pydantic data models for the Improvised TRPG Agent.
Covers entities, facts, secrets, events, proposals, and the LLM response protocol.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class EpistemicStatus(str, Enum):
    CANON = "canon"
    RUMOR = "rumor"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"


class Visibility(str, Enum):
    PUBLIC = "public"
    GM_ONLY = "gm_only"


class CausedBy(str, Enum):
    PLAYER = "player"
    SYSTEM = "system"
    LLM = "llm"


class EntityType(str, Enum):
    PLAYER = "player"
    NPC = "npc"
    LOCATION = "location"
    ITEM = "item"
    FACTION = "faction"
    QUEST = "quest"


# ── Core Domain Models ───────────────────────────────────────────────────────

class Entity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: EntityType
    display_name: str
    lore: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_id: str
    predicate: str
    object: str
    status: EpistemicStatus = EpistemicStatus.CANON
    source: CausedBy = CausedBy.LLM
    visibility: Visibility = Visibility.PUBLIC
    evidence_event_ids: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)


class RevealCondition(BaseModel):
    """
    condition_type: "fact_exists" | "quest_state" | "event_occurred" | "check_result"
    """
    condition_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class Secret(BaseModel):
    secret_id: str = Field(default_factory=lambda: str(uuid4()))
    description: str
    fact_ids: list[str] = Field(default_factory=list)
    visibility: Visibility = Visibility.GM_ONLY
    status: EpistemicStatus = EpistemicStatus.CANON
    reveal_conditions: list[RevealCondition] = Field(default_factory=list)
    revealed: bool = False


class GameEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    turn_id: int
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    caused_by: CausedBy = CausedBy.SYSTEM
    visibility: Visibility = Visibility.PUBLIC


class SessionContract(BaseModel):
    genre: str = ""
    style: str = ""
    boundaries: list[str] = Field(default_factory=list)


class CheckResult(BaseModel):
    roll: int
    modifier: int
    total: int
    dc: int
    success: bool
    detail: str = ""


# ── Proposal Types ───────────────────────────────────────────────────────────

class CreateEntityProposal(BaseModel):
    type: str = "CreateEntity"
    entity_type: str
    temp_name: str
    fields: dict[str, Any] = Field(default_factory=dict)


class UpdateEntityProposal(BaseModel):
    type: str = "UpdateEntity"
    entity_id: str
    updates: dict[str, Any] = Field(default_factory=dict)


class AddFactProposal(BaseModel):
    type: str = "AddFact"
    subject: str
    predicate: str
    object: str
    status: str = "canon"
    visibility: str = "public"


class AddSecretProposal(BaseModel):
    type: str = "AddSecret"
    description: str
    related_facts: list[dict[str, Any]] = Field(default_factory=list)
    reveal_conditions: list[dict[str, Any]] = Field(default_factory=list)


class RequestCheckProposal(BaseModel):
    type: str = "RequestCheck"
    actor_id: str
    skill: str
    dc: int
    reason: str = ""


class RequestRollProposal(BaseModel):
    type: str = "RequestRoll"
    roll_type: str = "open"
    dice: str = "1d20"
    reason: str = ""


class AdvanceClockProposal(BaseModel):
    type: str = "AdvanceClock"
    clock_id: str
    ticks: int = 1
    reason: str = ""


class RetconRequestProposal(BaseModel):
    type: str = "RetconRequest"
    target_turn_id: int
    description: str
    reason: str = ""


PROPOSAL_TYPE_MAP: dict[str, type[BaseModel]] = {
    "CreateEntity": CreateEntityProposal,
    "UpdateEntity": UpdateEntityProposal,
    "AddFact": AddFactProposal,
    "AddSecret": AddSecretProposal,
    "RequestCheck": RequestCheckProposal,
    "RequestRoll": RequestRollProposal,
    "AdvanceClock": AdvanceClockProposal,
    "RetconRequest": RetconRequestProposal,
}


def parse_proposal(raw: dict[str, Any]) -> BaseModel:
    ptype = raw.get("type", "")
    cls = PROPOSAL_TYPE_MAP.get(ptype)
    if cls is None:
        raise ValueError(f"Unknown proposal type: {ptype}")
    return cls.model_validate(raw)


# ── LLM Response ─────────────────────────────────────────────────────────────

class LLMResponse(BaseModel):
    narrative: str
    proposals: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_raw_json(cls, text: str) -> "LLMResponse":
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return cls.model_validate(json.loads(text))
