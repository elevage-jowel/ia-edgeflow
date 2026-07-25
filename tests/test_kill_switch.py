from pathlib import Path

from engine.kill_switch import AUTO_DRAWDOWN_REASON, MANUAL_REASON, KillSwitch


def test_not_active_by_default(tmp_path: Path):
    ks = KillSwitch(tmp_path / "KILL", max_daily_drawdown_pct=5.0)
    assert not ks.is_active()
    assert ks.reason() is None


def test_manual_kill_file_is_active_and_reported(tmp_path: Path):
    kill_file = tmp_path / "KILL"
    kill_file.write_text(MANUAL_REASON)
    ks = KillSwitch(kill_file, max_daily_drawdown_pct=5.0)

    assert ks.is_active()
    assert ks.reason() == MANUAL_REASON


def test_daily_drawdown_breach_triggers_and_is_visible_via_reason(tmp_path: Path):
    ks = KillSwitch(tmp_path / "KILL", max_daily_drawdown_pct=5.0)

    ks.is_active(equity=10_000, today="2026-07-25")  # day open, no drawdown yet
    assert not ks.is_active()

    killed = ks.is_active(equity=9_400, today="2026-07-25")  # -6% intraday
    assert killed
    assert ks.reason() == AUTO_DRAWDOWN_REASON


def test_auto_trigger_resets_on_new_trading_day(tmp_path: Path):
    ks = KillSwitch(tmp_path / "KILL", max_daily_drawdown_pct=5.0)

    ks.is_active(equity=10_000, today="2026-07-25")
    ks.is_active(equity=9_000, today="2026-07-25")  # -10%, triggers
    assert ks.is_active()
    assert ks.reason() == AUTO_DRAWDOWN_REASON

    # New day: even before any new equity reading, the stale auto-trigger
    # must not keep blocking the whole next trading session.
    still_active = ks.is_active(equity=9_000, today="2026-07-26")
    assert not still_active
    assert ks.reason() is None


def test_manual_kill_survives_a_new_trading_day(tmp_path: Path):
    kill_file = tmp_path / "KILL"
    ks = KillSwitch(kill_file, max_daily_drawdown_pct=5.0)
    kill_file.parent.mkdir(parents=True, exist_ok=True)
    kill_file.write_text(MANUAL_REASON)

    # A manual stop from the dashboard must NOT be auto-cleared just
    # because a new day rolled over -- only /api/resume should clear it.
    still_active = ks.is_active(equity=10_000, today="2026-07-26")
    assert still_active
    assert ks.reason() == MANUAL_REASON
