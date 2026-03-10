"""
LLM Adapter — provider-agnostic wrapper for calling an OpenAI-compatible
chat-completion API with structured JSON output and retry logic.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from openai import OpenAI

from models import LLMResponse

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Game Master of an improvised tabletop RPG. You do NOT have a \
pre-written script. Your job is to collaboratively build the world and story \
with the player through narration and structured proposals.

RULES:
1. Every response MUST be valid JSON with exactly two top-level keys:
   "narrative" (string — the text shown to the player) and
   "proposals" (list — structured world-change proposals).
2. If your narrative mentions a NEW entity (NPC, location, item, etc.) you MUST
   include a corresponding CreateEntity proposal; otherwise, use vague language.
3. You may NOT set dice results, reveal secrets, or directly change numeric stats.
4. Facts default to "canon"; use "rumor" or "hypothesis" when uncertain.
5. Keep the tone consistent with the Session Contract.
6. Write narrative in the language the player uses.
"""


class LLMAdapter:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o",
        temperature: float = 0.85,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        self.client = OpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
            base_url=base_url or os.getenv("OPENAI_BASE_URL"),
        )

    def call(
        self,
        context_prompt: str,
        player_input: str,
        extra_system: str = "",
    ) -> LLMResponse:
        system_msg = SYSTEM_PROMPT
        if extra_system:
            system_msg += f"\n\n{extra_system}"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"### World Context\n{context_prompt}"},
            {"role": "user", "content": f"### Player Action\n{player_input}"},
        ]

        raw_text = self._completion_with_retry(messages)
        return self._parse_response(raw_text)

    # ── completion with retry ────────────────────────────────────────────

    def _completion_with_retry(
        self, messages: list[dict[str, str]], *, json_mode: bool = True
    ) -> str:
        last_error: Exception | None = None
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
                content = resp.choices[0].message.content or ""
                return content
            except Exception as exc:
                last_error = exc
                logger.warning("LLM call attempt %d failed: %s", attempt, exc)
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
        raise RuntimeError(f"LLM call failed after {self.max_retries} attempts: {last_error}")

    # ── response parsing ─────────────────────────────────────────────────

    @staticmethod
    def _parse_response(raw_text: str) -> LLMResponse:
        try:
            return LLMResponse.from_raw_json(raw_text)
        except (json.JSONDecodeError, Exception) as exc:
            logger.error("Failed to parse LLM response: %s\nRaw text:\n%s", exc, raw_text[:500])
            return LLMResponse(
                narrative="[系统：LLM 返回了无法解析的内容，请重试。]",
                proposals=[],
            )

    # ── helper: build a short follow-up for rephrasing ───────────────────

    def request_rephrase(self, original_narrative: str, issue: str) -> str:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are a narrative editor. Rewrite the following narrative text to "
                    "fix the issue described. Return ONLY the corrected narrative text, "
                    "nothing else. Maintain the same language and tone."
                ),
            },
            {
                "role": "user",
                "content": f"Issue: {issue}\n\nOriginal:\n{original_narrative}",
            },
        ]
        try:
            return self._completion_with_retry(messages, json_mode=False)
        except Exception:
            return original_narrative
