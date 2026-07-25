from engine.market_scanner import ACTIVE, HIT_SL, HIT_TP, PENDING, _resolve_status
from engine.models import Candle
from engine.smc_analysis import Direction


def candle(o, h, l, c, t="t"):
    return Candle(time=t, open=o, high=h, low=l, close=c)


# A bullish setup: return to [1.08, 1.102], entry 1.091, SL 1.078, TP 1.117.
ENTRY, SL, TP = 1.091, 1.078, 1.117


def test_stays_pending_while_price_never_reaches_entry():
    candles = [
        candle(1.15, 1.16, 1.14, 1.155, "t1"),
        candle(1.155, 1.16, 1.145, 1.15, "t2"),
    ]
    status = _resolve_status(Direction.BULLISH, ENTRY, SL, TP, PENDING, candles)

    assert status == PENDING


def test_becomes_active_once_entry_is_touched_without_resolving():
    candles = [
        candle(1.095, 1.096, 1.089, 1.090, "t1"),  # low 1.089 <= entry -> ACTIVE
    ]
    status = _resolve_status(Direction.BULLISH, ENTRY, SL, TP, PENDING, candles)

    assert status == ACTIVE


def test_hits_take_profit_after_becoming_active():
    candles = [
        candle(1.095, 1.096, 1.089, 1.090, "t1"),  # activates
        candle(1.090, 1.120, 1.088, 1.118, "t2"),  # high >= TP
    ]
    status = _resolve_status(Direction.BULLISH, ENTRY, SL, TP, PENDING, candles)

    assert status == HIT_TP


def test_hits_stop_loss_after_becoming_active():
    candles = [
        candle(1.095, 1.096, 1.089, 1.090, "t1"),  # activates
        candle(1.090, 1.091, 1.070, 1.075, "t2"),  # low <= SL
    ]
    status = _resolve_status(Direction.BULLISH, ENTRY, SL, TP, PENDING, candles)

    assert status == HIT_SL


def test_activation_and_resolution_in_the_same_candle_hits_tp():
    candles = [
        candle(1.095, 1.120, 1.089, 1.118, "t1"),  # dips to entry AND spikes to TP
    ]
    status = _resolve_status(Direction.BULLISH, ENTRY, SL, TP, PENDING, candles)

    assert status == HIT_TP


def test_ambiguous_candle_hitting_both_sl_and_tp_resolves_to_sl():
    candles = [
        candle(1.090, 1.091, 1.089, 1.090, "t0"),  # activates cleanly first
        candle(1.090, 1.120, 1.070, 1.100, "t1"),  # this bar's range covers both SL and TP
    ]
    status = _resolve_status(Direction.BULLISH, ENTRY, SL, TP, PENDING, candles)

    assert status == HIT_SL


def test_already_active_resumes_without_needing_reentry():
    candles = [
        candle(1.090, 1.120, 1.089, 1.118, "t1"),  # hits TP directly, already ACTIVE
    ]
    status = _resolve_status(Direction.BULLISH, ENTRY, SL, TP, ACTIVE, candles)

    assert status == HIT_TP


def test_bearish_setup_mirrors_bullish_logic():
    # A bearish setup: return to the zone from below, entry 1.091, SL
    # above at 1.100, TP below at 1.070.
    bearish_entry, bearish_sl, bearish_tp = 1.091, 1.100, 1.070
    candles = [
        candle(1.085, 1.093, 1.084, 1.090, "t1"),  # high >= entry -> ACTIVE
        candle(1.090, 1.092, 1.068, 1.072, "t2"),  # low <= TP
    ]
    status = _resolve_status(Direction.BEARISH, bearish_entry, bearish_sl, bearish_tp, PENDING, candles)

    assert status == HIT_TP


def test_no_candles_leaves_status_unchanged():
    assert _resolve_status(Direction.BULLISH, ENTRY, SL, TP, PENDING, []) == PENDING
    assert _resolve_status(Direction.BULLISH, ENTRY, SL, TP, ACTIVE, []) == ACTIVE
