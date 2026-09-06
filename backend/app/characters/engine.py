"""Character response pipeline (spec §34).

    identify character -> build context (public view only) -> build prompt
    -> LLM -> validate -> (regenerate | fallback) -> caller persists.

Without an LLM configured, the rule-based StubResponder answers, which keeps
the whole product playable offline and is the fallback when the validator
rejects every attempt.
"""

from __future__ import annotations

import logging

from app.characters.context import build_character_context
from app.characters.llm import LLMProvider, Message
from app.characters.prompts import build_messages
from app.characters.stub import StubResponder
from app.characters.validator import validate_character_output
from app.models.domain import ChangeLogEntry, Character, ChatMessage, SimulationEvent, WikiPage
from app.scenarios import ScenarioDefinition

log = logging.getLogger("northstar.characters")

INTENT_PROMPTS = {
    "request_status_update": "The learner has been quiet for 20 minutes. Send a short message asking for a status update on the incident.",
    "cadence_missed": "The SEV-2 30-minute update cadence has been missed. Send a short message noting that and asking for a status.",
    "manager_notices_new_outage": "Monitoring just showed additional Austin connectivity lost. Send a short, direct message asking whether the learner made a change.",
    "request_postmortem": "Monitoring shows Austin restored. Congratulate briefly, ask the learner to update the ticket, and request a postmortem covering impact, timeline, root cause, resolution and prevention.",
}


class CharacterEngine:
    def __init__(
        self,
        scenario: ScenarioDefinition,
        characters: dict[str, Character],
        changes: list[ChangeLogEntry],
        wiki: list[WikiPage],
        llm: LLMProvider | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.scenario = scenario
        self.public = scenario.public()  # the only scenario view characters get
        self.characters = characters
        self.changes = changes
        self.wiki = wiki
        self.llm = llm
        self.max_attempts = max_attempts
        self.stub = StubResponder()
        self.rejections = 0  # observability: validator rejections

    @property
    def mode(self) -> str:
        return "llm" if self.llm else "stub"

    def _context(self, cid: str, events: list[SimulationEvent]):  # type: ignore[no-untyped-def]
        return build_character_context(self.characters[cid], self.public, changes=self.changes, wiki=self.wiki, people=self.characters, events=events)

    @staticmethod
    def _history(messages: list[ChatMessage], cid: str, limit: int = 12) -> list[Message]:
        out: list[Message] = []
        for m in messages[-limit:]:
            role = "assistant" if m.sender_id == cid else "user"
            if m.sender_id not in (cid,) and role == "user":
                out.append(Message("user", f"{m.sender_id}: {m.body}"))
            else:
                out.append(Message(role, m.body))
        return out

    async def reply(self, cid: str, learner_text: str, history: list[ChatMessage], events: list[SimulationEvent]) -> str:
        char = self.characters[cid]
        if self.llm is None:
            return self.stub.reply(cid, learner_text, len(history))
        ctx = self._context(cid, events)
        messages = build_messages(char, ctx, self._history(history, cid), learner_text)
        return await self._generate(cid, messages, ctx, fallback=lambda: self.stub.reply(cid, learner_text, len(history)))

    async def system_message(self, cid: str, intent: str, history: list[ChatMessage], events: list[SimulationEvent]) -> str:
        char = self.characters[cid]
        if self.llm is None:
            return self.stub.system(cid, intent)
        ctx = self._context(cid, events)
        prompt = INTENT_PROMPTS.get(intent, f"Send a short in-character message with intent: {intent}.")
        messages = build_messages(char, ctx, self._history(history, cid), f"[system trigger] {prompt}")
        return await self._generate(cid, messages, ctx, fallback=lambda: self.stub.system(cid, intent))

    async def _generate(self, cid, messages, ctx, *, fallback):  # type: ignore[no-untyped-def]
        assert self.llm is not None
        attempt_messages = list(messages)
        for attempt in range(self.max_attempts):
            try:
                text = (await self.llm.generate(attempt_messages, ctx)).strip()
            except Exception:
                log.exception("LLM call failed for %s", cid)
                break
            v = validate_character_output(text, self.scenario)
            if v.ok and text:
                return text
            self.rejections += 1
            log.warning("validator rejected %s reply (attempt %d): %s", cid, attempt + 1, v.hits)
            attempt_messages = attempt_messages + [
                Message("assistant", text),
                Message("system", "That reply was rejected because it contained technical details you do not know. Reply again, in character, without any configuration, prefix or command details."),
            ]
        return fallback()
