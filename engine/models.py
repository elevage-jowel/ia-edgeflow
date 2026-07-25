"""Core data structures shared across the copier engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalEvent(str, Enum):
    OPEN = "OPEN"
    MODIFY = "MODIFY"
    CLOSE = "CLOSE"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"


@dataclass(frozen=True)
class Candle:
    """One H1 bar, as sent by the EA for SMC entry-context analysis."""

    time: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class TradeSignal:
    """A trade event emitted by the SignalPublisher EA on the source account."""

    source_account_id: str
    source_ticket: int
    event: SignalEvent
    symbol: str
    side: Side
    # For PARTIAL_CLOSE, this is the volume STILL REMAINING on the source
    # after the partial close (not the amount closed) -- main.py derives
    # the target's proportional remaining size from it.
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    source_equity: float
    timestamp: str
    # Recent H1 candles leading up to the entry, only populated on OPEN
    # events -- the raw material for SMC entry-context analysis.
    context_candles: list[Candle] = field(default_factory=list)
    # Only populated on CLOSE events -- the outcome of the trade, without
    # which nothing downstream can ever learn which patterns were good
    # ones. close_profit is the SOURCE account's profit/loss in its own
    # account currency (informational only: the target's actual P&L
    # depends on its own volume and is not derived from this).
    close_price: Optional[float] = None
    close_profit: Optional[float] = None


@dataclass(frozen=True)
class SymbolSpec:
    """Broker/symbol-specific figures needed to convert a price distance into money."""

    symbol: str
    contract_size: float
    tick_size: float
    tick_value: float  # account-currency value of one tick move, per 1.0 lot
    volume_step: float
    volume_min: float
    volume_max: float


@dataclass(frozen=True)
class CopyCommand:
    """An order instruction written for the CommandExecutor EA on a target account."""

    target_account_id: str
    source_ticket: int
    event: SignalEvent
    symbol: str
    side: Side
    # For PARTIAL_CLOSE, this is the DESIRED REMAINING volume on the target
    # (the executor EA closes currentVolume - volume, not `volume` itself).
    volume: float
    stop_loss: float
    take_profit: float
    target_ticket: Optional[int] = None
