"""Risk-parity position sizing.

The whole point of this project: when the source account opens a trade risking
X% of its equity, the target account must open a trade that also risks X% of
*its own* equity -- not the same lot size, not a naive equity ratio, but a
size derived from the target's stop-loss distance and its own instrument
specs (which can differ between brokers).
"""
from __future__ import annotations

import math

from .models import SymbolSpec, TradeSignal


class RiskEngineError(ValueError):
    """Raised when a signal cannot be safely translated into a target volume."""


def risk_amount(equity: float, risk_pct: float) -> float:
    if equity <= 0:
        raise RiskEngineError(f"non-positive equity: {equity}")
    if risk_pct <= 0:
        raise RiskEngineError(f"non-positive risk_pct: {risk_pct}")
    return equity * (risk_pct / 100.0)


def stop_distance(entry_price: float, stop_loss_price: float) -> float:
    distance = abs(entry_price - stop_loss_price)
    if distance <= 0:
        raise RiskEngineError(
            "stop-loss distance is zero: cannot size a risk-parity trade "
            "without a stop-loss on the source trade"
        )
    return distance


def value_per_lot_at_risk(spec: SymbolSpec, sl_distance_price: float) -> float:
    """Account-currency loss for a 1.0-lot position if price moves sl_distance_price."""
    ticks = sl_distance_price / spec.tick_size
    return ticks * spec.tick_value


def round_to_step(volume: float, spec: SymbolSpec) -> float:
    steps = math.floor(volume / spec.volume_step + 1e-9)
    rounded = steps * spec.volume_step
    return round(rounded, 8)


def clamp(volume: float, spec: SymbolSpec) -> float:
    return max(spec.volume_min, min(volume, spec.volume_max))


def compute_target_volume(
    signal: TradeSignal,
    target_equity: float,
    target_risk_pct: float,
    target_spec: SymbolSpec,
) -> float:
    """Return the lot size to open on the target account for the same % risk.

    Raises RiskEngineError if the resulting size would be zero (rounds below
    the broker's minimum volume) -- callers must treat that as "skip this
    trade", never silently round up past the intended risk.
    """
    risk_amt = risk_amount(target_equity, target_risk_pct)
    sl_dist = stop_distance(signal.entry_price, signal.stop_loss)
    per_lot_risk = value_per_lot_at_risk(target_spec, sl_dist)

    if per_lot_risk <= 0:
        raise RiskEngineError(
            f"non-positive per-lot risk for {target_spec.symbol}: {per_lot_risk}"
        )

    raw_volume = risk_amt / per_lot_risk
    volume = round_to_step(raw_volume, target_spec)

    if volume < target_spec.volume_min:
        raise RiskEngineError(
            f"computed volume {raw_volume:.4f} rounds below broker minimum "
            f"{target_spec.volume_min} for {target_spec.symbol}; skipping trade "
            f"rather than over-risking the account"
        )

    return clamp(volume, target_spec)
