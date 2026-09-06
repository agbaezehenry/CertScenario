---
id: incident-response-guide
title: Incident Response Guide
owner: Infrastructure Engineering
last_reviewed: 2026-06-20
---

# Incident Response Guide

## Severity definitions

| Severity | Meaning                                        | Cadence                                   |
|----------|------------------------------------------------|-------------------------------------------|
| SEV-1    | Company-wide outage or revenue impact          | Escalate within **15 minutes**            |
| SEV-2    | A site or major service is down                | Manager update every **30 minutes**       |
| SEV-3    | Degraded, workaround exists                    | Daily update                              |

## DRI responsibilities

1. Acknowledge the incident in the ticket.
2. Establish blast radius: who and what is affected.
3. Post status updates on cadence in `#network-ops` or to your manager.
4. Escalate to a senior engineer if you have no working hypothesis after 45 minutes.
5. Record the resolution in the ticket.
6. Submit a postmortem within one business day for SEV-1/SEV-2.

## Postmortem template

Impact, Timeline, Root Cause, Resolution, Contributing Factors, Preventative Actions.
