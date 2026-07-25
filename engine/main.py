"""Orchestrator loop: source signals -> risk-parity sizing -> target commands.

Run with:  python -m engine.main config/config.yaml
"""
from __future__ import annotations

import sys
import time
from datetime import date

from . import db
from .config import EngineConfig, load_config
from .file_bridge import CommandOutbox, SignalInbox
from .heartbeat import HeartbeatError, read_heartbeat
from .kill_switch import KillSwitch
from .models import CopyCommand
from .risk_engine import RiskEngineError, compute_target_volume


def run_once(cfg: EngineConfig, inbox: SignalInbox, outboxes: dict[str, CommandOutbox],
             kill_switch: KillSwitch, conn) -> None:
    for path, signal in inbox.poll():
        killed = kill_switch.is_active(signal.source_equity, date.today().isoformat())

        for target in cfg.targets:
            outbox = outboxes[target.id]

            if signal.event.value == "CLOSE":
                # Closing exposure is always allowed, even while killed.
                outbox.send(CopyCommand(
                    target_account_id=target.id,
                    source_ticket=signal.source_ticket,
                    event=signal.event,
                    symbol=signal.symbol,
                    side=signal.side,
                    volume=signal.volume,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                ))
                db.log_copy(
                    conn, source_account_id=signal.source_account_id,
                    source_ticket=signal.source_ticket, target_account_id=target.id,
                    event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
                    source_volume=signal.volume, target_volume=None,
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit, status="CLOSE_FORWARDED",
                )
                continue

            if killed:
                db.log_copy(
                    conn, source_account_id=signal.source_account_id,
                    source_ticket=signal.source_ticket, target_account_id=target.id,
                    event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
                    source_volume=signal.volume, target_volume=None,
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit, status="SKIPPED_KILL_SWITCH",
                )
                continue

            spec = target.symbol_specs.get(signal.symbol)
            if spec is None:
                db.log_copy(
                    conn, source_account_id=signal.source_account_id,
                    source_ticket=signal.source_ticket, target_account_id=target.id,
                    event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
                    source_volume=signal.volume, target_volume=None,
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit, status="SKIPPED_NO_SYMBOL_SPEC",
                    detail=f"no symbol_specs entry for {signal.symbol} on {target.id}",
                )
                continue

            try:
                heartbeat = read_heartbeat(target.files_dir)
            except HeartbeatError as exc:
                db.log_copy(
                    conn, source_account_id=signal.source_account_id,
                    source_ticket=signal.source_ticket, target_account_id=target.id,
                    event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
                    source_volume=signal.volume, target_volume=None,
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit, status="SKIPPED_NO_HEARTBEAT",
                    detail=str(exc),
                )
                continue

            try:
                volume = compute_target_volume(
                    signal, target_equity=heartbeat.equity,
                    target_risk_pct=target.risk_pct, target_spec=spec,
                )
            except RiskEngineError as exc:
                db.log_copy(
                    conn, source_account_id=signal.source_account_id,
                    source_ticket=signal.source_ticket, target_account_id=target.id,
                    event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
                    source_volume=signal.volume, target_volume=None,
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit, status="SKIPPED_RISK_ERROR",
                    detail=str(exc),
                )
                continue

            outbox.send(CopyCommand(
                target_account_id=target.id,
                source_ticket=signal.source_ticket,
                event=signal.event,
                symbol=signal.symbol,
                side=signal.side,
                volume=volume,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            ))
            db.log_copy(
                conn, source_account_id=signal.source_account_id,
                source_ticket=signal.source_ticket, target_account_id=target.id,
                event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
                source_volume=signal.volume, target_volume=volume,
                entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                take_profit=signal.take_profit, status="COPIED",
            )

        inbox.mark_processed(path)


def main(config_path: str) -> None:
    cfg = load_config(config_path)
    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {t.id: CommandOutbox(t.files_dir) for t in cfg.targets}
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)

    with db.connect(cfg.db_path) as conn:
        print(f"edgeflow engine started: source={cfg.source.id} targets={[t.id for t in cfg.targets]}")
        while True:
            run_once(cfg, inbox, outboxes, kill_switch, conn)
            time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m engine.main <path-to-config.yaml>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
