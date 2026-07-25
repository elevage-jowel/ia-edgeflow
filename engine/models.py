"""Core data structures shared across the copier engine."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalEvent(str, Enum):
    OPEN = "OPEN"
    MODIFY = "MODIFY"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class TradeSignal:
    """A trade event emitted by the SignalPublisher EA on the source account."""

    source_account_id: str
    source_ticket: int
    event: SignalEvent
    symbol: str
    side: Side
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    source_equity: float
    timestamp: str


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
    volume: float
    stop_loss: float
    take_profit: float
    target_ticket: Optional[int] = None
