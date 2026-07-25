import pytest

from engine.models import Candle
from engine.smc_analysis import (
    Direction,
    analyze_entry_context,
    find_breaks_of_structure,
    find_fair_value_gaps,
    find_liquidity_grabs,
    find_order_blocks,
    find_swing_points,
)


def candle(o, h, l, c):
    return Candle(time="t", open=o, high=h, low=l, close=c)


def test_find_swing_points_detects_high_and_low():
    candles = [
        candle(1.10, 1.11, 1.09, 1.105),
        candle(1.105, 1.13, 1.104, 1.125),   # swing high
        candle(1.125, 1.126, 1.10, 1.102),
        candle(1.102, 1.103, 1.08, 1.085),   # swing low
        candle(1.085, 1.095, 1.084, 1.09),
    ]
    points = find_swing_points(candles, left=1, right=1)
    highs = [p for p in points if p.direction == Direction.BEARISH]
    lows = [p for p in points if p.direction == Direction.BULLISH]

    assert any(p.index == 1 and p.price == pytest.approx(1.13) for p in highs)
    assert any(p.index == 3 and p.price == pytest.approx(1.08) for p in lows)


def test_find_fair_value_gaps_bullish():
    candles = [
        candle(1.10, 1.105, 1.095, 1.10),
        candle(1.10, 1.12, 1.099, 1.119),
        candle(1.119, 1.125, 1.115, 1.12),
    ]
    gaps = find_fair_value_gaps(candles)

    assert len(gaps) == 1
    assert gaps[0].direction == Direction.BULLISH
    assert gaps[0].bottom == pytest.approx(1.105)
    assert gaps[0].top == pytest.approx(1.115)


def test_find_fair_value_gaps_bearish():
    candles = [
        candle(1.12, 1.125, 1.115, 1.12),
        candle(1.12, 1.101, 1.10, 1.101),
        candle(1.101, 1.105, 1.095, 1.10),
    ]
    gaps = find_fair_value_gaps(candles)

    assert len(gaps) == 1
    assert gaps[0].direction == Direction.BEARISH


def test_find_liquidity_grab_on_swing_high():
    candles = [
        candle(1.10, 1.11, 1.09, 1.105),
        candle(1.105, 1.13, 1.104, 1.125),   # swing high @ 1.13
        candle(1.125, 1.126, 1.10, 1.102),
        candle(1.102, 1.135, 1.10, 1.108),   # wick above 1.13, closes back below -> grab
    ]
    swings = find_swing_points(candles, left=1, right=1)
    grabs = find_liquidity_grabs(candles, swings)

    assert len(grabs) == 1
    assert grabs[0].direction == Direction.BEARISH
    assert grabs[0].swept_price == pytest.approx(1.13)
    assert grabs[0].index == 3


def test_break_of_structure_is_not_mistaken_for_a_grab():
    candles = [
        candle(1.10, 1.11, 1.09, 1.105),
        candle(1.105, 1.13, 1.104, 1.125),   # swing high @ 1.13
        candle(1.125, 1.126, 1.10, 1.102),
        candle(1.102, 1.14, 1.101, 1.138),   # closes above 1.13 -> confirmed break, not a grab
    ]
    swings = find_swing_points(candles, left=1, right=1)
    grabs = find_liquidity_grabs(candles, swings)
    boses = find_breaks_of_structure(candles, swings)

    assert grabs == []
    assert len(boses) == 1
    assert boses[0].direction == Direction.BULLISH
    assert boses[0].level == pytest.approx(1.13)
    assert boses[0].index == 3


def test_find_order_block_before_bullish_bos():
    candles = [
        candle(1.10, 1.11, 1.09, 1.105),
        candle(1.105, 1.13, 1.104, 1.125),
        candle(1.125, 1.126, 1.10, 1.102),   # down candle -> the order block
        candle(1.102, 1.14, 1.101, 1.138),   # BOS candle
    ]
    swings = find_swing_points(candles, left=1, right=1)
    boses = find_breaks_of_structure(candles, swings)
    obs = find_order_blocks(candles, boses)

    assert len(obs) == 1
    assert obs[0].direction == Direction.BULLISH
    assert obs[0].index == 2
    assert obs[0].high == pytest.approx(1.126)
    assert obs[0].low == pytest.approx(1.10)


def test_analyze_entry_context_integrates_all_detectors():
    # analyze_entry_context uses the default left=2/right=2 swing window,
    # so this needs enough padding on both sides of the swing high for it
    # to qualify (unlike the narrower left=1/right=1 tests above).
    candles = [
        candle(1.075, 1.080, 1.070, 1.078),
        candle(1.078, 1.090, 1.077, 1.085),
        candle(1.085, 1.130, 1.084, 1.125),   # swing high @ 1.13
        candle(1.125, 1.105, 1.090, 1.095),
        candle(1.095, 1.102, 1.080, 1.082),   # down candle -> order block
        candle(1.082, 1.140, 1.081, 1.138),   # closes above 1.13 -> BOS
    ]
    context = analyze_entry_context(candles)

    assert len(context.breaks_of_structure) >= 1
    assert len(context.order_blocks) >= 1


def test_analyze_entry_context_handles_short_history():
    context = analyze_entry_context([candle(1.10, 1.11, 1.09, 1.105)])

    assert context.fair_value_gaps == []
    assert context.breaks_of_structure == []
    assert context.liquidity_grabs == []
    assert context.order_blocks == []
