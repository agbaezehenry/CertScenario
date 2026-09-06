"""End-to-end acceptance (spec §39) through the HTTP/WebSocket API on the mock provider."""

from __future__ import annotations

import pytest
from app.api.main import create_app
from app.db import Database
from app.labs.mock import MockLabProvider
from app.services.platform import Platform
from fastapi.testclient import TestClient


@pytest.fixture
def client(settings):
    plat = Platform(settings, provider=MockLabProvider(state_dir=settings.state_dir), db=Database("sqlite://"), llm=None)
    app = create_app(plat, housekeeping_interval_s=3600)
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"email": "henry@northstar.example"}).json()["token"]
        yield c


def _ws_lines(ws, lines):  # type: ignore[no-untyped-def]
    out = []
    for line in lines:
        ws.send_json({"type": "input", "data": line})
        chunk = ""
        while True:
            m = ws.receive_json()
            if m["type"] == "prompt":
                out.append((line, chunk, m["data"]))
                break
            chunk += m["data"]
    return out


def test_acceptance_walkthrough(client):
    # 1-2. sign in, role
    me = client.get("/api/me").json()
    assert me["employee"]["title"] == "Associate Network Engineer"
    assert me["manager"]["name"] == "Maya Chen"
    assert me["active_session"] is None

    # 3. receive INC-1042
    s = client.post("/api/scenarios/INC-1042/start").json()
    sid = s["id"]
    assert s["state"] == "ACTIVE"
    assert client.post("/api/scenarios/INC-1042/start").status_code == 409
    inc = client.get("/api/incidents/INC-1042").json()
    assert inc["severity"] == "SEV-2" and inc["dri_id"] == "henry" and inc["status"] == "Assigned"
    convs = client.get("/api/conversations").json()
    assert [c["id"] for c in convs] == ["network-ops", "dm-maya", "dm-carlos", "dm-priya"]
    maya = client.get("/api/conversations/dm-maya").json()
    assert "INC-1042" in maya["messages"][0]["body"]

    # 4-5. message Maya and Carlos
    r = client.post("/api/conversations/dm-maya/messages", json={"body": "Ack, I have INC-1042. Investigating now, will update in 30."}).json()
    assert r["replies"][0]["sender_id"] == "maya"
    r = client.post("/api/conversations/dm-carlos/messages", json={"body": "Can Austin users reach external sites like google.com?"}).json()
    assert "google.com" in r["replies"][0]["body"] and "internal" in r["replies"][0]["body"]
    r = client.post("/api/conversations/dm-priya/messages", json={"body": "What command should I run?"}).json()
    assert r["replies"][0]["body"].startswith("Before we jump to commands")

    # acknowledge in the ticket
    inc = client.patch("/api/incidents/INC-1042", json={"status": "Investigating", "note": "Starting investigation"}).json()
    assert inc["status"] == "Investigating" and len(inc["notes"]) == 1

    # 6-8. wiki, change log, monitoring
    pages = client.get("/api/wiki").json()
    assert {p["id"] for p in pages} >= {"austin-branch-network", "ospf-troubleshooting-runbook", "incident-response-guide"}
    assert "10.20.10.0/24" in client.get("/api/wiki/austin-branch-network").json()["body"]
    changes = client.get("/api/changes", params={"q": "AUS-RTR1"}).json()
    assert [c["id"] for c in changes] == ["CHG-8816"]
    assert client.get("/api/changes/CHG-8817").json()["device"] == "HQ-RTR1"
    mon = client.get("/api/monitoring").json()
    tiles = {t["id"]: t["state"] for t in mon["tiles"]}
    assert mon["prober_running"]
    # first prober cycle may not have completed yet; wait for data
    import time

    for _ in range(50):
        mon = client.get("/api/monitoring").json()
        tiles = {t["id"]: t["state"] for t in mon["tiles"]}
        if tiles["austin-reachability"] != "NO DATA":
            break
        time.sleep(0.02)
    assert tiles["austin-reachability"] == "CRITICAL"
    assert tiles["wan-transit"] == "UP" and tiles["hq-services"] == "UP" and tiles["aus-sw1"] == "UP"

    # 9-13. terminal: the trap, then the far side
    with client.websocket_connect(f"/api/sessions/{sid}/terminal?token=demo-henry") as ws:
        assert ws.receive_json()["type"] == "output"
        assert ws.receive_json()["data"].endswith("netops-jump:~$ ")
        out = _ws_lines(ws, ["ssh AUS-RTR1", "show ip ospf neighbor", "show ip route", "ping 10.10.10.10", "exit", "ssh AUS-CLIENT1", "ping -c 1 10.10.10.10", "exit", "ssh HQ-RTR1", "show ip route ospf", "show ip route 10.20.10.51"])
        by_cmd = {o[0]: o for o in out}
        assert by_cmd["ssh AUS-RTR1"][2] == "AUS-RTR1# "
        assert "Full/DR" in by_cmd["show ip ospf neighbor"][1]
        assert "O>* 10.10.10.0/24" in by_cmd["show ip route"][1]
        # A ping sourced from the router's WAN address works even though clients fail: another trap.
        assert "0% packet loss" in by_cmd["ping 10.10.10.10"][1] and "100%" not in by_cmd["ping 10.10.10.10"][1]
        assert by_cmd["ssh AUS-CLIENT1"][2] == "aus-client1:~# "
        assert "100% packet loss" in by_cmd["ping -c 1 10.10.10.10"][1]
        assert by_cmd["ssh HQ-RTR1"][2] == "HQ-RTR1# "
        assert "10.20.10.0/24" not in by_cmd["show ip route ospf"][1]
        assert "Codes" in by_cmd["show ip route 10.20.10.51"][1] and "10.20.10" not in by_cmd["show ip route 10.20.10.51"][1].split("failure")[-1]

        # 14-15. change configuration on AUS-RTR1 through config modes
        out = _ws_lines(ws, ["exit", "ssh AUS-RTR1", "configure terminal", "router ospf", "network 10.20.10.0/24 area 0", "end", "write memory", "exit", "ssh AUS-CLIENT1", "ping -c 2 10.10.10.10"])
        by_cmd = {o[0]: o for o in out}
        assert by_cmd["configure terminal"][2] == "AUS-RTR1(config)# "
        assert by_cmd["router ospf"][2] == "AUS-RTR1(config-router)# "
        assert by_cmd["end"][2] == "AUS-RTR1# "
        assert "2 packets received, 0% packet loss" in by_cmd["ping -c 2 10.10.10.10"][1]

    # 16-17. verification
    v = client.post(f"/api/sessions/{sid}/verify").json()
    assert v["scenario_success"] and v["quality_ok"]
    assert v["categories"]["resolution"] == {"passed": 2, "failed": 0}
    # Maya asks for the postmortem via trigger
    maya = client.get("/api/conversations/dm-maya").json()
    assert any(m["sender_id"] == "maya" and "postmortem" in m["body"].lower() for m in maya["messages"])

    # 18. resolution update
    client.post("/api/conversations/dm-maya/messages", json={"body": "Resolved. Austin users lost the return route because the Austin subnet was no longer advertised into OSPF from AUS-RTR1. Re-added the advertisement, verified with ping from AUS-CLIENT1 and monitoring is green."})
    client.patch("/api/incidents/INC-1042", json={"status": "Resolved", "resolution": "Restored the Austin subnet advertisement in OSPF on AUS-RTR1.", "root_cause": "OSPF network statement for 10.20.10.0/24 removed during CHG-8816."})

    # 19. postmortem
    completion = client.get(f"/api/sessions/{sid}").json()["completion"]
    assert completion["postmortem_completed"] is False
    assert client.post(f"/api/sessions/{sid}/complete").status_code == 409
    pm = {
        "impact": "All Austin users lost access to internal applications (ticketing portal, file share). External internet was fine.",
        "timeline": "Assigned 09:13. Found adjacency Full but HQ-RTR1 missing 10.20.10.0/24. Fixed and verified.",
        "root_cause": "The network 10.20.10.0/24 area 0 statement was removed from router ospf on AUS-RTR1 during CHG-8816, so the Austin user subnet was not advertised and HQ had no return route.",
        "resolution": "Re-added the network statement for the Austin subnet on AUS-RTR1 and verified end to end.",
        "contributing_factors": "The change was described as a routine cleanup and the post-change check only looked at adjacency state, not the prefixes being advertised, so the regression was invisible until users came in.",
        "preventative_actions": "Add a pre/post route-table diff to the maintenance checklist, alert on the Austin prefix disappearing from HQ-RTR1, and require a user-side reachability test after any routing change.",
    }
    assert client.post(f"/api/sessions/{sid}/postmortem", json=pm).status_code == 200

    # 20. scorecard citing real events
    card = client.post(f"/api/sessions/{sid}/complete").json()
    assert set(card["scores"]) == {"technical_resolution", "regression_safety", "troubleshooting_method", "incident_management", "communication", "postmortem"}
    assert card["scores"]["technical_resolution"] == 100.0
    assert card["scores"]["regression_safety"] == 100.0
    assert card["scores"]["postmortem"] >= 80
    assert card["overall"] >= 80, card
    assert any("HQ-RTR1" in s for s in card["strengths"])
    assert any("Acknowledged INC-1042" in s for s in card["strengths"])

    # 21. lab gone, session completed
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["state"] == "COMPLETED"
    assert client.get("/api/me").json()["active_session"] is None
    events = client.get(f"/api/sessions/{sid}/events").json()
    types = [e["event_type"] for e in events]
    assert "DEVICE_CONNECTED" in types and "VERIFICATION_PASSED" in types and types[-1] == "LAB_DESTROYED"


