"""Continuous live-market scanning for SMC setups, independent of any open
or copied trade.

Watches a configured symbol watchlist (H1 candles pushed by the EA, see
mql/README.md) for the standard SMC entry setup -- a fresh Order Block
right after the Break of Structure that created it -- and alerts
(Telegram/email) with a concrete entry/SL/TP suggestion. Every detection
is also recorded in `market_patterns`, independent of whether the user
acts on it: that's the dataset that lets "how performant is this pattern
actually" get answered later, once enough of them have played out.

Deliberately narrow for a first pass: only Order Block + BOS gets a
concrete entry/SL/TP. Bare FVGs and liquidity grabs are detected by
smc_analysis but not (yet) turned into standalone setups here -- there's
no single agreed entry rule for those without more input from real trades.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import EngineConfig
from .models import Candle, Side
from .notifier import Notifier
from .smc_analysis import Direction, OrderBlock, analyze_entry_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketSetup:
    symbol: str
    direction: Direction
    order_block_time: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float


def _order_block_setup(symbol: str, ob: OrderBlock, candles: list[Candle], rr_ratio: float) -> MarketSetup:
    ob_range = ob.high - ob.low
    # A small buffer beyond the block itself, rather than a fixed pip
    # amount -- keeps this symbol-agnostic (no tick size needed here).
    buffer = ob_range * 0.1
    entry = (ob.high + ob.low) / 2.0

    if ob.direction == Direction.BULLISH:
        stop_loss = ob.low - buffer
        take_profit = entry + rr_ratio * (entry - stop_loss)
    else:
        stop_loss = ob.high + buffer
        take_profit = entry - rr_ratio * (stop_loss - entry)

    return MarketSetup(
        symbol=symbol,
        direction=ob.direction,
        order_block_time=candles[ob.index].time,
        entry_price=round(entry, 6),
        stop_loss=round(stop_loss, 6),
        take_profit=round(take_profit, 6),
        rr_ratio=rr_ratio,
    )


def find_setups(symbol: str, candles: list[Candle], rr_ratio: float = 2.0) -> list[MarketSetup]:
    """Every Order Block currently visible in `candles`, each with an
    entry/SL/TP already computed. Callers dedupe against what's already
    been recorded/alerted (see db.insert_market_pattern) -- this returns
    all of them, old ones included, every time it's called."""
    context = analyze_entry_context(candles)
    return [_order_block_setup(symbol, ob, candles, rr_ratio) for ob in context.order_blocks]


def setup_side(setup: MarketSetup) -> Side:
    return Side.BUY if setup.direction == Direction.BULLISH else Side.SELL


PENDING = "PENDING"   # price hasn't reached the entry zone yet
ACTIVE = "ACTIVE"     # entry touched, watching for TP/SL
HIT_TP = "HIT_TP"
HIT_SL = "HIT_SL"


def _resolve_status(direction: Direction, entry: float, stop_loss: float, take_profit: float,
                     status: str, candles_after: list[Candle]) -> str:
    """Walks candles_after chronologically, simulating the setup as a
    limit-style entry: PENDING until price trades into the entry zone,
    then ACTIVE until SL or TP is hit. If a single candle's range covers
    both SL and TP (only possible with OHLC, no intrabar order), SL wins
    -- the conservative assumption, standard in simplified backtesting.
    """
    for c in candles_after:
        if status == PENDING:
            triggered = (c.low <= entry) if direction == Direction.BULLISH else (c.high >= entry)
            if not triggered:
                continue
            status = ACTIVE

        if direction == Direction.BULLISH:
            hit_sl = c.low <= stop_loss
            hit_tp = c.high >= take_profit
        else:
            hit_sl = c.high >= stop_loss
            hit_tp = c.low <= take_profit

        if hit_sl:
            return HIT_SL
        if hit_tp:
            return HIT_TP

    return status


def _find_index_by_time(candles: list[Candle], time: str) -> int | None:
    for i, c in enumerate(candles):
        if c.time == time:
            return i
    return None


