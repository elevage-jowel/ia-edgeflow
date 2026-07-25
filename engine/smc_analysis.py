"""Smart Money Concepts (ICT-style) entry-context detection.

Given the H1 candles leading up to a trade entry, detects the structural
elements the user reads their entries by: imbalances (Fair Value Gaps),
liquidity grabs (stop hunts on a swing high/low), breaks of structure, and
order blocks.

SMC/ICT terminology has no single universally agreed rule set -- each
detector below uses one explicit, documented definition. Treat this as a
starting point to validate against real trades and refine, not gospel.

Not implemented yet: breaker blocks (an order block invalidated by price,
then acting as a flip zone). That needs tracking an order block's state
across many subsequent candles -- a natural follow-up once this base is
validated against real entries.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import Candle


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(frozen=True)
class SwingPoint:
    index: int
    price: float
    # BEARISH = swing high (resistance / sell-side structure), BULLISH = swing low
    direction: Direction


@dataclass(frozen=True)
class FairValueGap:
    direction: Direction
    top: float
    bottom: float
    index: int  # index of the impulsive (middle) candle of the 3-candle pattern


@dataclass(frozen=True)
class LiquidityGrab:
    direction: Direction  # resulting bias after the sweep is rejected
    swept_price: float
    index: int


@dataclass(frozen=True)
class BreakOfStructure:
    direction: Direction
    level: float
    index: int


@dataclass(frozen=True)
class OrderBlock:
    direction: Direction
    high: float
    low: float
    index: int


@dataclass(frozen=True)
class EntryContext:
    fair_value_gaps: list[FairValueGap]
    liquidity_grabs: list[LiquidityGrab]
    breaks_of_structure: list[BreakOfStructure]
    order_blocks: list[OrderBlock]


def find_swing_points(candles: list[Candle], left: int = 2, right: int = 2) -> list[SwingPoint]:
    """Fractal-style swing points: a high/low with `left`/`right` weaker bars on each side."""
    points: list[SwingPoint] = []
    n = len(candles)
    for i in range(left, n - right):
        high = candles[i].high
        if (all(candles[j].high < high for j in range(i - left, i))
                and all(candles[j].high < high for j in range(i + 1, i + right + 1))):
            points.append(SwingPoint(i, high, Direction.BEARISH))

        low = candles[i].low
        if (all(candles[j].low > low for j in range(i - left, i))
                and all(candles[j].low > low for j in range(i + 1, i + right + 1))):
            points.append(SwingPoint(i, low, Direction.BULLISH))
    return points


def find_fair_value_gaps(candles: list[Candle]) -> list[FairValueGap]:
    """Classic 3-candle ICT FVG: a gap between candle[i-1] and candle[i+1]
    left unfilled by candle[i]'s wick."""
    gaps: list[FairValueGap] = []
    for i in range(1, len(candles) - 1):
        prev, nxt = candles[i - 1], candles[i + 1]
        if prev.high < nxt.low:
            gaps.append(FairValueGap(Direction.BULLISH, top=nxt.low, bottom=prev.high, index=i))
        elif prev.low > nxt.high:
            gaps.append(FairValueGap(Direction.BEARISH, top=prev.low, bottom=nxt.high, index=i))
    return gaps


def find_liquidity_grabs(candles: list[Candle], swing_points: list[SwingPoint]) -> list[LiquidityGrab]:
    """A wick pierces a prior swing high/low (taking the resting liquidity)
    but the candle closes back on the other side -- rejection, not a break."""
    grabs: list[LiquidityGrab] = []
    for sp in swing_points:
        for i in range(sp.index + 1, len(candles)):
            c = candles[i]
            if sp.direction == Direction.BEARISH:  # swing high
                if c.high > sp.price and c.close < sp.price:
                    grabs.append(LiquidityGrab(Direction.BEARISH, sp.price, i))
                    break
                if c.close > sp.price:
                    break  # confirmed break instead of a grab; stop scanning this swing
            else:  # swing low
                if c.low < sp.price and c.close > sp.price:
                    grabs.append(LiquidityGrab(Direction.BULLISH, sp.price, i))
                    break
                if c.close < sp.price:
                    break
    return grabs


def find_breaks_of_structure(candles: list[Candle], swing_points: list[SwingPoint]) -> list[BreakOfStructure]:
    """A candle *closes* beyond a prior swing high/low (not just a wick)."""
    boses: list[BreakOfStructure] = []
    for sp in swing_points:
        for i in range(sp.index + 1, len(candles)):
            c = candles[i]
            if sp.direction == Direction.BEARISH and c.close > sp.price:
                boses.append(BreakOfStructure(Direction.BULLISH, sp.price, i))
                break
            if sp.direction == Direction.BULLISH and c.close < sp.price:
                boses.append(BreakOfStructure(Direction.BEARISH, sp.price, i))
                break
    return boses


def find_order_blocks(candles: list[Candle], breaks: list[BreakOfStructure]) -> list[OrderBlock]:
    """The last opposite-colored candle before the impulsive move that caused a BOS."""
    blocks: list[OrderBlock] = []
    for bos in breaks:
        for i in range(bos.index - 1, -1, -1):
            c = candles[i]
            if bos.direction == Direction.BULLISH and c.close < c.open:
                blocks.append(OrderBlock(Direction.BULLISH, c.high, c.low, i))
                break
            if bos.direction == Direction.BEARISH and c.close > c.open:
                blocks.append(OrderBlock(Direction.BEARISH, c.high, c.low, i))
                break
    return blocks


def analyze_entry_context(candles: list[Candle]) -> EntryContext:
    swings = find_swing_points(candles)
    fvgs = find_fair_value_gaps(candles)
    grabs = find_liquidity_grabs(candles, swings)
    boses = find_breaks_of_structure(candles, swings)
    obs = find_order_blocks(candles, boses)
    return EntryContext(fvgs, grabs, boses, obs)