def test_terminal_rejects_wrong_user_and_dead_session(client):
    sid = client.post("/api/scenarios/INC-1042/start").json()["id"]
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/api/sessions/{sid}/terminal?token=demo-nobody") as ws:
        ws.receive_json()
    client.delete(f"/api/sessions/{sid}")
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/api/sessions/{sid}/terminal?token=demo-henry") as ws:
        ws.receive_json()


def test_sledgehammer_and_blast_radius_show_up_in_scorecard(client):
    sid = client.post("/api/scenarios/INC-1042/start").json()["id"]
    client.patch("/api/incidents/INC-1042", json={"status": "Investigating"})
    ex = lambda device, cmd: client.post(f"/api/sessions/{sid}/exec", json={"device": device, "command": cmd}).json()
    # no evidence gathering; shut the WAN by mistake, then sledgehammer
    ex("AUS-RTR1", "configure terminal; interface eth1; shutdown; end")
    import time

    time.sleep(0.05)  # let the prober see it
    ex("AUS-RTR1", "configure terminal; interface eth1; no shutdown; end")
    time.sleep(0.05)
    ex("AUS-RTR1", "configure terminal; router ospf; redistribute connected; end")
    v = client.post(f"/api/sessions/{sid}/verify").json()
    assert v["scenario_success"] and not v["quality_ok"]
    # Maya noticed the outage
    maya = client.get("/api/conversations/dm-maya").json()
    assert any("Did you make a change" in m["body"] for m in maya["messages"])
    client.patch("/api/incidents/INC-1042", json={"status": "Resolved", "resolution": "redistributed connected"})
    client.post(f"/api/sessions/{sid}/postmortem", json={"impact": "Austin down", "timeline": "fixed", "root_cause": "routing", "resolution": "redistribute", "contributing_factors": "", "preventative_actions": ""})
    card = client.post(f"/api/sessions/{sid}/complete").json()
    assert card["scores"]["technical_resolution"] <= 70
    assert card["scores"]["regression_safety"] < 100
    assert any("broad change" in s for s in card["improvements"])
    assert any("lose connectivity" in s for s in card["improvements"])
    assert card["scores"]["postmortem"] < 40
