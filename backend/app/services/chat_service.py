"""Chat (spec §17) — persistence plus the character pipeline."""

from __future__ import annotations

from typing import Any

from app.characters.engine import CharacterEngine
from app.characters.stub import mentioned
from app.events import EventType
from app.models.domain import ChatMessage
from app.services.session_service import SessionService
from app.services.world import World

STATUS_WORDS = ("status", "update", "investigating", "impact", "resolved", "fixed", "restored", "root cause", "next step", "eta", "currently", "so far")


def looks_like_status_update(text: str) -> bool:
    t = text.lower()
    return len(t.split()) >= 8 and any(w in t for w in STATUS_WORDS)


class ChatService:
    def __init__(self, world: World, sessions: SessionService, engine_factory) -> None:  # type: ignore[no-untyped-def]
        self.world = world
        self.sessions = sessions
        self._engine_factory = engine_factory
        self._engines: dict[str, CharacterEngine] = {}

    def engine(self, scenario_id: str) -> CharacterEngine:
        if scenario_id not in self._engines:
            self._engines[scenario_id] = self._engine_factory(scenario_id)
        return self._engines[scenario_id]

    async def send(self, session_id: str, conv_id: str, body: str) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        conv = self.world.conversation(session_id, conv_id)
        if conv is None:
            raise KeyError(conv_id)
        body = body.strip()
        recipients = [p for p in conv.participant_ids if p != session.learner_id] if conv.kind == "dm" else mentioned(body)
        msg = self.world.add_message(session_id, conv_id, session.learner_id, body)
        is_update = looks_like_status_update(body) and ("maya" in recipients or conv.kind == "channel")
        await self.sessions.bus(session_id).emit(
            EventType.CHAT_MESSAGE_SENT,
            learner_id=session.learner_id,
            scenario_id=session.scenario_id,
            session_id=session_id,
            source="chat",
            conversation=conv_id,
            to=recipients,
            words=len(body.split()),
            status_update=is_update,
        )
        self.sessions._touch(session)
        replies: list[ChatMessage] = []
        engine = self.engine(session.scenario_id)
        for cid in recipients:
            if cid not in engine.characters:
                continue
            history = self.world.messages(session_id, conv_id)
            events = self.sessions.bus(session_id).events
            text = await engine.reply(cid, body, history[:-1], events)
            if not text:
                continue
            reply = self.world.add_message(session_id, conv_id, cid, text, {"mode": engine.mode})
            replies.append(reply)
            await self.sessions.bus(session_id).emit(
                EventType.CHAT_MESSAGE_RECEIVED,
                learner_id=session.learner_id,
                scenario_id=session.scenario_id,
                session_id=session_id,
                source="chat",
                conversation=conv_id,
                sender=cid,
                mode=engine.mode,
            )
        return {"message": msg.model_dump(mode="json"), "replies": [r.model_dump(mode="json") for r in replies]}

    async def system_message(self, session_id: str, cid: str, intent: str, conv_id: str | None = None) -> ChatMessage | None:
        session = self.sessions.get(session_id)
        conv_id = conv_id or f"dm-{cid}"
        engine = self.engine(session.scenario_id)
        history = self.world.messages(session_id, conv_id)
        text = await engine.system_message(cid, intent, history, self.sessions.bus(session_id).events)
        if not text:
            return None
        msg = self.world.add_message(session_id, conv_id, cid, text, {"mode": engine.mode, "intent": intent})
        await self.sessions.bus(session_id).emit(
            EventType.CHAT_MESSAGE_RECEIVED,
            learner_id=session.learner_id,
            scenario_id=session.scenario_id,
            session_id=session_id,
            source="trigger",
            conversation=conv_id,
            sender=cid,
            intent=intent,
        )
        return msg
