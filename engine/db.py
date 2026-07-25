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
    -- Volumes as opened, kept fixed for the position's whole lifetime even
    -- across partial closes -- PARTIAL_CLOSE handling (engine/main.py)
    -- always derives the target's proportional remaining size from these
    -- originals, never from a running total, so rounding never compounds.
    source_volume REAL NOT NULL,
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
    -- Outcome, filled in by close_position(): without this, nothing can
    -- ever learn which of the patterns detected at entry (has_fvg etc.)
    -- actually led to a good trade.
    close_price REAL,
    -- Source account's own profit/loss for this trade, in its account
    -- currency. Informational, NOT the target's P&L (the target's actual
    -- result depends on its own volume, which this figure doesn't reflect).
    source_profit REAL,
    UNIQUE(source_account_id, source_ticket, target_account_id)
);

CREATE INDEX IF NOT EXISTS idx_copy_log_ticket ON copy_log(source_account_id, source_ticket);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);

-- Every SMC setup the live market scanner (engine/market_scanner.py)
-- detects, whether or not the user acts on it -- independent of copied
-- trades entirely. This is the dataset "how good is this pattern really"
-- gets answered from, once enough of them have resolved.
--
-- status: PENDING (price hasn't reached the entry zone yet) -> ACTIVE
-- (entry touched, watching for TP/SL) -> HIT_TP / HIT_SL. EXPIRED means
-- the rolling candle window moved past what we'd last checked before it
-- resolved -- an honest "we lost track", not counted as a win or a loss.
CREATE TABLE IF NOT EXISTS market_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    symbol TEXT NOT NULL,
    pattern_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_block_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    rr_ratio REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    last_checked_time TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(symbol, pattern_type, order_block_time)
);

CREATE INDEX IF NOT EXISTS idx_market_patterns_symbol ON market_patterns(symbol);
CREATE INDEX IF NOT EXISTS idx_market_patterns_status ON market_patterns(status);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # WAL mode lets the dashboard read (`app.py`) while the engine keeps
    # writing without hitting "database is locked"; busy_timeout retries
    # briefly instead of failing outright on the rare write/write clash.
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
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
    source_volume: float,
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
) -> bool:
    """Returns False if a position already exists for this
    (source_account_id, source_ticket, target_account_id) -- a duplicate
    OPEN signal (e.g. a re-processed file) is ignored rather than
    overwriting an existing record, which would reset opened_at and could
    silently reopen an already-CLOSED position back to OPEN."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO positions (
            source_account_id, source_ticket, target_account_id, symbol, side,
            entry_price, stop_loss, take_profit, source_volume, target_volume,
            risk_pct_intended, rr_ratio, risk_deviation_pct, quality_score,
            has_fvg, has_liquidity_grab, has_bos, has_order_block, context_json,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            source_account_id, source_ticket, target_account_id, symbol, side,
            entry_price, stop_loss, take_profit, source_volume, target_volume,
            risk_pct_intended, rr_ratio, risk_deviation_pct, quality_score,
            int(has_fvg), int(has_liquidity_grab), int(has_bos), int(has_order_block),
            context_json,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_position_volumes(
    conn: sqlite3.Connection,
    *,
    source_account_id: str,
    source_ticket: int,
    target_account_id: str,
) -> tuple[float, float] | None:
    """Returns (source_volume, target_volume) as originally opened, for an
    OPEN position -- the basis PARTIAL_CLOSE handling uses to compute the
    target's proportional remaining size. None if there's no open position
    on record (e.g. it predates this engine, or the OPEN signal was missed)."""
    row = conn.execute(
        """
        SELECT source_volume, target_volume FROM positions
        WHERE source_account_id = ? AND source_ticket = ? AND target_account_id = ?
          AND status = 'OPEN'
        """,
        (source_account_id, source_ticket, target_account_id),
    ).fetchone()
    return (row[0], row[1]) if row else None


def close_position(
    conn: sqlite3.Connection,
    *,
    source_account_id: str,
    source_ticket: int,
    target_account_id: str,
    close_price: float | None = None,
    source_profit: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE positions
        SET status = 'CLOSED', closed_at = datetime('now'),
            close_price = ?, source_profit = ?
        WHERE source_account_id = ? AND source_ticket = ? AND target_account_id = ?
        """,
        (close_price, source_profit, source_account_id, source_ticket, target_account_id),
    )
    conn.commit()


def list_positions(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM positions ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def insert_market_pattern(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    pattern_type: str,
    direction: str,
    order_block_time: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    rr_ratio: float,
) -> bool:
    """Returns True if this is a NEWLY seen pattern (the caller should
    alert), False if it was already recorded -- the UNIQUE constraint on
    (symbol, pattern_type, order_block_time) means the same formation
    scanned again on a later poll is silently a no-op, not a re-alert."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO market_patterns (
            symbol, pattern_type, direction, order_block_time,
            entry_price, stop_loss, take_profit, rr_ratio, last_checked_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, pattern_type, direction, order_block_time,
         entry_price, stop_loss, take_profit, rr_ratio, order_block_time),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_market_patterns(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM market_patterns ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def list_unresolved_patterns(conn: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT * FROM market_patterns
        WHERE symbol = ? AND status IN ('PENDING', 'ACTIVE')
        """,
        (symbol,),
    ).fetchall()


def update_pattern_status(
    conn: sqlite3.Connection,
    pattern_id: int,
    *,
    status: str,
    last_checked_time: str,
    resolved: bool = False,
) -> None:
    conn.execute(
        """
        UPDATE market_patterns
        SET status = ?, last_checked_time = ?,
            resolved_at = CASE WHEN ? THEN datetime('now') ELSE resolved_at END
        WHERE id = ?
        """,
        (status, last_checked_time, resolved, pattern_id),
    )
    conn.commit()


def pattern_performance(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Win rate per (symbol, direction) among RESOLVED setups (HIT_TP/HIT_SL
    only -- PENDING/ACTIVE/EXPIRED don't count toward it either way)."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT symbol, direction,
               COUNT(*) AS resolved_count,
               SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) AS wins,
               ROUND(100.0 * SUM(CASE WHEN status = 'HIT_TP' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct
        FROM market_patterns
        WHERE status IN ('HIT_TP', 'HIT_SL')
        GROUP BY symbol, direction
        ORDER BY symbol, direction
        """
    ).fetchall()
