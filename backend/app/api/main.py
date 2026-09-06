"""FastAPI application (spec §32). Thin routes over the Platform facade."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import current_user, token_for, ws_user
from app.api.schemas import (
    ExecIn,
    IncidentPatch,
    LoginRequest,
    LoginResponse,
    MessageIn,
    PostmortemIn,
)
from app.events import EventType
from app.labs.base import LabError, UnknownDevice
from app.labs.reconcile import LIVE_STATES
from app.models.domain import Postmortem, ScenarioSession, User
from app.services.platform import IncompleteScenario, Platform
from app.services.terminal import TerminalSession
from app.services.world import DEMO_USERS

log = logging.getLogger("northstar.api")


def create_app(platform: Platform | None = None, *, housekeeping_interval_s: float = 30.0) -> FastAPI:
    plat = platform or Platform()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        info = await plat.startup()
        log.info("startup reconcile: %s", info)

        async def loop() -> None:
            while True:
                await asyncio.sleep(housekeeping_interval_s)
                try:
                    await plat.housekeeping()
                except Exception:
                    log.exception("housekeeping failed")

        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()
            await plat.shutdown()

    app = FastAPI(title="Northstar Technologies", version="0.2.0", lifespan=lifespan)
    app.state.platform = plat
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    # ----------------------------------------------------------------- helpers
    def resolve_session(user: User, session_id: str | None) -> ScenarioSession:
        if session_id:
            try:
                s = plat.sessions.get(session_id)
            except KeyError:
                raise HTTPException(404, "unknown session") from None
        else:
            s = plat.active_session(user.id)
            if s is None:
                raise HTTPException(404, "no active session; start a scenario first")
        if s.learner_id != user.id:
            raise HTTPException(403, "not your session")
        return s

    def sess_dep(session: str | None = Query(None), user: User = Depends(current_user)) -> ScenarioSession:
        return resolve_session(user, session)

    # -------------------------------------------------------------------- auth
    @app.post("/api/auth/login", response_model=LoginResponse)
    def login(body: LoginRequest) -> LoginResponse:
        email = body.email.strip().lower()
        for u in DEMO_USERS.values():
            if u.email == email or u.id == email:
                return LoginResponse(token=token_for(u), user=u.model_dump())
        raise HTTPException(401, "unknown demo account")

    @app.get("/api/me")
    def me(user: User = Depends(current_user)) -> dict[str, Any]:
        info = plat.world.me(user.id)
        active = plat.active_session(user.id)
        info["active_session"] = active.model_dump(mode="json", exclude={"baseline_configs"}) if active else None
        info["incidents"] = [i.model_dump() for i in plat.world.incidents(active.id)] if active else []
        info["scenarios"] = [{"id": "INC-1042", "title": plat.world.scenario("INC-1042").title}]
        info["character_mode"] = "llm" if plat.llm else "stub"
        return info

    # --------------------------------------------------------------- sessions
    @app.post("/api/scenarios/{scenario_id}/start")
    async def start_scenario(scenario_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
        try:
            s = await plat.start_scenario(scenario_id, user.id)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from None
        except Exception as e:
            log.exception("start failed")
            raise HTTPException(500, f"could not start scenario: {e}") from None
        return s.model_dump(mode="json", exclude={"baseline_configs"})

    @app.get("/api/sessions")
    def list_sessions(user: User = Depends(current_user)) -> list[dict[str, Any]]:
        return [s.model_dump(mode="json", exclude={"baseline_configs"}) for s in plat.store.list() if s.learner_id == user.id]

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
        s = resolve_session(user, session_id)
        d = s.model_dump(mode="json", exclude={"baseline_configs"})
        d["completion"] = plat.completion_status(s.id)
        d["prober_running"] = plat.sessions.prober(s.id) is not None
        return d

    @app.delete("/api/sessions/{session_id}")
    async def abandon_session(session_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
        s = resolve_session(user, session_id)
        if s.state in LIVE_STATES:
            s = await plat.sessions.destroy(s.id)
        return s.model_dump(mode="json", exclude={"baseline_configs"})

    @app.get("/api/sessions/{session_id}/events")
    def session_events(session_id: str, user: User = Depends(current_user), limit: int = 500) -> list[dict[str, Any]]:
        s = resolve_session(user, session_id)
        events = plat.db.events(s.id) or plat.sessions.bus(s.id).events
        return [e.model_dump(mode="json") for e in events[-limit:]]

    @app.post("/api/sessions/{session_id}/verify")
    async def verify(session_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
        s = resolve_session(user, session_id)
        if s.state not in LIVE_STATES:
            raise HTTPException(409, "session is not live")
        try:
            r = await plat.sessions.verify(s.id)
        except LabError as e:
            raise HTTPException(502, str(e)) from None
        return {**r.model_dump(mode="json"), "scenario_success": r.scenario_success, "quality_ok": r.quality_ok}

    @app.post("/api/sessions/{session_id}/exec")
    async def exec_command(session_id: str, body: ExecIn, user: User = Depends(current_user)) -> dict[str, Any]:
        """Non-interactive command execution (the terminal WebSocket is the primary path)."""
        s = resolve_session(user, session_id)
        if s.state not in LIVE_STATES:
            raise HTTPException(409, "session is not live")
        try:
            r = await plat.sessions.exec(s.id, body.device, body.command)
        except (KeyError, UnknownDevice):
            raise HTTPException(404, f"unknown device {body.device}") from None
        except LabError as e:
            raise HTTPException(502, str(e)) from None
        return r.model_dump()

    @app.get("/api/sessions/{session_id}/postmortem")
    def get_postmortem(session_id: str, user: User = Depends(current_user)) -> dict[str, Any] | None:
        s = resolve_session(user, session_id)
        pm = plat.postmortem(s.id)
        return pm.model_dump(mode="json") if pm else None

    @app.post("/api/sessions/{session_id}/postmortem")
    async def submit_postmortem(session_id: str, body: PostmortemIn, user: User = Depends(current_user)) -> dict[str, Any]:
        s = resolve_session(user, session_id)
        pm = Postmortem(session_id=s.id, incident_id=s.scenario_id, **body.model_dump())
        plat.save_postmortem(s.id, pm)
        await plat.sessions.bus(s.id).emit(EventType.POSTMORTEM_SUBMITTED, learner_id=s.learner_id, scenario_id=s.scenario_id, session_id=s.id, source="ui", words=sum(len(v.split()) for v in body.model_dump().values()))
        return pm.model_dump(mode="json")

    @app.post("/api/sessions/{session_id}/complete")
    async def complete(session_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
        s = resolve_session(user, session_id)
        try:
            assessment, detail = await plat.complete(s.id)
        except IncompleteScenario as e:
            raise HTTPException(409, {"missing": e.missing}) from None
        return {**assessment.model_dump(mode="json"), "detail": detail}

    @app.get("/api/sessions/{session_id}/scorecard")
    def scorecard(session_id: str, user: User = Depends(current_user)) -> dict[str, Any]:
        s = resolve_session(user, session_id)
        card = plat.scorecard(s.id)
        if card is None:
            if s.state in LIVE_STATES:
                return {"preview": True, **plat.preview_assessment(s.id)}
            raise HTTPException(404, "no scorecard")
        return {"preview": False, **card}

    # ---------------------------------------------------------------- world
    @app.get("/api/incidents")
    def incidents(s: ScenarioSession = Depends(sess_dep)) -> list[dict[str, Any]]:
        return [i.model_dump() for i in plat.world.incidents(s.id)]

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str, s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        inc = plat.world.incident(s.id, incident_id)
        if not inc:
            raise HTTPException(404, "unknown incident")
        return inc.model_dump()

    @app.patch("/api/incidents/{incident_id}")
    async def patch_incident(incident_id: str, body: IncidentPatch, s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        try:
            inc = await plat.update_incident(s.id, incident_id, body.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, "unknown incident") from None
        return inc.model_dump()

    @app.get("/api/wiki")
    def wiki(s: ScenarioSession = Depends(sess_dep)) -> list[dict[str, Any]]:
        return [p.model_dump(exclude={"body"}) for p in plat.world.wiki(s.scenario_id)]

    @app.get("/api/wiki/{page_id}")
    async def wiki_page(page_id: str, s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        p = plat.world.wiki_page(s.scenario_id, page_id)
        if not p:
            raise HTTPException(404, "unknown page")
        await plat.record_view(s.id, EventType.WIKI_PAGE_VIEWED, page=page_id)
        return p.model_dump()

    @app.get("/api/changes")
    async def changes(q: str | None = None, s: ScenarioSession = Depends(sess_dep)) -> list[dict[str, Any]]:
        await plat.record_view(s.id, EventType.CHANGE_LOG_VIEWED, query=q or "")
        return [c.model_dump() for c in plat.world.changes(s.scenario_id, q)]

    @app.get("/api/changes/{change_id}")
    async def change(change_id: str, s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        c = plat.world.change(s.scenario_id, change_id)
        if not c:
            raise HTTPException(404, "unknown change")
        await plat.record_view(s.id, EventType.CHANGE_LOG_VIEWED, change=c.id)
        return c.model_dump()

    @app.get("/api/monitoring")
    async def monitoring(s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        await plat.record_view(s.id, EventType.MONITORING_VIEWED)
        return plat.monitoring(s.id)

    @app.get("/api/network")
    def network(s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        sc = plat.world.scenario(s.scenario_id)
        return {
            "devices": [d.model_dump() for d in sc.topology.devices],
            "links": [
                {"a": "HQ-CLIENT", "b": "HQ-RTR1"},
                {"a": "APP-SRV", "b": "HQ-RTR1"},
                {"a": "HQ-RTR1", "b": "AUS-RTR1", "label": "WAN 10.255.0.0/30"},
                {"a": "AUS-RTR1", "b": "AUS-SW1"},
                {"a": "AUS-SW1", "b": "AUS-CLIENT1"},
                {"a": "AUS-SW1", "b": "AUS-CLIENT2"},
            ],
        }

    @app.get("/api/people")
    def people() -> list[dict[str, Any]]:
        return [e.model_dump() for e in plat.world.people()]

    # ----------------------------------------------------------------- chat
    @app.get("/api/conversations")
    def conversations(s: ScenarioSession = Depends(sess_dep)) -> list[dict[str, Any]]:
        return plat.world.conversations(s.id)

    @app.get("/api/conversations/{conv_id}")
    def conversation(conv_id: str, s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        conv = plat.world.conversation(s.id, conv_id)
        if not conv:
            raise HTTPException(404, "unknown conversation")
        return {**conv.model_dump(), "messages": [m.model_dump(mode="json") for m in plat.world.messages(s.id, conv_id)]}

    @app.post("/api/conversations/{conv_id}/messages")
    async def post_message(conv_id: str, body: MessageIn, s: ScenarioSession = Depends(sess_dep)) -> dict[str, Any]:
        try:
            return await plat.chat.send(s.id, conv_id, body.body)
        except KeyError:
            raise HTTPException(404, "unknown conversation") from None

    # -------------------------------------------------------------- terminal
    @app.websocket("/api/sessions/{session_id}/terminal")
    async def terminal(ws: WebSocket, session_id: str) -> None:
        user = ws_user(ws)
        if not user:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        try:
            s = plat.sessions.get(session_id)
        except KeyError:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if s.learner_id != user.id or s.state not in LIVE_STATES:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await ws.accept()
        term = TerminalSession(plat.sessions, session_id)
        await ws.send_json({"type": "output", "data": term.banner()})
        await ws.send_json({"type": "prompt", "data": term.prompt()})
        try:
            while True:
                msg = await ws.receive_json()
                # Authorise per message (spec §28): the session must still be this user's and live.
                current = plat.sessions.get(session_id)
                if current.learner_id != user.id or current.state not in LIVE_STATES:
                    await ws.send_json({"type": "output", "data": "\nSession ended.\n"})
                    await ws.close()
                    return
                if msg.get("type") != "input":
                    continue
                line = str(msg.get("data", ""))[:2000]
                try:
                    out = await term.handle(line)
                except (LabError, UnknownDevice) as e:
                    out = f"% {e}\n"
                except Exception:
                    log.exception("terminal error")
                    out = "% internal error\n"
                if out:
                    await ws.send_json({"type": "output", "data": out})
                await ws.send_json({"type": "prompt", "data": term.prompt()})
        except WebSocketDisconnect:
            return

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "provider": plat.provider.name, "characters": "llm" if plat.llm else "stub", "live_sessions": len(plat.sessions.live_sessions())}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        log.exception("unhandled error on %s", request.url.path)
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app
