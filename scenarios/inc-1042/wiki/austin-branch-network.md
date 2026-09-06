---
id: austin-branch-network
title: Austin Branch Network
owner: Infrastructure Engineering
last_reviewed: 2026-08-14
---

# Austin Branch Network

The Austin office is a single-router branch connected to HQ over a point-to-point
WAN transit link. Routing between HQ and Austin is OSPF area 0.

## Topology

```
              HQ-CLIENT        APP-SRV (10.10.10.10)
                   \              /
                    \            /
                      HQ-RTR1
                         |
                     WAN transit
                    10.255.0.0/30
                         |
                     AUS-RTR1
                         |
                     AUS-SW1
                    /       \
          AUS-CLIENT1    AUS-CLIENT2
```

## Devices

| Device     | Role             | Site   | Management |
|------------|------------------|--------|------------|
| HQ-RTR1    | Core/WAN router  | HQ     | vtysh      |
| AUS-RTR1   | Branch router    | Austin | vtysh      |
| AUS-SW1    | Access switch    | Austin | n/a        |
| APP-SRV    | Internal app     | HQ     | ssh        |

Router CLI is IOS-like (FRRouting). `show ip route`, `show ip ospf neighbor`,
`show running-config` and `configure terminal` behave as you expect. Exact keywords
differ from Cisco in places; knowing what to look at transfers, memorised syntax does not.

## Addressing

| Prefix          | Purpose                | Gateway     |
|-----------------|------------------------|-------------|
| 10.10.10.0/24   | HQ application subnet  | 10.10.10.1  |
| 10.10.20.0/24   | HQ user subnet         | 10.10.20.1  |
| 10.10.30.0/30   | NetOps monitoring link | 10.10.30.1  |
| 10.255.0.0/30   | WAN transit HQ<->Austin | .1 HQ / .2 Austin |
| 10.20.10.0/24   | Austin user subnet     | 10.20.10.1  |

## Routing

* OSPF process, single area 0.
* Every LAN and the WAN transit must be advertised on the router that owns it.
* No static routes are expected on either router.

## Relevant runbooks

* [OSPF Troubleshooting Runbook](ospf-troubleshooting-runbook)
* [Incident Response Guide](incident-response-guide)
