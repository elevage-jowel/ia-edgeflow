import json
import time
from pathlib import Path

import pytest
import yaml

from engine import db
from engine.config import ConfigError, EngineConfig, SourceAccountConfig, TargetAccountConfig, load_config
from engine.file_bridge import SignalInbox
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
