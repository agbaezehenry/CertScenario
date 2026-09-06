"""Shared ping-output parsing for busybox (Alpine) and iputils formats."""

from __future__ import annotations

import re

_STATS = re.compile(r"(\d+) packets transmitted, (\d+)(?: packets)? received")
_RTT = re.compile(r"time[=<]([\d.]+) ?ms")


def parse_ping(output: str) -> tuple[bool, float | None]:
    sent = recv = 0
    m = _STATS.search(output)
    if m:
        sent, recv = int(m.group(1)), int(m.group(2))
    rtts = [float(x) for x in _RTT.findall(output)]
    rtt = sum(rtts) / len(rtts) if rtts else None
    return (sent > 0 and recv == sent), rtt
