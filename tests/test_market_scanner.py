import pytest

from engine.models import Candle, Side
from engine.market_scanner import find_setups, setup_side
from engine.smc_analysis import Direction


def candle(o, h, l, c, t="t"):
    return Candle(time=t, open=o, high=h, low=l, close=c)


def _bullish_ob_scenario():
    return [
        candle(1.075, 1.080, 1.070, 1.078, "t0"),
        candle(1.078, 1.090, 1.077, 1.085, "t1"),
        candle(1.085, 1.130, 1.084, 1.125, "t2"),
        candle(1.125, 1.105, 1.090, 1.095, "t3"),
        candle(1.095, 1.102, 1.080, 1.082, "t4"),  # order block
        candle(1.082, 1.140, 1.081, 1.138, "t5"),  # BOS
    ]


def test_finds_bullish_order_block_setup():
    setups = find_setups("EURUSD", _bullish_ob_scenario(), rr_ratio=2.0)

    assert len(setups) == 1
    setup = setups[0]
    assert setup.symbol == "EURUSD"
    assert setup.direction == Direction.BULLISH
    assert setup.order_block_time == "t4"
    assert setup.stop_loss < setup.entry_price < setup.take_profit


def test_rr_ratio_controls_take_profit_distance():
    candles = _bullish_ob_scenario()
    setup_2r = find_setups("EURUSD", candles, rr_ratio=2.0)[0]
    setup_4r = find_setups("EURUSD", candles, rr_ratio=4.0)[0]

    risk_2r = setup_2r.entry_price - setup_2r.stop_loss
    reward_2r = setup_2r.take_profit - setup_2r.entry_price
    reward_4r = setup_4r.take_profit - setup_4r.entry_price

    assert reward_2r == pytest.approx(risk_2r * 2.0)
    assert reward_4r == pytest.approx(risk_2r * 4.0)


def test_no_setups_when_no_order_block_present():
    flat_candles = [candle(1.10, 1.101, 1.099, 1.100, f"t{i}") for i in range(10)]

    assert find_setups("EURUSD", flat_candles) == []


def test_setup_side_matches_direction():
    setup = find_setups("EURUSD", _bullish_ob_scenario())[0]

    assert setup_side(setup) == Side.BUY
