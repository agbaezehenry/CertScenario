---
id: ospf-troubleshooting-runbook
title: OSPF Troubleshooting Runbook
owner: Infrastructure Engineering
last_reviewed: 2026-07-02
---

# OSPF Troubleshooting Runbook

Work through the steps in order. Do not skip a step because the previous one
looked healthy; each one checks a different thing.

## 1. Confirm the symptom and its direction

Reproduce from an affected host. Note whether the failure is one-way. A ping
that fails can mean the request never arrived **or** the reply never came back.

## 2. Check interface state on the path

```
show interface brief
show interface <name>
```

All transit interfaces should be up/up with the expected address.

## 3. Check neighbor state

```
show ip ospf neighbor
```

Neighbors should be `Full`. If a neighbor is missing or stuck in `Init`/`ExStart`,
investigate MTU, area, timers, network type and authentication.

**A Full adjacency only means the routers are talking. It does not mean any
particular prefix is being exchanged.**

## 4. Verify the prefixes are actually advertised

On each router, confirm the interfaces you expect to be in OSPF are:

```
show ip ospf interface
show running-config   (section: router ospf)
```

Every LAN that must be reachable from the rest of the domain needs to be
covered by a `network` statement (or `ip ospf area` on the interface).

## 5. Check the routing table on **both** ends

```
show ip route
show ip route ospf
```

Look for the far-side prefix on each router. Connectivity is bidirectional:
a prefix present on one router and absent on the other is a broken path even
though half of it works.

## 6. Correlate with recent changes

Search the change log for the devices on the path before changing anything.

## 7. Fix minimally, then verify

Prefer the smallest configuration change that restores the intended design.
Re-run steps 3 and 5 after the change, then re-test from the affected host.
