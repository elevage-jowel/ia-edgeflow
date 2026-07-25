"""Reads the target account's live equity, published by its own EA.

Risk-parity sizing needs the *target* account's equity, not the source's --
two accounts of different sizes must risk the same %, which means different
money amounts. The CommandExecutor EA writes `<target_files_dir>/heartbeat.json`
on a timer (see mql/README.md); we treat a stale or missing heartbeat as
"we don't know this account's state" and refuse to size a trade for it
rather than guess.

Staleness is judged from the file's own mtime, not the `timestamp` field
inside it: that field is MQL's TimeCurrent(), which is broker server time
(often GMT+2/3), not this host's clock -- comparing it to time.time() would
misfire. The engine and the Wine-hosted terminal share the same filesystem
and OS clock, so mtime is skew-free.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


class HeartbeatError(RuntimeError):
    pass


@dataclass(frozen=True)
class Heartbeat:
    equity: float
    balance: float
    updated_at: float


def read_heartbeat(target_files_dir: Path, max_age_seconds: float = 15.0) -> Heartbeat:
    path = target_files_dir / "heartbeat.json"
    if not path.exists():
        raise HeartbeatError(f"no heartbeat file at {path}; is the target EA running?")

    raw = json.loads(path.read_text())
    mtime = path.stat().st_mtime
    hb = Heartbeat(
        equity=float(raw["equity"]),
        balance=float(raw["balance"]),
        updated_at=mtime,
    )

    age = time.time() - hb.updated_at
    if age > max_age_seconds:
        raise HeartbeatError(
            f"heartbeat for {target_files_dir} is {age:.1f}s old "
            f"(max {max_age_seconds}s); target terminal may be disconnected"
        )
    return hb