def resolve_patterns_for_symbol(conn, symbol: str, candles: list[Candle],
                                 notifier: Notifier | None) -> None:
    """Re-checks every still-open (PENDING/ACTIVE) setup for `symbol`
    against the candles that have arrived since it was last checked, and
    updates its status. A setup whose last-checked point has fallen out of
    the (rolling, ScanCandleCount-wide) snapshot window before resolving
    is marked EXPIRED rather than guessed at -- see db.py's schema comment.
    """
    for row in db.list_unresolved_patterns(conn, symbol):
        idx = _find_index_by_time(candles, row["last_checked_time"])
        if idx is None:
            db.update_pattern_status(
                conn, row["id"], status="EXPIRED",
                last_checked_time=row["last_checked_time"],
            )
            continue

        candles_after = candles[idx + 1:]
        if not candles_after:
            continue  # nothing new since the last check

        new_status = _resolve_status(
            Direction(row["direction"]), row["entry_price"], row["stop_loss"],
            row["take_profit"], row["status"], candles_after,
        )
        resolved = new_status in (HIT_TP, HIT_SL)
        db.update_pattern_status(
            conn, row["id"], status=new_status,
            last_checked_time=candles_after[-1].time, resolved=resolved,
        )

        if resolved:
            logger.info("market setup resolved: %s #%s -> %s", symbol, row["id"], new_status)
            if notifier is not None:
                outcome = "TP atteint" if new_status == HIT_TP else "SL touché"
                notifier.notify(
                    f"Sentinel : setup {symbol} résolu ({outcome})",
                    f"{symbol} {row['direction']} détecté le {row['order_block_time']} : {outcome}.\n"
                    f"Entrée {row['entry_price']} | SL {row['stop_loss']} | TP {row['take_profit']}",
                    kind=f"market_resolution_{symbol}",
                )


def read_market_candles(path: Path) -> list[Candle]:
    raw = json.loads(path.read_text())
    return [
        Candle(
            time=str(c["time"]), open=float(c["open"]), high=float(c["high"]),
            low=float(c["low"]), close=float(c["close"]),
        )
        for c in raw.get("candles", [])
    ]


def scan_and_notify(cfg: EngineConfig, conn, notifier: Notifier | None) -> int:
    """Scans every configured watchlist symbol once. Returns how many
    genuinely NEW setups were found (already-seen ones are silently
    deduped by db.insert_market_pattern's UNIQUE constraint)."""
    if not cfg.market_scan.enabled:
        return 0

    market_dir = cfg.market_scan.market_files_dir or (cfg.source.files_dir / "market")
    new_count = 0

    for symbol in cfg.market_scan.symbols:
        path = market_dir / f"{symbol}.json"
        if not path.exists():
            continue

        try:
            candles = read_market_candles(path)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
            logger.warning("could not read market snapshot %s: %s", path, exc)
            continue

        if len(candles) < 5:
            continue

        resolve_patterns_for_symbol(conn, symbol, candles, notifier)

        for setup in find_setups(symbol, candles, cfg.market_scan.rr_ratio):
            inserted = db.insert_market_pattern(
                conn,
                symbol=setup.symbol, pattern_type="ORDER_BLOCK_BOS",
                direction=setup.direction.value, order_block_time=setup.order_block_time,
                entry_price=setup.entry_price, stop_loss=setup.stop_loss,
                take_profit=setup.take_profit, rr_ratio=setup.rr_ratio,
            )
            if not inserted:
                continue

            new_count += 1
            logger.info("new market setup: %s %s @ %s", symbol, setup.direction.value, setup.entry_price)
            if notifier is not None:
                side = setup_side(setup).value
                notifier.notify(
                    f"Sentinel : setup détecté {symbol}",
                    f"{side} {symbol} (Order Block + BOS)\n"
                    f"Entrée : {setup.entry_price}\n"
                    f"SL : {setup.stop_loss}\n"
                    f"TP : {setup.take_profit} (R:R {setup.rr_ratio:g})\n\n"
                    f"Alerte seule -- Sentinel n'ouvre rien automatiquement.",
                    kind=f"market_setup_{symbol}",
                )

    return new_count
