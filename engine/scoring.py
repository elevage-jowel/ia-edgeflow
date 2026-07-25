"""Position quality scoring, computed when a copied trade opens.

Combines two independent signals into one 0-100 score:
  - reward:risk ratio (distance to TP vs distance to SL) -- the classic
    trade-quality measure, independent of account size.
  - risk deviation -- how far the risk *actually* taken (after rounding the
    volume to the broker's step/min/max) drifts from the intended risk_pct.
    round_to_step() in risk_engine only ever rounds down, so this is never a
    safety concern -- it just flags symbols where a broker's minimum lot
    size is systematically under-cutting the intended risk.

The score is descriptive, not prescriptive: it never blocks or resizes a
trade. It's attached to the position record for later review and, in
phase 2, as a feature for pattern matching.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import SymbolSpec, TradeSignal
from .risk_engine import risk_amount, stop_distance, value_per_lot_at_risk

# A reward:risk ratio at or above this maxes out the RR component of the score.
RR_RATIO_CAP = 3.0
RR_WEIGHT = 70.0
DEVIATION_WEIGHT = 30.0
# A risk deviation at or beyond this zeroes out the deviation component.
DEVIATION_FLOOR_PCT = 50.0


@dataclass(frozen=True)
class PositionScore:
    rr_ratio: float | None  # None when the source trade has no take-profit
    risk_deviation_pct: float
    quality_score: float


def _rr_ratio(signal: TradeSignal) -> float | None:
    if signal.take_profit == 0:
        return None
    sl_dist = stop_distance(signal.entry_price, signal.stop_loss)
    tp_dist = abs(signal.take_profit - signal.entry_price)
    return tp_dist / sl_dist


def _risk_deviation_pct(
    signal: TradeSignal,
    target_equity: float,
    target_risk_pct: float,
    target_spec: SymbolSpec,
    actual_volume: float,
) -> float:
    intended = risk_amount(target_equity, target_risk_pct)
    sl_dist = stop_distance(signal.entry_price, signal.stop_loss)
    per_lot = value_per_lot_at_risk(target_spec, sl_dist)
    actual = actual_volume * per_lot
    return (intended - actual) / intended * 100.0


def score_position(
    signal: TradeSignal,
    target_equity: float,
    target_risk_pct: float,
    target_spec: SymbolSpec,
    actual_volume: float,
) -> PositionScore:
    rr = _rr_ratio(signal)
    deviation = _risk_deviation_pct(signal, target_equity, target_risk_pct, target_spec, actual_volume)

    rr_component = 0.0 if rr is None else min(rr / RR_RATIO_CAP, 1.0) * RR_WEIGHT
    deviation_component = max(0.0, 1.0 - abs(deviation) / DEVIATION_FLOOR_PCT) * DEVIATION_WEIGHT
    quality = round(rr_component + deviation_component, 1)

    return PositionScore(
        rr_ratio=None if rr is None else round(rr, 2),
        risk_deviation_pct=round(deviation, 2),
        quality_score=quality,
    )
