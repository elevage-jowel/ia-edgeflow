import json
import time
from pathlib import Path

import pytest

from engine import db
from engine.config import EngineConfig, SourceAccountConfig, TargetAccountConfig
from engine.file_bridge import CommandOutbox, SignalInbox
from engine.kill_switch import KillSwitch
from engine.main import run_once
from engine.models import SymbolSpec


def _setup(tmp_path: Path, max_absolute_volume=None):
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
                                      symbol_specs={"EURUSD": spec},
                                      max_absolute_volume=max_absolute_volume)],
    )
    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {"tgt": CommandOutbox(target_dir)}
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)
    return cfg, inbox, outboxes, kill_switch, source_dir, target_dir


def _drop(source_dir, name, event, volume, **overrides):
    payload = {
        "ticket": 42, "event": event, "symbol": "EURUSD", "side": "BUY",
        "volume": volume, "entry_price": 1.1000, "stop_loss": 1.0950, "take_profit": 1.1150,
        "equity": 10000, "timestamp": "t",
    }
    payload.update(overrides)
    (source_dir / "out" / name).write_text(json.dumps(payload))


def test_partial_close_reduces_target_proportionally(tmp_path: Path):
    cfg, inbox, outboxes, kill_switch, source_dir, target_dir = _setup(tmp_path)

    # Open with source volume 1.0; risk-parity sizing on a 10k account at
    # 1% risk with a 50-pip SL gives a target volume of 0.2 (see risk_engine
    # doctests-equivalent in test_risk_engine.py).
    _drop(source_dir, "1_open.json", "OPEN", volume=1.0)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)
        opened = dict(db.list_positions(conn)[0])
    target_volume_at_open = opened["target_volume"]
    assert target_volume_at_open == pytest.approx(0.2)

    # Source closes half (down to 0.5 remaining) -> target should shrink to
    # roughly half its own volume too, proportionally.
    _drop(source_dir, "2_partial.json", "PARTIAL_CLOSE", volume=0.5)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)
        rows = conn.execute(
            "SELECT status FROM copy_log WHERE event = 'PARTIAL_CLOSE'"
        ).fetchall()

    assert rows and rows[0][0] == "PARTIAL_CLOSE_FORWARDED"
    cmd_files = list((target_dir / "in").glob("42_PARTIAL_CLOSE_*.json"))
    assert len(cmd_files) == 1
    sent = json.loads(cmd_files[0].read_text())
    assert sent["volume"] == pytest.approx(0.1)  # half of 0.2


def test_partial_close_below_volume_min_closes_fully(tmp_path: Path):
    cfg, inbox, outboxes, kill_switch, source_dir, target_dir = _setup(tmp_path)

    _drop(source_dir, "1_open.json", "OPEN", volume=1.0)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)

    # Source closes down to a sliver (1% remaining) -> the proportional
    # target remainder (0.002) rounds below volume_min (0.01), so the
    # target must be told to close fully (volume=0), not forced back up.
    _drop(source_dir, "2_partial.json", "PARTIAL_CLOSE", volume=0.01)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)

    cmd_files = list((target_dir / "in").glob("42_PARTIAL_CLOSE_*.json"))
    sent = json.loads(cmd_files[0].read_text())
    assert sent["volume"] == 0.0


def test_partial_close_without_known_position_is_skipped(tmp_path: Path):
    cfg, inbox, outboxes, kill_switch, source_dir, target_dir = _setup(tmp_path)

    # No OPEN was ever recorded for this ticket (e.g. engine missed it).
    _drop(source_dir, "1_partial.json", "PARTIAL_CLOSE", volume=0.5)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)
        rows = conn.execute("SELECT status FROM copy_log").fetchall()

    assert rows and rows[0][0] == "SKIPPED_NO_POSITION_RECORD"
    assert list((target_dir / "in").glob("*.json")) == []


def test_partial_close_allowed_even_when_kill_switch_active(tmp_path: Path):
    cfg, inbox, outboxes, kill_switch, source_dir, target_dir = _setup(tmp_path)

    _drop(source_dir, "1_open.json", "OPEN", volume=1.0)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)

    kill_switch.kill_file.parent.mkdir(parents=True, exist_ok=True)
    kill_switch.kill_file.write_text("MANUAL")

    _drop(source_dir, "2_partial.json", "PARTIAL_CLOSE", volume=0.5)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)
        rows = conn.execute(
            "SELECT status FROM copy_log WHERE event = 'PARTIAL_CLOSE'"
        ).fetchall()

    assert rows and rows[0][0] == "PARTIAL_CLOSE_FORWARDED"


def test_volume_above_safety_cap_is_clamped(tmp_path: Path):
    cfg, inbox, outboxes, kill_switch, source_dir, target_dir = _setup(
        tmp_path, max_absolute_volume=0.05,
    )

    # Risk-parity sizing would compute 0.2 lots here, well above the cap.
    _drop(source_dir, "1_open.json", "OPEN", volume=1.0)
    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn)
        opened = dict(db.list_positions(conn)[0])

    assert opened["target_volume"] == pytest.approx(0.05)
