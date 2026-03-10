"""
Narrative Patcher — detects when the LLM narrative "smuggles" new entities
(NPCs, locations, items) that have no corresponding CreateEntity proposal,
and rewrites those references to vague/anonymous descriptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from canon_store import CanonStore


@dataclass
class PatchReport:
    patched: bool
    original: str
    result: str
    smuggled_names: list[str]


class NarrativePatcher:
    """
    Compares the narrative text against known entities and the current batch
    of CreateEntity proposals.  Any proper-noun-like reference that is neither
    already registered nor being created is flagged as "smuggled" and replaced
    with a generic placeholder.
    """

    REPLACEMENT_MAP = {
        "npc": "一个陌生人",
        "location": "某个地方",
        "item": "某件物品",
        "faction": "某个组织",
        "quest": "一件悬而未决的事",
    }

    DEFAULT_REPLACEMENT = "某个未知事物"

    def __init__(self, store: CanonStore):
        self.store = store

    def patch(
        self,
        narrative: str,
        proposals: list[dict[str, Any]],
    ) -> PatchReport:
        known_names = self._collect_known_names()
        proposed_names = self._collect_proposed_names(proposals)
        allowed_names = known_names | proposed_names

        smuggled = self._detect_smuggled(narrative, allowed_names)

        if not smuggled:
            return PatchReport(
                patched=False,
                original=narrative,
                result=narrative,
                smuggled_names=[],
            )

        result = narrative
        for name in smuggled:
            result = result.replace(name, self.DEFAULT_REPLACEMENT)

        return PatchReport(
            patched=True,
            original=narrative,
            result=result,
            smuggled_names=smuggled,
        )

    # ── helpers ──────────────────────────────────────────────────────────

    def _collect_known_names(self) -> set[str]:
        names: set[str] = set()
        for entity in self.store.list_entities():
            names.add(entity.display_name)
            names.update(entity.tags)
        return names

    @staticmethod
    def _collect_proposed_names(proposals: list[dict[str, Any]]) -> set[str]:
        names: set[str] = set()
        for p in proposals:
            if p.get("type") == "CreateEntity":
                temp = p.get("temp_name", "")
                if temp:
                    names.add(temp)
                dn = p.get("fields", {}).get("display_name", "")
                if dn:
                    names.add(dn)
        return names

    def _detect_smuggled(self, narrative: str, allowed: set[str]) -> list[str]:
        """
        Heuristic detection of named entities in narrative text.
        Looks for quoted names (「…」, "…", '…') and CJK proper-noun patterns.
        """
        smuggled: list[str] = []

        quoted = re.findall(r'[「"\']([\u4e00-\u9fff\w]{2,10})[」"\']', narrative)
        for name in quoted:
            if name not in allowed and not self._is_common_word(name):
                smuggled.append(name)

        titled = re.findall(r"(?:叫|名为|称为|名叫|是)([\u4e00-\u9fff]{2,6})", narrative)
        for name in titled:
            if name not in allowed and not self._is_common_word(name):
                if name not in smuggled:
                    smuggled.append(name)

        return smuggled

    @staticmethod
    def _is_common_word(word: str) -> bool:
        common = {
            "你", "我", "他", "她", "它", "这里", "那里", "什么", "怎么",
            "一个", "一些", "可以", "不能", "已经", "需要", "知道", "觉得",
            "地方", "东西", "时候", "事情", "人们", "世界", "自己",
        }
        return word in common
