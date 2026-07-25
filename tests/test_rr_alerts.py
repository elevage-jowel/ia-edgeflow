import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from engine import db
from engine.config import EngineConfig, SourceAccountConfig, TargetAccountConfig
from engine.file_bridge import CommandOutbox, SignalInbox
from engine.kill_switch import KillSwitch
from engine.main import run_once
from engine.models import SymbolSpec


def _make_cfg(tmp_path: Path, source_dir: Path, target_dir: Path, spec: SymbolSpec,
              rr_alert_thresholds=None) -> EngineConfig:
    return EngineConfig(
        poll_interval_seconds=0.1, max_daily_drawdown_pct=5.0,
        kill_switch_file=tmp_path / "KILL", db_path=tmp_path / "edgeflow.db",
        source=SourceAccountConfig(id="src", files_dir=source_dir),
        targets=[TargetAccountConfig(id="tgt", files_dir=target_dir, risk_pct=1.0,
                                      symbol_specs={"EURUSD": spec})],
        rr_alert_thresholds=rr_alert_thresholds or [4.0, 3.0, 2.0],
    )


def _drop_open(source_dir: Path, sl: float, tp: float):
    (source_dir / "out" / "1_open.json").write_text(json.dumps({
        "ticket": 1, "event": "OPEN", "symbol": "EURUSD", "side": "BUY",
        "volume": 1.0, "entry_price": 1.1000, "stop_loss": sl, "take_profit": tp,
        "equity": 10000, "timestamp": "t",
    }))


def _setup(tmp_path: Path):
    source_dir = tmp_path / "source"
    (source_dir / "out").mkdir(parents=True)
    target_dir = tmp_path / "target"
    (target_dir / "in").mkdir(parents=True)
    (target_dir / "heartbeat.json").write_text(
        json.dumps({"equity": 10000, "balance": 10000, "timestamp": time.time()})
    )
    spec = SymbolSpec("EURUSD", 100000, 0.00001, 1.0, 0.01, 0.01, 100)
    return source_dir, target_dir, spec


def test_high_rr_open_triggers_notification(tmp_path: Path):
    source_dir, target_dir, spec = _setup(tmp_path)
    cfg = _make_cfg(tmp_path, source_dir, target_dir, spec)
    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {"tgt": CommandOutbox(target_dir)}
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)
    notifier = MagicMock()

    _drop_open(source_dir, sl=1.0950, tp=1.1150)  # RR = 3:1

    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn, notifier)

    notifier.notify.assert_called_once()
    subject, message = notifier.notify.call_args.args
    assert "3.0" in subject
    assert "EURUSD" in message


def test_low_rr_open_does_not_trigger_notification(tmp_path: Path):
    source_dir, target_dir, spec = _setup(tmp_path)
    cfg = _make_cfg(tmp_path, source_dir, target_dir, spec)
    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {"tgt": CommandOutbox(target_dir)}
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)
    notifier = MagicMock()

    _drop_open(source_dir, sl=1.0950, tp=1.1050)  # RR = 1:1, below the 2.0 threshold

    with db.connect(cfg.db_path) as conn:
        run_once(cfg, inbox, outboxes, kill_switch, conn, notifier)

    notifier.notify.assert_not_called()
