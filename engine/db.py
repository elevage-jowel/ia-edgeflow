"""SQLite trade log.

Every copy attempt is logged, successful or not -- this is the audit trail
you'll want when a broker disputes a fill, and later it's the raw dataset
phase 2 (pattern recognition) trains on. SQLite is enough for a single-VPS
MVP; swap for Postgres (see deploy/hostinger-setup.md) once volume or the
phase-2 workload calls for it.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS copy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_account_id TEXT NOT NULL,
    source_ticket INTEGER NOT NULL,
    target_account_id TEXT NOT NULL,
    event TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    source_volume REAL,
    target_volume REAL,
    entry_price REAL,
    stop_loss REAL,
    take_profit REAL,
    status TEXT NOT NULL,
    detail TEXT
);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


def log_copy(
    conn: sqlite3.Connection,
    *,
    source_account_id: str,
    source_ticket: int,
    target_account_id: str,
    event: str,
    symbol: str,
    side: str,
    source_volume: float | None,
    target_volume: float | None,
    entry_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    status: str,
    detail: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO copy_log (
            source_account_id, source_ticket, target_account_id, event,
            symbol, side, source_volume, target_volume, entry_price,
            stop_loss, take_profit, status, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_account_id, source_ticket, target_account_id, event,
            symbol, side, source_volume, target_volume, entry_price,
            stop_loss, take_profit, status, detail,
        ),
    )
    conn.commit()
