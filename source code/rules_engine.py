"""
Rules Engine — dice rolling, skill checks, and resource settlement.
All randomness is reproducible via deterministic seeding.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any

from models import (
    CausedBy,
    CheckResult,
    GameEvent,
    RequestCheckProposal,
    RequestRollProposal,
    Visibility,
)


@dataclass
class RulesEngine:
    """Lightweight d20-based rules engine with reproducible RNG."""

    global_seed: str = "rpg-heaven-v0"
    _rng_cache: dict[str, random.Random] = field(default_factory=dict)

    # ── deterministic RNG ────────────────────────────────────────────────

    def _rng_for(self, event_id: str) -> random.Random:
        if event_id not in self._rng_cache:
            seed_bytes = hashlib.sha256(
                f"{self.global_seed}:{event_id}".encode()
            ).digest()
            seed_int = int.from_bytes(seed_bytes[:8], "big")
            self._rng_cache[event_id] = random.Random(seed_int)
        return self._rng_cache[event_id]

    # ── dice parsing & rolling ───────────────────────────────────────────

    def roll_dice(self, dice_expr: str, event_id: str) -> list[int]:
        """
        Parse expressions like '1d20', '2d6', '3d8+2' and return individual rolls.
        The modifier (if any) is NOT included in the returned list.
        """
        match = re.match(r"(\d+)d(\d+)", dice_expr)
        if not match:
            raise ValueError(f"Invalid dice expression: {dice_expr}")
        count, sides = int(match.group(1)), int(match.group(2))
        rng = self._rng_for(event_id)
        return [rng.randint(1, sides) for _ in range(count)]

    @staticmethod
    def parse_modifier(dice_expr: str) -> int:
        match = re.search(r"[+-]\d+$", dice_expr)
        return int(match.group()) if match else 0

    # ── skill check (d20 system) ─────────────────────────────────────────

    def resolve_check(
        self,
        proposal: RequestCheckProposal,
        modifier: int,
        event_id: str,
        turn_id: int,
    ) -> tuple[CheckResult, GameEvent]:
        rolls = self.roll_dice("1d20", event_id)
        roll_value = rolls[0]
        total = roll_value + modifier
        success = total >= proposal.dc

        result = CheckResult(
            roll=roll_value,
            modifier=modifier,
            total=total,
            dc=proposal.dc,
            success=success,
            detail=f"d20({roll_value}) + mod({modifier}) = {total} vs DC {proposal.dc}",
        )

        event = GameEvent(
            event_id=event_id,
            turn_id=turn_id,
            type="CheckResolved",
            payload={
                "actor_id": proposal.actor_id,
                "skill": proposal.skill,
                "dc": proposal.dc,
                "roll": roll_value,
                "modifier": modifier,
                "total": total,
                "success": success,
                "reason": proposal.reason,
            },
            caused_by=CausedBy.SYSTEM,
            visibility=Visibility.PUBLIC,
        )
        return result, event

    # ── generic roll ─────────────────────────────────────────────────────

    def resolve_roll(
        self,
        proposal: RequestRollProposal,
        event_id: str,
        turn_id: int,
    ) -> tuple[list[int], GameEvent]:
        rolls = self.roll_dice(proposal.dice, event_id)
        modifier = self.parse_modifier(proposal.dice)
        total = sum(rolls) + modifier

        vis = Visibility.GM_ONLY if proposal.roll_type == "hidden" else Visibility.PUBLIC
        event = GameEvent(
            event_id=event_id,
            turn_id=turn_id,
            type="RollResolved",
            payload={
                "dice": proposal.dice,
                "rolls": rolls,
                "modifier": modifier,
                "total": total,
                "roll_type": proposal.roll_type,
                "reason": proposal.reason,
            },
            caused_by=CausedBy.SYSTEM,
            visibility=vis,
        )
        return rolls, event

    # ── player stat helpers ──────────────────────────────────────────────

    @staticmethod
    def get_modifier_for_skill(player_state: dict[str, Any], skill: str) -> int:
        skills: dict[str, int] = player_state.get("skills", {})
        if skill in skills:
            return skills[skill]
        attributes: dict[str, int] = player_state.get("attributes", {})
        attr_map = {
            "Perception": "wisdom",
            "Athletics": "strength",
            "Stealth": "dexterity",
            "Persuasion": "charisma",
            "Investigation": "intelligence",
            "Survival": "wisdom",
            "Arcana": "intelligence",
            "Intimidation": "charisma",
        }
        attr = attr_map.get(skill, "")
        base = attributes.get(attr, 10)
        return (base - 10) // 2
