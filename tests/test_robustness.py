import json
import time
from pathlib import Path

import pytest
import yaml

from engine import db
from engine.config import ConfigError, EngineConfig, SourceAccountConfig, TargetAccountConfig, load_config
from engine.file_bridge import CommandOutbox, SignalInbox
from engine.heartbeat import HeartbeatError, read_heartbeat
from engine.kill_switch import KillSwitch
from engine.main import run_once
from engine.models import SymbolSpec


def test_load_config_missing_file_raises_config_error():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.yaml")


def test_load_config_missing_required_field_raises_clear_error(tmp_path: Path):
    bad_config = {
        "source_account": {"id": "src"},  # missing files_dir
        "targets": [],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(bad_config))

    with pytest.raises(ConfigError, match="files_dir"):
        load_config(path)


def test_load_config_empty_targets_raises_config_error(tmp_path: Path):
    bad_config = {
        "source_account": {"id": "src", "files_dir": "/tmp/src"},
        "targets": [],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(bad_config))

    with pytest.raises(ConfigError, match="targets"):
        load_config(path)


def test_load_config_valid_minimal_config_succeeds(tmp_path: Path):
    good_config = {
        "source_account": {"id": "src", "files_dir": str(tmp_path / "src")},
        "targets": [{
            "id": "tgt",
            "files_dir": str(tmp_path / "tgt"),
            "risk_pct": 1.0,
            "symbol_specs": {
                "EURUSD": {
                    "contract_size": 100000, "tick_size": 0.00001, "tick_value": 1.0,
                    "volume_step": 0.01, "volume_min": 0.01, "volume_max": 100,
                }
            },
        }],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(good_config))

    cfg = load_config(path)
    assert cfg.source.id == "src"
    assert cfg.targets[0].risk_pct == 1.0


def test_malformed_signal_file_is_quarantined_not_crashed(tmp_path: Path):
    source_dir = tmp_path / "source"
    (source_dir / "out").mkdir(parents=True)

    # Valid JSON, but missing required fields -- simulates an EA bug.
    (source_dir / "out" / "bad.json").write_text(json.dumps({"ticket": 1}))
    # A good signal alongside it should still be picked up.
    good_payload = {
        "ticket": 2, "event": "OPEN", "symbol": "EURUSD", "side": "BUY",
        "volume": 1.0, "entry_price": 1.10, "stop_loss": 1.09, "take_profit": 1.12,
        "equity": 10000, "timestamp": "t",
    }
    (source_dir / "out" / "good.json").write_text(json.dumps(good_payload))

    inbox = SignalInbox(source_dir, "src")
    results = list(inbox.poll())

    assert len(results) == 1
    assert results[0][1].source_ticket == 2
    assert (source_dir / "out" / "error" / "bad.json").exists()
    assert not (source_dir / "out" / "bad.json").exists()


def test_read_heartbeat_malformed_json_raises_heartbeat_error(tmp_path: Path):
    (tmp_path / "heartbeat.json").write_text("{not valid json")

    with pytest.raises(HeartbeatError):
        read_heartbeat(tmp_path)


def test_read_heartbeat_missing_field_raises_heartbeat_error(tmp_path: Path):
    (tmp_path / "heartbeat.json").write_text(json.dumps({"equity": 1000}))  # no balance

    with pytest.raises(HeartbeatError):
        read_heartbeat(tmp_path)


class _ExplodingOutbox:
    """Simulates an unexpected failure (e.g. a disk error) writing a command."""

    def send(self, command):
        raise RuntimeError("disk exploded")


def test_run_once_survives_unexpected_error_in_one_target(tmp_path: Path):
    source_dir = tmp_path / "source"
    (source_dir / "out").mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "heartbeat.json").write_text(
        json.dumps({"equity": 10000, "balance": 10000, "timestamp": time.time()})
    )

    payload = {
        "ticket": 42, "event": "OPEN", "symbol": "EURUSD", "side": "BUY",
        "volume": 1.0, "entry_price": 1.10, "stop_loss": 1.09, "take_profit": 1.12,
        "equity": 10000, "timestamp": "t",
    }
    (source_dir / "out" / "sig.json").write_text(json.dumps(payload))

    spec = SymbolSpec("EURUSD", 100000, 0.00001, 1.0, 0.01, 0.01, 100)
    cfg = EngineConfig(
        poll_interval_seconds=0.1, max_daily_drawdown_pct=5.0,
        kill_switch_file=tmp_path / "KILL", db_path=tmp_path / "edgeflow.db",
        source=SourceAccountConfig(id="src", files_dir=source_dir),
        targets=[TargetAccountConfig(id="tgt", files_dir=target_dir, risk_pct=1.0,
                                      symbol_specs={"EURUSD": spec})],
    )

    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {"tgt": _ExplodingOutbox()}
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)

    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)  # must not raise
        rows = conn.execute("SELECT status FROM copy_log").fetchall()
        assert rows and rows[0][0] == "SKIPPED_UNEXPECTED_ERROR"

    # The signal file must still be archived -- one target's crash shouldn't
    # leave it stuck reprocessing forever.
    assert (source_dir / "out" / "processed" / "sig.json").exists()


