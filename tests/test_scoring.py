import pytest

from engine.models import Side, SignalEvent, SymbolSpec, TradeSignal
from engine.scoring import score_position


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


def make_signal(entry=1.1000, sl=1.0950, tp=1.1150):
    return TradeSignal(
        source_account_id="src", source_ticket=1, event=SignalEvent.OPEN,
        symbol="EURUSD", side=Side.BUY, volume=1.0,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        source_equity=10_000.0, timestamp="2026-07-25T00:00:00",
    )


def test_good_rr_and_no_deviation_scores_high():
    # SL 50 pips, TP 150 pips -> RR 3:1 (the cap), volume sized exactly to risk.
    signal = make_signal(sl=1.0950, tp=1.1150)
    spec = eurusd_spec()
    # exact risk-parity volume for this signal/spec: 100 / (500 * 1.0) = 0.2
    score = score_position(signal, target_equity=10_000, target_risk_pct=1.0,
                            target_spec=spec, actual_volume=0.2)

    assert score.rr_ratio == pytest.approx(3.0)
    assert score.risk_deviation_pct == pytest.approx(0.0, abs=0.5)
    assert score.quality_score >= 95


def test_no_take_profit_gives_none_rr():
    signal = make_signal(tp=0)
    spec = eurusd_spec()

    score = score_position(signal, target_equity=10_000, target_risk_pct=1.0,
                            target_spec=spec, actual_volume=0.2)

    assert score.rr_ratio is None


def test_clamped_volume_lowers_score_via_deviation():
    signal = make_signal(sl=1.0950, tp=1.1150)
    spec = eurusd_spec()
    # Intended volume is 0.2; simulate a broker max clamp down to 0.01.
    score = score_position(signal, target_equity=10_000, target_risk_pct=1.0,
                            target_spec=spec, actual_volume=0.01)

    assert score.risk_deviation_pct == pytest.approx(95.0, abs=0.5)
    assert score.quality_score < 75  # RR component alone (70) minus some, deviation ~0


def test_bad_rr_scores_lower_than_good_rr():
    good = make_signal(sl=1.0950, tp=1.1150)  # RR 3:1
    bad = make_signal(sl=1.0950, tp=1.1010)   # RR 0.2:1
    spec = eurusd_spec()

    good_score = score_position(good, target_equity=10_000, target_risk_pct=1.0,
                                 target_spec=spec, actual_volume=0.2)
    bad_score = score_position(bad, target_equity=10_000, target_risk_pct=1.0,
                                target_spec=spec, actual_volume=0.2)

    assert good_score.quality_score > bad_score.quality_score
