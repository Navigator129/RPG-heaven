"""
Proposal Validator — enforces schema legality, world consistency,
and permission boundaries before any proposal is committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from canon_store import CanonStore
from models import (
    AddFactProposal,
    AddSecretProposal,
    CreateEntityProposal,
    EntityType,
    EpistemicStatus,
    RequestCheckProposal,
    RequestRollProposal,
    RetconRequestProposal,
    UpdateEntityProposal,
    parse_proposal,
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Validator:
    """Validates LLM proposals against the canon store."""

    VALID_ENTITY_TYPES = {t.value for t in EntityType}
    VALID_STATUSES = {s.value for s in EpistemicStatus}

    def __init__(self, store: CanonStore):
        self.store = store

    def validate_proposals(
        self,
        raw_proposals: list[dict[str, Any]],
        pending_entity_names: set[str] | None = None,
    ) -> list[tuple[Any, ValidationResult]]:
        """
        Validate a batch of proposals.  Returns (parsed_proposal, result) pairs.
        *pending_entity_names* tracks names created earlier in the same batch so
        that cross-references within one LLM response are allowed.
        """
        if pending_entity_names is None:
            pending_entity_names = set()

        results: list[tuple[Any, ValidationResult]] = []
        for raw in raw_proposals:
            try:
                proposal = parse_proposal(raw)
            except Exception as exc:
                results.append((raw, ValidationResult(ok=False, errors=[f"Parse error: {exc}"])))
                continue

            result = self._validate_one(proposal, pending_entity_names)
            if isinstance(proposal, CreateEntityProposal) and result.ok:
                pending_entity_names.add(proposal.temp_name)
            results.append((proposal, result))

        return results

    # ── per-type validation ──────────────────────────────────────────────

    def _validate_one(self, proposal: Any, pending_names: set[str]) -> ValidationResult:
        if isinstance(proposal, CreateEntityProposal):
            return self._validate_create_entity(proposal)
        if isinstance(proposal, UpdateEntityProposal):
            return self._validate_update_entity(proposal, pending_names)
        if isinstance(proposal, AddFactProposal):
            return self._validate_add_fact(proposal, pending_names)
        if isinstance(proposal, AddSecretProposal):
            return self._validate_add_secret(proposal)
        if isinstance(proposal, RequestCheckProposal):
            return self._validate_request_check(proposal, pending_names)
        if isinstance(proposal, RequestRollProposal):
            return self._validate_request_roll(proposal)
        if isinstance(proposal, RetconRequestProposal):
            return self._validate_retcon(proposal)
        return ValidationResult(ok=True)

    def _validate_create_entity(self, p: CreateEntityProposal) -> ValidationResult:
        errors: list[str] = []
        if p.entity_type not in self.VALID_ENTITY_TYPES:
            errors.append(f"Invalid entity_type '{p.entity_type}'. Must be one of {self.VALID_ENTITY_TYPES}")
        if not p.temp_name.strip():
            errors.append("temp_name must not be empty")
        existing = self.store.get_entity_by_name(p.temp_name)
        if existing:
            errors.append(f"Entity with name '{p.temp_name}' already exists (id={existing.id})")
        return ValidationResult(ok=len(errors) == 0, errors=errors)

    def _validate_update_entity(self, p: UpdateEntityProposal, pending_names: set[str]) -> ValidationResult:
        errors: list[str] = []
        if not self.store.entity_exists(p.entity_id):
            if p.entity_id not in pending_names:
                errors.append(f"Entity '{p.entity_id}' does not exist")
        if not p.updates:
            errors.append("updates must not be empty")
        return ValidationResult(ok=len(errors) == 0, errors=errors)

    def _validate_add_fact(self, p: AddFactProposal, pending_names: set[str]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if p.status not in self.VALID_STATUSES:
            errors.append(f"Invalid fact status '{p.status}'")

        subject_ref = p.subject.split(":")[-1] if ":" in p.subject else p.subject
        entity = self.store.get_entity_by_name(subject_ref) or self.store.get_entity(subject_ref)
        if entity is None and subject_ref not in pending_names:
            errors.append(f"Subject entity '{p.subject}' does not exist and is not being created in this batch")

        if p.status == "canon" and entity is not None:
            conflicts = self.store.find_conflicting_facts(entity.id, p.predicate)
            if conflicts:
                for cf in conflicts:
                    if cf.object != p.object:
                        warnings.append(
                            f"Potential conflict with existing canon fact {cf.fact_id}: "
                            f"'{cf.predicate}' already states '{cf.object}'"
                        )

        return ValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings)

    def _validate_add_secret(self, p: AddSecretProposal) -> ValidationResult:
        errors: list[str] = []
        if not p.description.strip():
            errors.append("Secret description must not be empty")
        if not p.reveal_conditions:
            errors.append("Secret must have at least one reveal condition")
        return ValidationResult(ok=len(errors) == 0, errors=errors)

    def _validate_request_check(self, p: RequestCheckProposal, pending_names: set[str]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        if p.actor_id != "player" and not self.store.entity_exists(p.actor_id):
            if p.actor_id not in pending_names:
                errors.append(f"Actor '{p.actor_id}' does not exist")
        if p.dc < 1 or p.dc > 30:
            warnings.append(f"Unusual DC value: {p.dc}")
        return ValidationResult(ok=len(errors) == 0, errors=errors, warnings=warnings)

    def _validate_request_roll(self, p: RequestRollProposal) -> ValidationResult:
        errors: list[str] = []
        if p.roll_type not in ("open", "hidden"):
            errors.append(f"roll_type must be 'open' or 'hidden', got '{p.roll_type}'")
        return ValidationResult(ok=len(errors) == 0, errors=errors)

    def _validate_retcon(self, p: RetconRequestProposal) -> ValidationResult:
        errors: list[str] = []
        if p.target_turn_id < 0:
            errors.append("target_turn_id must be >= 0")
        if not p.description.strip():
            errors.append("Retcon description must not be empty")
        return ValidationResult(ok=len(errors) == 0, errors=errors)

    # ── permission checks ────────────────────────────────────────────────

    @staticmethod
    def check_permissions(raw_proposals: list[dict[str, Any]]) -> list[str]:
        """
        LLM is NOT allowed to:
        - Directly set dice results
        - Directly change secret visibility to public
        - Set numeric outcomes in check/roll proposals
        """
        violations: list[str] = []
        for p in raw_proposals:
            ptype = p.get("type", "")
            if ptype in ("RequestCheck", "RequestRoll"):
                if "result" in p or "outcome" in p or "roll_value" in p:
                    violations.append(f"LLM may not pre-determine results for {ptype}")
            if ptype == "AddSecret":
                if p.get("visibility") == "public":
                    violations.append("LLM may not create public secrets — use AddFact instead")
            if ptype == "UpdateEntity":
                updates = p.get("updates", {})
                state = updates.get("state", {})
                if "hp" in state or "level" in state or "xp" in state:
                    violations.append("LLM may not directly modify numeric stats (hp/level/xp)")
        return violations
