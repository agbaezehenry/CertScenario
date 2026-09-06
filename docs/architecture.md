# Northstar — Architecture (Phase 0)

This document is the spec §48 deliverable: repository structure, system
architecture, domain model, scenario schema, lab-provider interface,
verification interface, event model, decisions and tradeoffs, and the Phase 1
plan. The code under `backend/app` implements Phase 1 exactly as described here.

## 1. Repository structure

```
northstar/
├── backend/app/
│   ├── config.py            env-driven Settings
│   ├── cli.py               northstar CLI (scenario / lab / prober / cleanup-labs / milestone)
│   ├── models/domain.py     domain entities (Pydantic; SQLAlchemy mapping is Phase 2)
│   ├── events/              EventType vocabulary + EventBus (JSONL system of record)
│   ├── scenarios/           YAML schema (ScenarioDefinition / ScenarioPublicView) + loader
│   ├── labs/                LabProvider protocol, containerlab + mock providers,
│   │                        FRR config parser, fault injection, session store, reconciliation
│   ├── verification/        deterministic assertions + categorised runner
│   ├── probing/             1 Hz background prober -> JSONL + connectivity events
│   ├── characters/          context builder (structural spoiler defense) + output validator
│   ├── services/            SessionService (lifecycle) + milestone runner
│   ├── assessments/         (Phase 6)
│   └── api/                 (Phase 2 — FastAPI)
├── scenarios/inc-1042/
│   ├── scenario.yaml        declarative scenario; `hidden:` is the ground truth
│   ├── topology.clab.yml    containerlab template (__LAB_NAME__ placeholder)
│   ├── configs/healthy/     FRR configs as deployed
│   ├── configs/broken/      healthy minus the fault — the diff IS the root cause
│   ├── wiki/                markdown pages
│   └── seed/                change log, characters, monitoring board
├── tests/unit | integration | characters
├── docs/
└── frontend/                (Phase 3)
```

## 2. System architecture

A modular monolith. One Python process owns all engines; boundaries are
package boundaries, not network boundaries.

```
              CLI (now) / FastAPI + WebSocket (Phase 2-3)
                              |
                       SessionService
        +----------+----------+-----------+------------+
        |          |          |           |            |
   ScenarioEngine  LabProvider  Verifier   Prober   CharacterEngine (Phase 4)
   (yaml, state)   (clab|mock)  (assertions) (1 Hz)   (context builder, LLM, validator)
        |          |          |           |            |
        +----------+----------+-----------+------------+
                              |
                          EventBus  ->  events.jsonl  (system of record)
```

Four conceptual engines (spec §46) map onto packages:

| Engine      | Packages                              | Determinism |
|-------------|---------------------------------------|-------------|
| World       | characters, scenarios/seed, wiki      | LLM + rules |
| Simulation  | labs, probing                         | real containers |
| Scenario    | scenarios, services, events           | deterministic |
| Competency  | verification, assessments             | deterministic (LLM only for prose quality) |

The AI layer never touches `labs` or `verification`. Success is decided by
assertions against real device state; characters are told *that* things
happened (via filtered events), never *what the device output was*.

## 3. Domain model

See `backend/app/models/domain.py`. Highlights:

* `ScenarioSession` carries `lab_id`, `state`, `baseline_configs`,
  `last_activity_at` (idle clock) and `container_minutes` (cost, spec §7.3).
* `ScenarioState` with an explicit legal-transition table and
  `transition()` guard. `FAILED`/`DESTROYED` are terminal-ish escape hatches.
* `VerificationResult` groups `CheckResult`s by `AssertionCategory`
  (`resolution` / `regression` / `quality`), and defines
  `scenario_success = resolution_ok and regression_ok`.
* `SimulationEvent` is the append-only record; `ProberSample` is the prober's.
* `CharacterContext` is the explicit knowledge boundary object.
* Career / skill entities exist as data only (spec §25–26).

Scenario-specific *definitions* stay in YAML (`scenarios/schema.py`);
runtime entities are typed models. Persistence is a `SessionStore` protocol
with a JSON implementation; Phase 2 swaps in SQLAlchemy behind the same shape.

## 4. Scenario schema

See `docs/scenario-authoring.md`. The load-bearing design choices:

* **Three assertion categories, never flattened.** The schema rejects a
  scenario without at least one resolution and one regression check.
* **`hidden:` key.** Root cause, minimal fix, forbidden strings and
  sledgehammer patterns. `ScenarioDefinition.public()` builds a
  `ScenarioPublicView` field-by-field; that type has no `hidden` attribute and
  is the *only* scenario type the character context builder accepts.
* **Prober matrix is declared per scenario**, with named vantages on a
  dedicated prober container.
* **`configs/healthy` and `configs/broken` are both committed.** A unit test
  asserts `broken == healthy − fault`, so the ground truth can't drift from
  the fault definition.

## 5. Lab-provider interface

```python
class LabProvider(Protocol):
    name: str
    async def provision(self, scenario, *, lab_id) -> LabInstance
    async def destroy(self, lab_id) -> None
    async def exec(self, lab_id, device, command) -> CommandResult   # host shell
    async def vtysh(self, lab_id, device, lines) -> CommandResult    # router CLI session
    async def ping(self, lab_id, device, destination, *, source, count, timeout_s) -> PingResult
    async def get_state(self, lab_id) -> LabState                    # running configs
    async def list_labs(self) -> list[str]                           # for reconciliation
    def devices(self, lab_id) -> list[Device]
```