def test_modify_to_breakeven_sl_is_still_forwarded(tmp_path: Path):
    """Regression test: a MODIFY that moves SL to the entry price (breakeven)
    must not go through risk-based volume sizing -- that has a zero
    stop-distance guard which would otherwise silently drop the update."""
    source_dir = tmp_path / "source"
    (source_dir / "out").mkdir(parents=True)
    target_dir = tmp_path / "target"
    (target_dir / "in").mkdir(parents=True)
    (target_dir / "heartbeat.json").write_text(
        json.dumps({"equity": 10000, "balance": 10000, "timestamp": time.time()})
    )

    spec = SymbolSpec("EURUSD", 100000, 0.00001, 1.0, 0.01, 0.01, 100)
    cfg = EngineConfig(
        poll_interval_seconds=0.1, max_daily_drawdown_pct=5.0,
        kill_switch_file=tmp_path / "KILL", db_path=tmp_path / "edgeflow.db",
        source=SourceAccountConfig(id="src", files_dir=source_dir),
        targets=[TargetAccountConfig(id="tgt", files_dir=target_dir, risk_pct=1.0,
                                      symbol_specs={"EURUSD": spec})],
    )
    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {"tgt": CommandOutbox(target_dir)}
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)

    def drop(name, event, sl):
        (source_dir / "out" / name).write_text(json.dumps({
            "ticket": 321, "event": event, "symbol": "EURUSD", "side": "BUY",
            "volume": 1.0, "entry_price": 1.1000, "stop_loss": sl, "take_profit": 1.1150,
            "equity": 10000, "timestamp": "t",
        }))

    with db.connect(cfg.db_path) as conn:
        drop("1_open.json", "OPEN", 1.0950)
        run_once(cfg, inbox, outboxes, kill_switch, conn)

        drop("2_mod.json", "MODIFY", 1.1000)  # breakeven: SL == entry price
        run_once(cfg, inbox, outboxes, kill_switch, conn)

        rows = conn.execute(
            "SELECT status FROM copy_log WHERE event = 'MODIFY'"
        ).fetchall()

    assert rows and rows[0][0] == "MODIFY_FORWARDED"
    modify_files = list((target_dir / "in").glob("321_MODIFY_*.json"))
    assert len(modify_files) == 1
    sent = json.loads(modify_files[0].read_text())
    assert sent["stop_loss"] == pytest.approx(1.1000)


def test_close_event_records_outcome(tmp_path: Path):
    """Regression test: closing a position must capture close_price and
    profit, not just flip status to CLOSED -- otherwise nothing can ever
    learn which entry patterns led to good trades."""
    source_dir = tmp_path / "source"
    (source_dir / "out").mkdir(parents=True)
    target_dir = tmp_path / "target"
    (target_dir / "in").mkdir(parents=True)
    (target_dir / "heartbeat.json").write_text(
        json.dumps({"equity": 10000, "balance": 10000, "timestamp": time.time()})
    )

    spec = SymbolSpec("EURUSD", 100000, 0.00001, 1.0, 0.01, 0.01, 100)
    cfg = EngineConfig(
        poll_interval_seconds=0.1, max_daily_drawdown_pct=5.0,
        kill_switch_file=tmp_path / "KILL", db_path=tmp_path / "edgeflow.db",
        source=SourceAccountConfig(id="src", files_dir=source_dir),
        targets=[TargetAccountConfig(id="tgt", files_dir=target_dir, risk_pct=1.0,
                                      symbol_specs={"EURUSD": spec})],
    )
    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {"tgt": CommandOutbox(target_dir)}
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)

    (source_dir / "out" / "1_open.json").write_text(json.dumps({
        "ticket": 99, "event": "OPEN", "symbol": "EURUSD", "side": "BUY",
        "volume": 1.0, "entry_price": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1150,
        "equity": 10000, "timestamp": "t",
    }))
    (source_dir / "out" / "2_close.json").write_text(json.dumps({
        "ticket": 99, "event": "CLOSE", "symbol": "EURUSD", "side": "BUY",
        "volume": 1.0, "entry_price": 1.1000, "close_price": 1.1150,
        "stop_loss": 1.0950, "take_profit": 1.1150, "profit": 150.0,
        "equity": 10150, "timestamp": "t",
    }))

    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)
        run_once(cfg, inbox, outboxes, kill_switch, conn)
        row = dict(db.list_positions(conn)[0])

    assert row["status"] == "CLOSED"
    assert row["close_price"] == pytest.approx(1.1150)
    assert row["source_profit"] == pytest.approx(150.0)


def _open_position_kwargs(**overrides):
    kwargs = dict(
        source_account_id="src", source_ticket=1, target_account_id="tgt",
        symbol="EURUSD", side="BUY", entry_price=1.10, stop_loss=1.09,
        take_profit=1.12, target_volume=0.1, risk_pct_intended=1.0,
        rr_ratio=2.0, risk_deviation_pct=0.0, quality_score=90.0,
    )
    kwargs.update(overrides)
    return kwargs


def test_duplicate_open_position_is_ignored_not_overwritten(tmp_path: Path):
    """Regression test: a re-processed OPEN signal for a ticket that
    already has a position record must not reset an already-CLOSED
    position back to OPEN, nor wipe its opened_at timestamp."""
    with db.connect(tmp_path / "edgeflow.db") as conn:
        first = db.open_position(conn, **_open_position_kwargs())
        assert first is True

        db.close_position(conn, source_account_id="src", source_ticket=1, target_account_id="tgt")

        # A duplicate OPEN arrives (e.g. a reprocessed file) after the
        # position was already closed.
        second = db.open_position(conn, **_open_position_kwargs(quality_score=10.0))
        assert second is False

        positions = db.list_positions(conn)
        assert len(positions) == 1
        assert dict(positions[0])["status"] == "CLOSED"
        assert dict(positions[0])["quality_score"] == 90.0
