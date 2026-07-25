import pytest

from engine.models import Side, SignalEvent, SymbolSpec, TradeSignal
from engine.risk_engine import RiskEngineError, compute_target_volume


def make_signal(entry=1.1000, sl=1.0950, volume=1.0, equity=10_000.0):
    return TradeSignal(
        source_account_id="src",
        source_ticket=1,
        event=SignalEvent.OPEN,
        symbol="EURUSD",
        side=Side.BUY,
        volume=volume,
        entry_price=entry,
        stop_loss=sl,
        take_profit=1.1100,
        source_equity=equity,
        timestamp="2026-07-25T00:00:00",
    )


def eurusd_spec(**overrides):
    defaults = dict(
        symbol="EURUSD",
        contract_size=100_000,
        tick_size=0.00001,
        tick_value=1.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=100,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def test_same_risk_pct_scales_with_target_equity():
    signal = make_signal()
    spec = eurusd_spec()

    small = compute_target_volume(signal, target_equity=5_000, target_risk_pct=1.0, target_spec=spec)
    big = compute_target_volume(signal, target_equity=50_000, target_risk_pct=1.0, target_spec=spec)

    assert big == pytest.approx(small * 10, rel=1e-6)


def test_volume_rounds_down_to_step():
    signal = make_signal()
    spec = eurusd_spec(volume_step=0.1)

    volume = compute_target_volume(signal, target_equity=10_000, target_risk_pct=1.0, target_spec=spec)

    # 10_000 * 1% = 100 risk / (500 ticks * 1.0 tick_value) = 0.2 lots exactly here,
    # but any fractional result must land on a 0.1 multiple.
    assert round(volume / 0.1) * 0.1 == pytest.approx(volume)


def test_volume_clamped_to_broker_max():
    signal = make_signal(entry=1.1000, sl=1.0999)  # tiny stop -> huge nominal size
    spec = eurusd_spec(volume_max=5.0)

    volume = compute_target_volume(signal, target_equity=1_000_000, target_risk_pct=5.0, target_spec=spec)

    assert volume == 5.0


def test_zero_stop_distance_rejected():
    signal = make_signal(entry=1.1000, sl=1.1000)
    spec = eurusd_spec()

    with pytest.raises(RiskEngineError):
        compute_target_volume(signal, target_equity=10_000, target_risk_pct=1.0, target_spec=spec)


def test_volume_below_broker_minimum_is_skipped_not_rounded_up():
    signal = make_signal(entry=1.1000, sl=1.0000)  # huge stop distance -> tiny size
    spec = eurusd_spec(volume_min=0.01, volume_step=0.01)

    with pytest.raises(RiskEngineError):
        compute_target_volume(signal, target_equity=100, target_risk_pct=0.01, target_spec=spec)


def test_non_positive_equity_rejected():
    signal = make_signal()
    spec = eurusd_spec()

    with pytest.raises(RiskEngineError):
        compute_target_volume(signal, target_equity=0, target_risk_pct=1.0, target_spec=spec)
