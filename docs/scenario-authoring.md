# Scenario authoring

A scenario is a directory:

```
scenarios/<id>/
├── scenario.yaml
├── topology.clab.yml        containerlab template; `name: __LAB_NAME__`
├── configs/healthy/         daemons + <node>/frr.conf per router
├── configs/broken/          healthy with the fault applied (ground truth)
├── wiki/*.md                front-matter: id, title, owner, last_reviewed
└── seed/
    ├── changes.yaml         change log (real change + decoys)
    ├── characters.yaml      persona / knows / does_not_know / incorrect_beliefs / triggers
    └── monitoring.yaml      tile ordering
```

## scenario.yaml

```yaml
id: INC-1042
title: ...
difficulty: junior
role: {title, team, manager}
incident: {severity, reported_by, reported_at, affected, description}

topology:
  template: topology.clab.yml
  configs: configs/healthy
  devices:                # learner-facing name -> containerlab node
    - {name: AUS-RTR1, node: aus-rtr1, kind: router, site: Austin}
    - {name: AUS-CLIENT1, node: aus-client1, kind: host, site: Austin, address: 10.20.10.51}
  prober:
    node: prober
    vantages: {austin: {interface: eth1, address: 10.20.10.250}, hq: {...}}

faults:                   # applied at runtime via the provider; must be reversible
  - {id: FAULT-1, type: configuration, device: AUS-RTR1, action: remove_ospf_network, target: "10.20.10.0/24", area: 0}

decoys:
  - {type: change_log_entry, id: CHG-8817, device: HQ-RTR1, description: ...}

characters: [maya, carlos, priya]
objectives: [...]
skills: [{id: ccna.3.4.ospf, weight: 0.5}]

verification:             # every entry has a category
  - {id: austin-to-app, check: ping, from: AUS-CLIENT1, destination: 10.10.10.10, expect: success, category: resolution}
  - {id: hq-has-austin-prefix, check: route_exists, device: HQ-RTR1, prefix: "10.20.10.0/24", via: ospf, category: resolution}
  - {id: ospf-adjacency-full, check: ospf_neighbor, device: AUS-RTR1, neighbor: HQ-RTR1, expect: Full, category: regression}
  - {id: decoy-acl-untouched, check: config_unchanged, device: HQ-RTR1, section: access-list, category: regression}
  - {id: no-redistribute-connected, check: config_absent, device: AUS-RTR1, pattern: "redistribute connected", category: quality, on_fail: partial_credit}

prober_matrix:
  - {id: austin->app, vantage: austin, destination: 10.10.10.10, label: "Austin -> HQ application"}

monitoring:
  - {id: austin-reachability, label: Austin site reachability, source: prober, pair: austin->app}
  - {id: aus-sw1, label: AUS-SW1, source: static, value: UP}

hidden:                   # never read by the character context builder
  root_cause: ...
  minimal_fix: {device: AUS-RTR1, lines: [router ospf, network 10.20.10.0/24 area 0]}
  minimum_config_lines_changed: 1
  forbidden_in_character_output: ["10.20.10.0/24", "network statement", ...]
  sledgehammers: [{pattern: "redistribute connected", score: partial}]
```

### Check types

| check | params | notes |
|---|---|---|
| `ping` | `from`, `destination`, `expect: success\|fail` | runs from a host or router container |
| `route_exists` | `device`, `prefix`, `via` (protocol), `expect: present\|absent` | parses `show ip route json` |
| `ospf_neighbor` | `device`, `neighbor`, `expect: Full` | any neighbor in that state |
| `interface` | `device`, `interface`, `expect: up\|down` | admin **and** oper |
| `config_present` / `config_absent` | `device`, `pattern` | line-anchored literal match on running-config |
| `config_unchanged` | `device`, `section` | compares to the post-fault baseline |
| `dns`, `service` | `source`, `name` / `url` | for later scenarios |
| `vlan` | — | always fails on FRR; placeholder |

### Categories

* `resolution` — must flip fail → pass. Required.
* `regression` — must hold at start **and** end. Failure blocks completion.
* `quality` — passing not required; `on_fail: partial_credit` affects score.

## Authoring checklist (spec §49 — "verification that does not verify")

Before a scenario ships, prove with `LAB_PROVIDER=containerlab`:

1. **Broken state:** every `resolution` check FAILS; every `regression` check PASSES.
   A resolution check that passes while the network is down is graded noise.
2. **Minimal fix:** everything passes.
3. **Each sledgehammer** in `hidden.sledgehammers`: resolution passes, the
   matching `quality` check fails.
4. **A plausible wrong move** (shut the wrong interface, revert the decoy):
   at least one `regression` check fails.
5. `configs/broken` equals `configs/healthy` minus the fault (unit-tested).
6. No string from `hidden.forbidden_in_character_output` appears anywhere in
   `seed/characters.yaml`, `seed/changes.yaml` or the wiki (unit-tested for
   characters; grep the rest).
7. Node count ≤ 6 routers+hosts.

`northstar milestone` performs 1–4 for INC-1042; copy `services/milestone.py`
for the next scenario and track hours spent — if scenario two does not take
sharply less time than scenario one, the abstraction needs work.

## Adding a fault type

Implement `FaultAction` in `backend/app/labs/faults.py` with `apply`,
`revert`, `is_applied` — all idempotent — and register it in `FAULT_ACTIONS`.
The mock must model whatever state your fault changes; if it can't, mark the
scenario `real-only` and rely on the integration job.
