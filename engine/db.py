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

-- One row per copied trade's full lifecycle (vs. copy_log's one row per
-- event) -- the "enregistrer les positions pour après" record: this is
-- the dataset phase 2 pattern matching will train on.
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_account_id TEXT NOT NULL,
    source_ticket INTEGER NOT NULL,
    target_account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    target_volume REAL NOT NULL,
    risk_pct_intended REAL NOT NULL,
    rr_ratio REAL,
    risk_deviation_pct REAL NOT NULL,
    quality_score REAL NOT NULL,
    -- SMC entry-context (engine/smc_analysis.py): quick boolean flags for
    -- filtering, plus the full detected context as JSON for later reuse.
    has_fvg INTEGER NOT NULL DEFAULT 0,
    has_liquidity_grab INTEGER NOT NULL DEFAULT 0,
    has_bos INTEGER NOT NULL DEFAULT 0,
    has_order_block INTEGER NOT NULL DEFAULT 0,
    context_json TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    opened_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
    UNIQUE(source_account_id, source_ticket, target_account_id)
);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
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


def open_position(
    conn: sqlite3.Connection,
    *,
    source_account_id: str,
    source_ticket: int,
    target_account_id: str,
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    target_volume: float,
    risk_pct_intended: float,
    rr_ratio: float | None,
    risk_deviation_pct: float,
    quality_score: float,
    has_fvg: bool = False,
    has_liquidity_grab: bool = False,
    has_bos: bool = False,
    has_order_block: bool = False,
    context_json: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO positions (
            source_account_id, source_ticket, target_account_id, symbol, side,
            entry_price, stop_loss, take_profit, target_volume,
            risk_pct_intended, rr_ratio, risk_deviation_pct, quality_score,
            has_fvg, has_liquidity_grab, has_bos, has_order_block, context_json,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            source_account_id, source_ticket, target_account_id, symbol, side,
            entry_price, stop_loss, take_profit, target_volume,
            risk_pct_intended, rr_ratio, risk_deviation_pct, quality_score,
            int(has_fvg), int(has_liquidity_grab), int(has_bos), int(has_order_block),
            context_json,
        ),
    )
    conn.commit()


def close_position(
    conn: sqlite3.Connection,
    *,
    source_account_id: str,
    source_ticket: int,
    target_account_id: str,
) -> None:
    conn.execute(
        """
        UPDATE positions SET status = 'CLOSED', closed_at = datetime('now')
        WHERE source_account_id = ? AND source_ticket = ? AND target_account_id = ?
        """,
        (source_account_id, source_ticket, target_account_id),
    )
    conn.commit()


def list_positions(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM positions ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
