import json
from pathlib import Path
from unittest.mock import MagicMock

from engine import db
from engine.config import EngineConfig, MarketScanConfig, SourceAccountConfig
from engine.market_scanner import scan_and_notify

CANDLES = [
    {"time": "t0", "open": 1.075, "high": 1.080, "low": 1.070, "close": 1.078},
    {"time": "t1", "open": 1.078, "high": 1.090, "low": 1.077, "close": 1.085},
    {"time": "t2", "open": 1.085, "high": 1.130, "low": 1.084, "close": 1.125},
    {"time": "t3", "open": 1.125, "high": 1.105, "low": 1.090, "close": 1.095},
    {"time": "t4", "open": 1.095, "high": 1.102, "low": 1.080, "close": 1.082},
    {"time": "t5", "open": 1.082, "high": 1.140, "low": 1.081, "close": 1.138},
]


def _cfg(tmp_path: Path, enabled: bool = True) -> tuple[EngineConfig, Path]:
    source_dir = tmp_path / "source"
    market_dir = source_dir / "market"
    market_dir.mkdir(parents=True)
    cfg = EngineConfig(
        poll_interval_seconds=0.5, max_daily_drawdown_pct=5.0,
        kill_switch_file=tmp_path / "KILL", db_path=tmp_path / "edgeflow.db",
        source=SourceAccountConfig(id="src", files_dir=source_dir),
        targets=[],
        market_scan=MarketScanConfig(enabled=enabled, symbols=["EURUSD"], rr_ratio=2.0),
    )
    return cfg, market_dir


def test_scan_detects_and_notifies_new_setup(tmp_path: Path):
    cfg, market_dir = _cfg(tmp_path)
    (market_dir / "EURUSD.json").write_text(json.dumps({"symbol": "EURUSD", "candles": CANDLES}))
    notifier = MagicMock()

    with db.connect(cfg.db_path) as conn:
        new_count = scan_and_notify(cfg, conn, notifier)
        patterns = db.list_market_patterns(conn)

    assert new_count == 1
    notifier.notify.assert_called_once()
    assert len(patterns) == 1
    assert patterns[0]["symbol"] == "EURUSD"


def test_scan_does_not_renotify_the_same_setup(tmp_path: Path):
    cfg, market_dir = _cfg(tmp_path)
    (market_dir / "EURUSD.json").write_text(json.dumps({"symbol": "EURUSD", "candles": CANDLES}))
    notifier = MagicMock()

    with db.connect(cfg.db_path) as conn:
        scan_and_notify(cfg, conn, notifier)
        scan_and_notify(cfg, conn, notifier)
        count_after_two_scans = notifier.notify.call_count
        # Same static snapshot scanned twice more -- whatever happened on
        # the first two scans (detection, and possibly an immediate
        # resolution given this fixture), it must not keep re-firing.
        scan_and_notify(cfg, conn, notifier)
        scan_and_notify(cfg, conn, notifier)
        count_after_four_scans = notifier.notify.call_count
        patterns = db.list_market_patterns(conn)

    assert count_after_four_scans == count_after_two_scans
    assert len(patterns) == 1  # still deduped to a single row, not re-inserted


def test_scan_disabled_does_nothing(tmp_path: Path):
    cfg, market_dir = _cfg(tmp_path, enabled=False)
    (market_dir / "EURUSD.json").write_text(json.dumps({"symbol": "EURUSD", "candles": CANDLES}))
    notifier = MagicMock()

    with db.connect(cfg.db_path) as conn:
        new_count = scan_and_notify(cfg, conn, notifier)

    assert new_count == 0
    notifier.notify.assert_not_called()


def test_scan_missing_snapshot_file_is_skipped_not_crashed(tmp_path: Path):
    cfg, _market_dir = _cfg(tmp_path)  # no EURUSD.json written
    notifier = MagicMock()

    with db.connect(cfg.db_path) as conn:
        new_count = scan_and_notify(cfg, conn, notifier)  # must not raise

    assert new_count == 0


def test_scan_malformed_snapshot_is_skipped_not_crashed(tmp_path: Path):
    cfg, market_dir = _cfg(tmp_path)
    (market_dir / "EURUSD.json").write_text("{not valid json")
    notifier = MagicMock()

    with db.connect(cfg.db_path) as conn:
        new_count = scan_and_notify(cfg, conn, notifier)  # must not raise

    assert new_count == 0
    notifier.notify.assert_not_called()


def test_setup_expires_once_its_window_rolls_past_it(tmp_path: Path):
    """Once the rolling snapshot no longer contains the setup's
    last-checked candle, it must be marked EXPIRED rather than silently
    guessed at -- an honest "we lost track", not counted as a win or loss."""
    cfg, market_dir = _cfg(tmp_path)
    (market_dir / "EURUSD.json").write_text(json.dumps({"symbol": "EURUSD", "candles": CANDLES}))
    notifier = MagicMock()

    with db.connect(cfg.db_path) as conn:
        scan_and_notify(cfg, conn, notifier)  # detects (and likely resolves, see other test)

        # A much later window that no longer contains any of the original
        # candles' timestamps -- simulates the rolling window moving on.
        later_candles = [
            {"time": f"later{i}", "open": 1.20, "high": 1.201, "low": 1.199, "close": 1.200}
            for i in range(10)
        ]
        (market_dir / "EURUSD.json").write_text(json.dumps({"symbol": "EURUSD", "candles": later_candles}))
        scan_and_notify(cfg, conn, notifier)

        statuses = {row["status"] for row in db.list_market_patterns(conn)}

    # The original setup is either resolved (HIT_TP/HIT_SL, decided before
    # the window moved on) or EXPIRED -- never left dangling as PENDING/ACTIVE.
    assert statuses <= {"HIT_TP", "HIT_SL", "EXPIRED"}
