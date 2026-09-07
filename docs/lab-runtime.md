# Lab runtime

## Providers

`LAB_PROVIDER=containerlab` (real) or `mock` (dev/CI only). Selection is in
`app/labs/__init__.py:make_provider`.

### containerlab

Requirements on the host running the backend: Docker and
[containerlab](https://containerlab.dev) on `PATH` (Linux; on macOS use the
containerlab devcontainer/`clab` VM per their docs — Docker Desktop alone
cannot create the veth links).

Images: `quay.io/frrouting/frr:10.2.1` (routers) and `alpine:3.20`
(hosts, switch bridge, prober). No commercial images, ever (spec §7.1).

Per session:

```
<state_dir>/labs/<lab_id>/
├── topology.clab.yml     template with name=<lab_id>
├── configs/              copy of scenarios/<id>/configs/healthy
│   ├── daemons
│   ├── hq-rtr1/frr.conf  bind-mounted at /etc/frr/frr.conf
│   └── aus-rtr1/frr.conf
└── lab.json              LabInstance (device inventory)
```

Container names are `clab-<lab_id>-<node>`; every lab id starts with `ns-`,
which is how reconciliation tells ours from anything else on the daemon.

Commands:

* routers: `docker exec clab-<lab>-<node> vtysh -c <line> -c <line> ...`
* hosts:   `docker exec clab-<lab>-<node> sh -c "<command>"`
* ping:    `docker exec ... ping -c N -W 1 [-I <src>] <dst>` parsed by `labs/pingparse.py`

Every subprocess has `NORTHSTAR_EXEC_TIMEOUT_SECONDS` and
`NORTHSTAR_EXEC_MAX_OUTPUT_BYTES` applied (spec §28). A learner running
`ping -f` hits the cap, not the host.

Convergence: OSPF hello/dead defaults are 10/40 s. After `provision`, allow
~30 s before asserting connectivity; after a `no shutdown`, up to 40 s.
The milestone and integration tests sleep accordingly.

### mock

`SimulatedNetwork` in `app/labs/mock.py`. Reads the same template and
configs, models L2 segments, `network`-statement containment, adjacency,
prefix flooding, static routes, `redistribute connected`, interface shutdown,
and bidirectional forwarding. Renders FRR-shaped text and JSON. Persists per
lab to `<state_dir>/labs/<lab_id>/mock-state.json` so CLI processes share it.

Not modelled: ACL packet filtering (the decoy ACL is graded by config diff,
not behaviour), OSPF timers/areas beyond 0, MTU, BGP. The mock must never be
the only thing a grading path is proven on.

## Lifecycle

```
start  -> ScenarioSession saved (PROVISIONING)     <- crash-safe from here
       -> provider.provision (healthy)
       -> FaultInjector.apply (runtime, reversible)
       -> baseline running-configs captured (post-fault)
       -> ACTIVE, TICKET_OPENED
       -> Prober.start (1 Hz, dedicated container, JSONL)
learner exec/vtysh -> COMMAND_EXECUTED / CONFIG_CHANGED, idle clock touched
verify -> VerificationResult saved; VERIFICATION_PASSED -> RESOLVED
destroy -> prober stop, containerlab destroy, docker rm by label, container-minutes recorded
```

## Reconciliation (spec §29)

`northstar cleanup-labs [--dry-run]` and (Phase 2) backend startup call
`labs/reconcile.py`:

1. `provider.list_labs()` — from docker labels, so it works even if
   containerlab's own bookkeeping is gone.
2. Sessions in a live state from the store own their `lab_id`.
3. Running labs without an owner → orphans → destroyed.
4. Owned labs past the absolute (4 h) or idle (20 min) timeout → destroyed,
   session marked DESTROYED.

Sessions are persisted *before* provisioning, so a crash mid-deploy leaves a
PROVISIONING session that still owns its lab and is cleaned up by the idle
timeout, not mistaken for foreign state.

## Prober

Dedicated `prober` container with two legs and policy routing:

* `eth1` 10.20.10.250/24 on the Austin LAN (via AUS-SW1) → table 101 via 10.20.10.1
* `eth2` 10.10.30.2/30 on HQ-RTR1 → table 102 via 10.10.30.1

Each matrix pair pings from a vantage with `-I <address>`, once per second.
Samples: `{ts, src: "prober@austin", dst, result, rtt_ms, pair_id}` to
`<state_dir>/sessions/<id>/prober.jsonl`. A pair flipping ok→fail emits
`NETWORK_CONNECTIVITY_LOST`; fail→ok emits `..._RESTORED`. A pair that is
already down on the first cycle (the scenario's own symptom) is recorded as an
open outage without an event, so the learner's own blast radius is
distinguishable from the incident.

## Security notes for Phase 2/3

* The WebSocket terminal must authorise per message and bind a session token
  to one `lab_id`; device names from the client are validated against
  `provider.devices(lab_id)`.
* Labs must not share a Docker network with the backend or database.
* Run FRR containers without `--privileged`; the topology only needs
  `net.ipv4.ip_forward`.

## Development-environment quirks

* **macOS App Nap.** When the backend runs as a background process on macOS
  (for example under the desktop app's preview server), asyncio timers are
  throttled while the process is idle, so the prober logs far fewer than one
  sample per second. Samples carry real timestamps, so outage durations and
  blast-radius scoring stay correct; only sample density drops. Linux hosts
  are unaffected.
* **Idle timeout is real.** Twenty minutes without a terminal line, chat
  message, ticket update or page view destroys the lab. The dashboard shows
  why the previous session ended (`end_reason`).
