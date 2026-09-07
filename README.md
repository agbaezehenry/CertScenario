# Northstar Technologies

An AI-powered network engineer simulation platform. The learner joins a
fictional company as an Associate Network Engineer and works a real incident
on a real (containerised) network. AI characters supply context, incomplete
information and wrong beliefs; a deterministic verification engine decides
whether the network is actually fixed.

Build spec: [northstar-build-spec.md](northstar-build-spec.md) ·
Architecture: [docs/architecture.md](docs/architecture.md) ·
Scenario authoring: [docs/scenario-authoring.md](docs/scenario-authoring.md) ·
Lab runtime: [docs/lab-runtime.md](docs/lab-runtime.md)

## Status

| Phase | State |
|---|---|
| 0 — architecture, domain model, schema, interfaces | done |
| 1 — lab runtime (containerlab + FRR, faults, verification, prober, lifecycle, reconciliation, CLI) | code complete; 12-step milestone passes on the mock; **not yet executed on real containerlab** (needs a Linux host with docker + containerlab) |
| 2 — backend API and persistence (FastAPI, SQLAlchemy, WebSocket terminal) | done |
| 3 — learner workspace UI (Next.js) | done; the §39 acceptance walkthrough was played end to end in a browser against the mock |
| 4 — AI characters (context boundaries, LLM provider, validator, triggers) | done; rule-based responder when no LLM key is set; **leak-test gate not yet run against a live model** |
| 5 — consequence system (prober → monitoring → character reactions) | done |
| 6 — assessment (six-dimension scorecard citing real events, postmortem grading) | done |
| 7 — polish (compose, docs, CI) | done for MVP scope |

## Quick start

```bash
docker compose up
# open http://localhost:3000 and sign in as henry@northstar.example
```

That runs the **mock** lab provider, which is a network model of the same
topology (not canned text) and is enough to play the whole incident. To run
real FRR routers you need Linux with Docker and
[containerlab](https://containerlab.dev):

```bash
docker compose --profile real up
```

Set `LLM_API_KEY` (and optionally `LLM_BASE_URL`, `LLM_MODEL`) to have Maya,
Carlos and Priya answered by an OpenAI-compatible model; without it they use a
rule-based responder and the header shows "characters: offline".

### Local development

```bash
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --app-dir backend --reload      # API on :8000
cd frontend && npm install && npm run dev                       # UI on :3000
```

```bash
.venv/bin/pytest                          # 60 tests (unit + API walkthrough)
.venv/bin/python -m app.cli milestone     # the 12-step Phase 1 loop (mock)
```

### CLI

```bash
northstar scenario start INC-1042 --provider mock
export NORTHSTAR_SESSION=<id> LAB_PROVIDER=mock
northstar lab exec AUS-RTR1 "show ip ospf neighbor"      # Full — the trap
northstar lab exec HQ-RTR1  "show ip route ospf"         # Austin prefix missing
northstar lab exec AUS-RTR1 "configure terminal; router ospf; network 10.20.10.0/24 area 0; end"
northstar scenario verify
northstar scenario destroy
northstar cleanup-labs --dry-run
```

## Layout

```
backend/app/
  api/           FastAPI routes, demo auth, terminal WebSocket
  labs/          LabProvider protocol, containerlab + mock providers, faults, reconciliation
  scenarios/     YAML schema (ScenarioDefinition / ScenarioPublicView) + loader
  verification/  deterministic assertions, categorised runner
  probing/       1 Hz prober -> JSONL + connectivity events
  characters/    context builder, LLM abstraction, prompts, validator, stub, triggers, engine
  assessments/   scorecard + postmortem grading
  services/      session lifecycle, world (people/tickets/chat/wiki), terminal, platform facade
  events/        event vocabulary + bus (system of record)
  db.py          SQLAlchemy persistence (SQLite default, DATABASE_URL for Postgres)
frontend/        Next.js 15 workspace: dashboard, incidents, chat, monitoring, wiki, change log,
                 network, terminal (xterm.js), postmortem, scorecard
scenarios/       data-driven scenarios; inc-1042 is the vertical slice
tests/           unit (mock) · integration (real containerlab) · characters (leak gate)
docs/            architecture · scenario-authoring · lab-runtime
```

## Principles enforced in code

* An LLM never decides whether the network is fixed (`verification/`).
* Terminal output comes from real containers; the mock is a network model,
  and CI runs the integration suite against the real provider.
* Characters structurally cannot see the root cause: the context builder only
  accepts `ScenarioPublicView`, which has no `hidden` field
  (`tests/unit/test_character_context.py`). A validator rejects and
  regenerates any reply that names a forbidden string or config line.
* Resolution, regression and quality checks are distinct categories.
* Every scorecard line cites an event (command telemetry, prober log, ticket,
  chat). Nothing is inferred by a model.
* Labs are ephemeral: 20-minute idle and 4-hour absolute timeouts, orphan
  reconciliation on startup, `northstar cleanup-labs`.

## Known gaps

* The real containerlab provider has not been exercised on this machine
  (macOS, no docker). Run `LAB_PROVIDER=containerlab northstar milestone` on a
  Linux host before trusting grading on real FRR.
* The character leak gate (`tests/characters`) needs an LLM key to run. Zero
  leaks is a ship requirement (spec §40.3).
* Demo auth only (bearer token `demo-<user>`). Replace `api/deps.py` before
  exposing the service.
* SQLite auto-adds missing columns; use Alembic once there is a production
  schema to protect.
