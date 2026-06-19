"""Canonical-symbol <-> source-native resolution (the §6 symbol map)."""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Tuple


@functools.lru_cache(maxsize=1)
def _map() -> dict:
    path = Path(__file__).with_name("symbol_map.json")
    return json.loads(path.read_text(encoding="utf-8"))


def display_name(canonical: str) -> str:
    idx = _map().get("indices", {}).get(canonical.upper())
    return idx["display"] if idx else canonical.upper()


def resolve(canonical: str, source: str) -> str:
    """canonical id -> native symbol for 'stooq' or 'yahoo'."""
    m = _map()
    cid = canonical.upper()
    idx = m.get("indices", {}).get(cid)
    if idx:
        return idx[source]
    rule = m.get("equity_rule", {})
    if source == "stooq":
        return cid.lower() + rule.get("stooq_suffix", ".us")
    return cid  # yahoo bare uppercase