`ping` is first-class because both verification and the prober need a parsed
result and the mock must answer from its model, not from text.
`vtysh` takes a list of lines executed in one session so config changes are
atomic from the caller's view (`configure terminal`, ..., `end`).

Two implementations:

* `ContainerlabProvider` — renders the topology template into a per-session
  workdir, copies healthy configs there (bind-mounted, so `write memory`
  persists per session), runs `containerlab deploy/destroy` and `docker exec`.
  Every subprocess has a wall-clock timeout and output cap.
* `MockLabProvider` — a `SimulatedNetwork` built from the same template and
  configs: L2 segments from links (switch ports bridge), FRR `network`
  statement containment semantics, adjacency only when both ends are in
  OSPF and up, prefix flooding over the adjacency graph, hop-by-hop
  forwarding *in both directions*. It reproduces the §9.3 behaviour (Full
  adjacency, outbound route present, reply dropped) rather than scripting it.

## 6. Verification interface

```python
class Assertion:            # id, category, on_fail
    async def evaluate(provider, lab_id, ctx) -> CheckResult
```

Implemented: `PingAssertion`, `RouteAssertion`, `OSPFNeighborAssertion`,
`InterfaceAssertion`, `ConfigPresentAssertion`, `ConfigAbsentAssertion`,
`ConfigUnchangedAssertion`, `DNSAssertion`, `ServiceAssertion`, and a
`VLANAssertion` placeholder (needs a switching NOS, spec §7.1).

Assertions read `show ... json` so one parser serves real FRR and the mock.
`ConfigUnchangedAssertion` compares a config *section* against
`VerificationContext.baseline_configs`, captured **after** fault injection —
the world as the learner found it.

`Verifier.run` returns the §19.4 shape with per-category pass/fail counts.

## 7. Event model

`EventType` enumerates the §11 vocabulary plus lab/prober lifecycle events.
`EventBus.emit()` appends to `events.jsonl` under the session dir and fans out
to in-process subscribers (the consequence engine and character triggers hang
off this in Phase 5). `COMMAND_EXECUTED` carries `read_only`, which is what
the evidence-before-change ratio (§22) is computed from.

## 8. Decisions and tradeoffs

| Decision | Why | Cost |
|---|---|---|
| FRR for routers, Alpine for hosts | Free, permissive, IOS-like vtysh | No VLAN/STP coverage until cEOS licensing is resolved |
| Prober is a dedicated container with two legs (Austin LAN via AUS-SW1, HQ via a /30 on HQ-RTR1) and policy routing | Its traffic is distinguishable by source address; exercises the same path as Austin clients | One extra /30 and an extra interface on HQ-RTR1, documented in the wiki as the monitoring link |
| Node cap counts routers+hosts (6), not switch/prober | Matches the spec's "six nodes plus the switch"; infra shouldn't shrink the scenario budget | Cap is a policy, not a container count |
| Fault applied at runtime via vtysh, not by deploying `configs/broken` | Idempotent, reversible, testable broken→fixed→broken without reprovisioning | `configs/broken` is documentation/ground truth, kept consistent by a test |
| Baseline for `config_unchanged` captured after fault | The learner must leave untouched what they found; reverting the decoy is a regression | Captured state includes the fault — intentional |
| Mock is a network model, not canned output | A canned mock produces confident false passes (spec §30) | ~600 lines; must never become the graded path — CI runs the real suite |
| JSON session store now, SQLAlchemy later | Phase 1 needs crash-survivable lab ownership for reconciliation, not a DB | One migration later |
| Modular monolith, asyncio | One process, one deploy; subprocess I/O is naturally async | Horizontal scaling means multiple backends sharing a DB later |
| Session-scoped `EventBus` writing JSONL | Replayable, greppable, survives crashes | DB sink added in Phase 2 |

## 9. Phase 1 plan (delivered)

1. Scenario assets: topology template, healthy/broken configs, wiki, seed.
2. Scenario schema + loader with validation and the public view.
3. `LabProvider` protocol; containerlab provider; mock network model.
4. Fault injector (`remove_ospf_network`), idempotent + reversible.
5. Assertions + categorised verifier.
6. Prober with JSONL log and connectivity events.
7. Session lifecycle service with telemetry events and idle clock.
8. Orphan/expiry reconciliation and `cleanup-labs`.
9. CLI: `scenario start|verify|destroy|status|list|lint`, `lab exec|ls`,
   `prober run`, `cleanup-labs`, `milestone`.
10. Tests: unit suite on the mock (57 tests) covering the four Phase 1 exit
    criteria; integration suite against real containerlab (skipped where
    unavailable, mandatory in CI).

**Exit criterion status.** All four §38 cases hold on the mock
(`tests/unit/test_verification.py`) and `northstar milestone --provider mock`
passes all 12 steps. The same milestone runs against real FRR via
`northstar milestone --provider containerlab`; it has not yet been executed
on a host with docker + containerlab (see README "Status").

## 10. What is deliberately not built yet

FastAPI/WebSocket terminal, frontend, LLM characters beyond the abstractions,
assessment scoring, docker-compose. Spec §48: nothing above the lab loop
until the loop is proven on real containers.
