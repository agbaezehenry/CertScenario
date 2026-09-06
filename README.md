# Northstar Technologies

An AI-powered network engineer simulation platform. The learner joins a
fictional company as an Associate Network Engineer and works a real incident
on a real (containerised) network. AI characters supply context, incomplete
information and wrong beliefs; a deterministic verification engine decides
whether the network is actually fixed.

Build spec: [northstar-build-spec.md](northstar-build-spec.md).
Architecture: [docs/architecture.md](docs/architecture.md).

## Status

| Phase | State |
|---|---|
| 0 — architecture, domain model, schema, interfaces | done — `docs/` |
| 1 — real lab runtime (containerlab + FRR, faults, verification, prober, lifecycle, reconciliation, CLI) | code complete; 12-step milestone passes on the mock; **not yet executed on real containerlab** (needs a Linux host with docker + containerlab) |
| 2 — backend API / persistence | not started |
| 3 — learner workspace UI | not started |
| 4 — AI characters | abstractions only (context builder, validator, leak-test fixtures) |
| 5–7 | not started |

## Quick start (mock provider, no Docker needed)

```bash
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
source .venv/bin/activate
pytest                                  # 57 unit tests
northstar milestone --provider mock     # the 12-step Phase 1 loop
```

Then play the incident from the CLI:

```bash
northstar scenario start INC-1042 --provider mock
export NORTHSTAR_SESSION=<id printed above> LAB_PROVIDER=mock

northstar lab exec AUS-RTR1 "show ip ospf neighbor"      # Full — the trap
northstar lab exec AUS-RTR1 "show ip route"              # HQ prefix present
northstar lab exec HQ-RTR1  "show ip route ospf"         # Austin prefix missing
northstar lab exec AUS-CLIENT1 "ping -c 1 10.10.10.10"   # fails
northstar scenario verify                                # resolution FAIL, regression PASS

northstar lab exec AUS-RTR1 "configure terminal; router ospf; network 10.20.10.0/24 area 0; end"
northstar scenario verify                                # all PASS
northstar scenario destroy
northstar cleanup-labs --dry-run
```

## Real lab (containerlab)

On a Linux host with Docker and [containerlab](https://containerlab.dev):

```bash
export LAB_PROVIDER=containerlab
northstar milestone                     # provisions FRR, proves the 4 exit criteria, destroys
pytest -m integration tests/integration # the §40.2 suite
```

Images: `quay.io/frrouting/frr:10.2.1`, `alpine:3.20`. No commercial NOS
images are used or supported (spec §7.1).

## Layout

```
backend/app/     modular monolith (labs, scenarios, verification, probing, events, characters, services, cli)
scenarios/       data-driven scenarios; inc-1042 is the vertical slice
tests/           unit (mock) · integration (real containerlab) · characters (leak gate)
docs/            architecture · scenario-authoring · lab-runtime
```

## Principles enforced in code

* An LLM never decides whether the network is fixed (`verification/`).
* Terminal output comes from real containers; the mock is a network model,
  and CI must run the integration suite against the real provider.
* Characters structurally cannot see the root cause: the context builder only
  accepts `ScenarioPublicView`, which has no `hidden` field
  (`tests/unit/test_character_context.py`).
* Resolution, regression and quality checks are distinct categories.
* Labs are ephemeral and reconciled: `northstar cleanup-labs`.
