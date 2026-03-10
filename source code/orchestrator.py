"""
Orchestrator — the main turn loop that coordinates every subsystem:
player input → context build → LLM call → validate → rules execute → commit → output.
Implements the two-phase commit (Propose → Validate → Commit) protocol.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from canon_store import CanonStore
from context_builder import ContextBuilder
from event_log import EventLog
from llm_adapter import LLMAdapter
from models import (
    AddFactProposal,
    AddSecretProposal,
    CausedBy,
    CreateEntityProposal,
    Entity,
    EntityType,
    EpistemicStatus,
    Fact,
    GameEvent,
    LLMResponse,
    RequestCheckProposal,
    RequestRollProposal,
    RevealCondition,
    Secret,
    SessionContract,
    UpdateEntityProposal,
    Visibility,
)
from narrative_patcher import NarrativePatcher
from rules_engine import RulesEngine
from validator import Validator

logger = logging.getLogger(__name__)


class TurnResult:
    """Collects everything that happened during one turn for the UI to display."""

    def __init__(self) -> None:
        self.narrative: str = ""
        self.system_messages: list[str] = []
        self.events: list[GameEvent] = []
        self.validation_errors: list[str] = []
        self.validation_warnings: list[str] = []
        self.patch_report: Any = None
        self.context_bundle: Any = None
        self.raw_llm_response: LLMResponse | None = None
        self.state_diff: dict[str, Any] = {}


class Orchestrator:
    SNAPSHOT_INTERVAL = 10

    def __init__(
        self,
        db_path: str = "data/rpg.db",
        session_contract: SessionContract | None = None,
        llm_adapter: LLMAdapter | None = None,
    ):
        self.store = CanonStore(db_path)
        self.event_log = EventLog(db_path)
        self.rules = RulesEngine()
        self.validator = Validator(self.store)
        self.patcher = NarrativePatcher(self.store)
        self.llm = llm_adapter or LLMAdapter()

        contract = session_contract or SessionContract()
        self.ctx_builder = ContextBuilder(self.store, self.event_log, contract)

        self.current_turn: int = self.event_log.get_latest_turn_id() + 1

    # ── main turn ────────────────────────────────────────────────────────

    def run_turn(self, player_input: str) -> TurnResult:
        result = TurnResult()
        turn_id = self.current_turn

        self._log_player_event(player_input, turn_id)
        self.ctx_builder.add_transcript_entry("player", player_input, turn_id)

        context = self.ctx_builder.build(turn_id, keywords=self._extract_keywords(player_input))
        result.context_bundle = context

        llm_response = self.llm.call(context.to_prompt_sections(), player_input)
        result.raw_llm_response = llm_response

        perm_violations = Validator.check_permissions(llm_response.proposals)
        if perm_violations:
            result.validation_errors.extend(perm_violations)
            for v in perm_violations:
                logger.warning("Permission violation: %s", v)

        patch_report = self.patcher.patch(llm_response.narrative, llm_response.proposals)
        result.patch_report = patch_report
        narrative = patch_report.result

        validated = self.validator.validate_proposals(llm_response.proposals)

        committed_events: list[GameEvent] = []
        entity_id_map: dict[str, str] = {}

        for proposal, vr in validated:
            if not vr.ok:
                result.validation_errors.extend(vr.errors)
                continue
            result.validation_warnings.extend(vr.warnings)

            events = self._execute_proposal(proposal, turn_id, entity_id_map)
            committed_events.extend(events)

        for ev in committed_events:
            self.event_log.append(ev)
        result.events = committed_events

        self._check_secret_reveals(turn_id, committed_events, result)

        result.narrative = narrative
        result.system_messages = self._format_system_messages(committed_events)

        self.ctx_builder.add_transcript_entry("gm", narrative, turn_id)

        if turn_id % self.SNAPSHOT_INTERVAL == 0:
            self.event_log.save_snapshot(turn_id, self.store.export_state())

        self.current_turn += 1
        return result

    # ── proposal execution ───────────────────────────────────────────────

    def _execute_proposal(
        self,
        proposal: Any,
        turn_id: int,
        entity_id_map: dict[str, str],
    ) -> list[GameEvent]:
        events: list[GameEvent] = []

        if isinstance(proposal, CreateEntityProposal):
            entity = Entity(
                type=EntityType(proposal.entity_type),
                display_name=proposal.temp_name,
                lore=proposal.fields.get("lore", {}),
                state=proposal.fields.get("state", {}),
                tags=proposal.fields.get("tags", []),
            )
            self.store.add_entity(entity)
            entity_id_map[proposal.temp_name] = entity.id
            events.append(GameEvent(
                turn_id=turn_id,
                type="EntityCreated",
                payload={"entity_id": entity.id, "entity": entity.model_dump()},
                caused_by=CausedBy.LLM,
            ))

        elif isinstance(proposal, UpdateEntityProposal):
            eid = entity_id_map.get(proposal.entity_id, proposal.entity_id)
            old_entity = self.store.get_entity(eid)
            self.store.update_entity(eid, proposal.updates)
            events.append(GameEvent(
                turn_id=turn_id,
                type="EntityUpdated",
                payload={
                    "entity_id": eid,
                    "updates": proposal.updates,
                    "old_state": old_entity.state if old_entity else {},
                },
                caused_by=CausedBy.LLM,
            ))

        elif isinstance(proposal, AddFactProposal):
            subject_ref = proposal.subject.split(":")[-1] if ":" in proposal.subject else proposal.subject
            entity = self.store.get_entity_by_name(subject_ref) or self.store.get_entity(subject_ref)
            subject_id = entity.id if entity else entity_id_map.get(subject_ref, subject_ref)

            fact = Fact(
                subject_id=subject_id,
                predicate=proposal.predicate,
                object=proposal.object,
                status=EpistemicStatus(proposal.status),
                source=CausedBy.LLM,
                visibility=Visibility(proposal.visibility),
            )
            self.store.add_fact(fact)
            events.append(GameEvent(
                turn_id=turn_id,
                type="FactAdded",
                payload={"fact": fact.model_dump()},
                caused_by=CausedBy.LLM,
                visibility=fact.visibility,
            ))

        elif isinstance(proposal, AddSecretProposal):
            secret = Secret(
                description=proposal.description,
                reveal_conditions=[
                    RevealCondition.model_validate(rc) for rc in proposal.reveal_conditions
                ],
            )
            self.store.add_secret(secret)
            events.append(GameEvent(
                turn_id=turn_id,
                type="SecretCreated",
                payload={"secret_id": secret.secret_id, "description": secret.description},
                caused_by=CausedBy.LLM,
                visibility=Visibility.GM_ONLY,
            ))

        elif isinstance(proposal, RequestCheckProposal):
            player = self.store.get_entity(proposal.actor_id)
            player_state = player.state if player else {}
            modifier = RulesEngine.get_modifier_for_skill(player_state, proposal.skill)
            check_result, check_event = self.rules.resolve_check(
                proposal, modifier, str(uuid4()), turn_id
            )
            events.append(check_event)

        elif isinstance(proposal, RequestRollProposal):
            _, roll_event = self.rules.resolve_roll(proposal, str(uuid4()), turn_id)
            events.append(roll_event)

        return events

    # ── secret reveal checking ───────────────────────────────────────────

    def _check_secret_reveals(
        self,
        turn_id: int,
        new_events: list[GameEvent],
        result: TurnResult,
    ) -> None:
        secrets = self.store.get_unrevealed_secrets()
        for secret in secrets:
            if self._should_reveal(secret, new_events):
                self.store.reveal_secret(secret.secret_id)
                reveal_event = GameEvent(
                    turn_id=turn_id,
                    type="SecretRevealed",
                    payload={
                        "secret_id": secret.secret_id,
                        "description": secret.description,
                    },
                    caused_by=CausedBy.SYSTEM,
                    visibility=Visibility.PUBLIC,
                )
                self.event_log.append(reveal_event)
                result.events.append(reveal_event)
                result.system_messages.append(f"[秘密揭示] {secret.description}")

    def _should_reveal(self, secret: Secret, new_events: list[GameEvent]) -> bool:
        if not secret.reveal_conditions:
            return False
        for cond in secret.reveal_conditions:
            if not self._evaluate_condition(cond, new_events):
                return False
        return True

    def _evaluate_condition(self, cond: RevealCondition, new_events: list[GameEvent]) -> bool:
        if cond.condition_type == "fact_exists":
            subj = cond.parameters.get("subject_id", "")
            pred = cond.parameters.get("predicate", "")
            facts = self.store.get_facts_for_subject(subj)
            return any(f.predicate == pred for f in facts)

        if cond.condition_type == "event_occurred":
            target_type = cond.parameters.get("event_type", "")
            return any(e.type == target_type for e in new_events)

        if cond.condition_type == "check_result":
            skill = cond.parameters.get("skill", "")
            required_success = cond.parameters.get("success", True)
            for e in new_events:
                if e.type == "CheckResolved" and e.payload.get("skill") == skill:
                    if e.payload.get("success") == required_success:
                        return True
            return False

        if cond.condition_type == "quest_state":
            quest_id = cond.parameters.get("quest_id", "")
            required_status = cond.parameters.get("status", "")
            quest = self.store.get_entity(quest_id)
            return quest is not None and quest.state.get("status") == required_status

        return False

    # ── helpers ──────────────────────────────────────────────────────────

    def _log_player_event(self, player_input: str, turn_id: int) -> None:
        event = GameEvent(
            turn_id=turn_id,
            type="PlayerInput",
            payload={"text": player_input},
            caused_by=CausedBy.PLAYER,
        )
        self.event_log.append(event)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        words = text.replace("，", " ").replace("。", " ").replace("、", " ").split()
        return [w.strip() for w in words if len(w.strip()) >= 2][:10]

    @staticmethod
    def _format_system_messages(events: list[GameEvent]) -> list[str]:
        messages: list[str] = []
        for e in events:
            if e.visibility == Visibility.GM_ONLY:
                continue
            if e.type == "CheckResolved":
                p = e.payload
                outcome = "成功" if p.get("success") else "失败"
                messages.append(
                    f"[检定] {p.get('skill', '?')} — "
                    f"d20({p.get('roll')}) + {p.get('modifier')} = {p.get('total')} "
                    f"vs DC {p.get('dc')} → {outcome}"
                )
            elif e.type == "RollResolved":
                p = e.payload
                messages.append(f"[掷骰] {p.get('dice')} → {p.get('rolls')} 合计 {p.get('total')}")
            elif e.type == "EntityCreated":
                name = e.payload.get("entity", {}).get("display_name", "?")
                messages.append(f"[新实体] {name}")
            elif e.type == "SecretRevealed":
                messages.append(f"[秘密揭示] {e.payload.get('description', '?')}")
        return messages

    # ── session bootstrap ────────────────────────────────────────────────

    def bootstrap_session(self, genre: str, style: str, boundaries: list[str] | None = None) -> None:
        contract = SessionContract(genre=genre, style=style, boundaries=boundaries or [])
        self.ctx_builder.session_contract = contract

        event = GameEvent(
            turn_id=0,
            type="SessionStarted",
            payload=contract.model_dump(),
            caused_by=CausedBy.SYSTEM,
        )
        self.event_log.append(event)

    def fork_from_turn(self, turn_id: int, new_db_path: str) -> "Orchestrator":
        """Create a branched game from a given turn."""
        events = self.event_log.fork_from(turn_id)
        new_orch = Orchestrator(db_path=new_db_path, llm_adapter=self.llm)
        for ev in events:
            new_orch.event_log.append(ev)
        snapshot = self.store.export_state()
        new_orch.store.import_state(snapshot)
        new_orch.current_turn = turn_id + 1
        return new_orch
