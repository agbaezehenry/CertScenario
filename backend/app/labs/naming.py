"""Lab/session naming. Everything Northstar creates is identifiable by prefix."""

from __future__ import annotations

import secrets

LAB_PREFIX = "ns-"


def new_lab_id() -> str:
    return f"{LAB_PREFIX}{secrets.token_hex(4)}"


def is_northstar_lab(name: str) -> bool:
    return name.startswith(LAB_PREFIX)


def container_name(lab_id: str, node: str) -> str:
    """containerlab names containers ``clab-<lab>-<node>``."""
    return f"clab-{lab_id}-{node}"
