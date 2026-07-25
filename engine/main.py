"""Orchestrator loop: source signals -> risk-parity sizing -> target commands.

Run with:  python -m engine.main config/config.yaml
"""
from __future__ import annotations

import json
import logging
import signal as signal_module
import sys
import time
from dataclasses import asdict
from datetime import date

from . import db
from .config import ConfigError, EngineConfig, TargetAccountConfig, load_config
from .file_bridge import CommandOutbox, SignalInbox
from .heartbeat import HeartbeatError, read_heartbeat
from .kill_switch import KillSwitch
from .market_scanner import scan_and_notify
from .models import CopyCommand, TradeSignal
from .notifier import Notifier
from .risk_engine import RiskEngineError, compute_target_volume, round_to_step
from .scoring import score_position
from .smc_analysis import analyze_entry_context

logger = logging.getLogger(__name__)

# Minimum gap between two "unexpected error" alerts -- a bug that fires on
# every poll cycle (every ~500ms) must not turn into hundreds of messages.
UNEXPECTED_ERROR_ALERT_INTERVAL_SECONDS = 900


def _rr_tier_reached(rr_ratio: float, thresholds: list[float]) -> float | None:
    """thresholds must be sorted descending; returns the highest one rr_ratio clears."""
    for threshold in thresholds:
        if rr_ratio >= threshold:
            return threshold
    return None


def _handle_partial_close(signal: TradeSignal, target: TargetAccountConfig,
                           outbox: CommandOutbox, conn) -> None:
    volumes = db.get_position_volumes(
        conn, source_account_id=signal.source_account_id,
        source_ticket=signal.source_ticket, target_account_id=target.id,
    )
    if volumes is None:
        db.log_copy(
            conn, source_account_id=signal.source_account_id,
            source_ticket=signal.source_ticket, target_account_id=target.id,
            event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
            source_volume=signal.volume, target_volume=None,
            entry_price=signal.entry_price, stop_loss=signal.stop_loss,
            take_profit=signal.take_profit, status="SKIPPED_NO_POSITION_RECORD",
            detail="no open position on record for this ticket -- can't compute a proportional size",
        )
        return

    source_volume_at_open, target_volume_at_open = volumes
    # Always derive from the ORIGINAL opened volumes, never from a running
    # total: correct after any number of partial closes without rounding
    # compounding across them.
    fraction_remaining = signal.volume / source_volume_at_open
    desired_target_volume = target_volume_at_open * fraction_remaining

    spec = target.symbol_specs.get(signal.symbol)
    if spec is not None:
        # Round DOWN (never up) so the target never ends up carrying more
        # exposure than proportionally correct. If what's left rounds below
        # the broker's minimum, treat it as "close the rest" (0) rather than
        # forcing it back up to volume_min, which would undo the partial close.
        desired_target_volume = round_to_step(desired_target_volume, spec)
        if desired_target_volume < spec.volume_min:
            desired_target_volume = 0.0

    outbox.send(CopyCommand(
        target_account_id=target.id,
        source_ticket=signal.source_ticket,
        event=signal.event,
        symbol=signal.symbol,
        side=signal.side,
        volume=desired_target_volume,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
    ))
    db.log_copy(
        conn, source_account_id=signal.source_account_id,
        source_ticket=signal.source_ticket, target_account_id=target.id,
        event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
        source_volume=signal.volume, target_volume=desired_target_volume,
        entry_price=signal.entry_price, stop_loss=signal.stop_loss,
        take_profit=signal.take_profit, status="PARTIAL_CLOSE_FORWARDED",
    )


def _process_target(signal: TradeSignal, target: TargetAccountConfig,
                     outbox: CommandOutbox, killed: bool, conn,
                     notifier: Notifier | None = None,
                     rr_alert_thresholds: list[float] | None = None) -> None:
    if signal.event.value == "PARTIAL_CLOSE":
        # Risk-reducing, same as a full CLOSE -- always allowed, even while killed.
        _handle_partial_close(signal, target, outbox, conn)
        return

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
        db.close_position(
            conn, source_account_id=signal.source_account_id,
            source_ticket=signal.source_ticket, target_account_id=target.id,
            close_price=signal.close_price, source_profit=signal.close_profit,
        )
        return

    if killed:
        db.log_copy(
            conn, source_account_id=signal.source_account_id,
            source_ticket=signal.source_ticket, target_account_id=target.id,
            event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
            source_volume=signal.volume, target_volume=None,
            entry_price=signal.entry_price, stop_loss=signal.stop_loss,
            take_profit=signal.take_profit, status="SKIPPED_KILL_SWITCH",
        )
        return

    if signal.event.value == "MODIFY":
        # A SL/TP edit on an already-open position never changes its size,
        # so this must NOT go through risk-based volume sizing below: a
        # breakeven stop (SL == entry price) would hit compute_target_volume's
        # zero-distance guard and silently drop the update. The executor EA
        # ignores the volume field on MODIFY (see mql/README.md), so this is
        # a pure SL/TP forward.
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
            take_profit=signal.take_profit, status="MODIFY_FORWARDED",
        )
        return

    # OPEN from here on: needs full risk-parity sizing.
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
        return

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
        return

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
        return

    if target.max_absolute_volume is not None and volume > target.max_absolute_volume:
        logger.warning(
            "volume %.4f for %s on %s exceeds safety cap %.4f; clamping",
            volume, signal.symbol, target.id, target.max_absolute_volume,
        )
        if notifier is not None:
            notifier.notify(
                "Sentinel : volume plafonné",
                f"{signal.symbol} sur {target.id} : volume calculé {volume:.2f} lots dépasse le "
                f"plafond de sécurité {target.max_absolute_volume:.2f} — ouverture plafonnée.",
                kind=f"volume_capped_{target.id}",
                min_interval_seconds=3600,
            )
        volume = target.max_absolute_volume

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

    # Only OPEN reaches here -- CLOSE and MODIFY both return earlier above.
    score = score_position(
        signal, target_equity=heartbeat.equity,
        target_risk_pct=target.risk_pct, target_spec=spec,
        actual_volume=volume,
    )

    context_json = None
    has_fvg = has_grab = has_bos = has_ob = False
    if signal.context_candles:
        context = analyze_entry_context(signal.context_candles)
        has_fvg = len(context.fair_value_gaps) > 0
        has_grab = len(context.liquidity_grabs) > 0
        has_bos = len(context.breaks_of_structure) > 0
        has_ob = len(context.order_blocks) > 0
        context_json = json.dumps(asdict(context))

    inserted = db.open_position(
        conn, source_account_id=signal.source_account_id,
        source_ticket=signal.source_ticket, target_account_id=target.id,
        symbol=signal.symbol, side=signal.side.value,
        entry_price=signal.entry_price, stop_loss=signal.stop_loss,
        take_profit=signal.take_profit, source_volume=signal.volume, target_volume=volume,
        risk_pct_intended=target.risk_pct, rr_ratio=score.rr_ratio,
        risk_deviation_pct=score.risk_deviation_pct,
        quality_score=score.quality_score,
        has_fvg=has_fvg, has_liquidity_grab=has_grab,
        has_bos=has_bos, has_order_block=has_ob,
        context_json=context_json,
    )
    if not inserted:
        logger.warning(
            "duplicate OPEN for ticket %s on %s ignored (position record already exists)",
            signal.source_ticket, target.id,
        )
        return

    if notifier is not None and score.rr_ratio is not None and rr_alert_thresholds:
        tier = _rr_tier_reached(score.rr_ratio, rr_alert_thresholds)
        if tier is not None:
            notifier.notify(
                f"Sentinel : trade R:R {score.rr_ratio:.1f}",
                f"{signal.symbol} {signal.side.value} sur {target.id} — "
                f"R:R {score.rr_ratio:.2f} (seuil {tier:g} atteint), "
                f"score qualité {score.quality_score:.0f}/100, volume {volume}.",
                kind=f"rr_open_{target.id}_{signal.source_ticket}",
            )


def run_once(cfg: EngineConfig, inbox: SignalInbox, outboxes: dict[str, CommandOutbox],
             kill_switch: KillSwitch, conn, notifier: Notifier | None = None) -> None:
    for path, signal in inbox.poll():
        killed = kill_switch.is_active(signal.source_equity, date.today().isoformat())

        for target in cfg.targets:
            try:
                _process_target(signal, target, outboxes[target.id], killed, conn,
                                 notifier, cfg.rr_alert_thresholds)
            except Exception as exc:  # noqa: BLE001 -- one bad target must never take down the service
                logger.exception(
                    "unexpected error copying ticket %s (%s) to %s",
                    signal.source_ticket, signal.symbol, target.id,
                )
                db.log_copy(
                    conn, source_account_id=signal.source_account_id,
                    source_ticket=signal.source_ticket, target_account_id=target.id,
                    event=signal.event.value, symbol=signal.symbol, side=signal.side.value,
                    source_volume=signal.volume, target_volume=None,
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit, status="SKIPPED_UNEXPECTED_ERROR",
                    detail=str(exc),
                )
                if notifier is not None:
                    notifier.notify(
                        "Sentinel : erreur inattendue",
                        f"Copie de {signal.symbol} (ticket {signal.source_ticket}) vers "
                        f"{target.id} en échec : {exc}",
                        kind="unexpected_error",
                        min_interval_seconds=UNEXPECTED_ERROR_ALERT_INTERVAL_SECONDS,
                    )

        try:
            inbox.mark_processed(path)
        except OSError:
            logger.exception("failed to archive processed signal file %s", path)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(config_path: str) -> None:
    _setup_logging()

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        logger.error("config error: %s", exc)
        sys.exit(1)

    inbox = SignalInbox(cfg.source.files_dir, cfg.source.id)
    outboxes = {t.id: CommandOutbox(t.files_dir) for t in cfg.targets}
    notifier = Notifier(cfg.notifications)

    def _on_auto_trigger(drawdown_pct: float) -> None:
        notifier.notify(
            "Sentinel : arrêt automatique (drawdown)",
            f"Le drawdown journalier du compte source {cfg.source.id} a atteint "
            f"{drawdown_pct:.1f}% (limite {cfg.max_daily_drawdown_pct:.1f}%). "
            f"Nouvelles copies bloquées jusqu'à demain ou une reprise manuelle.",
        )

    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct,
                              on_auto_trigger=_on_auto_trigger)

    stop = {"requested": False}

    def _request_stop(signum, frame):  # noqa: ARG001 -- required signal handler signature
        logger.info("received signal %s, shutting down after current cycle", signum)
        stop["requested"] = True

    signal_module.signal(signal_module.SIGTERM, _request_stop)
    signal_module.signal(signal_module.SIGINT, _request_stop)

    if cfg.market_scan.enabled:
        logger.info("market scan enabled for %s", cfg.market_scan.symbols)

    last_market_scan = 0.0

    with db.connect(cfg.db_path) as conn:
        logger.info(
            "Sentinel engine started: source=%s targets=%s",
            cfg.source.id, [t.id for t in cfg.targets],
        )
        while not stop["requested"]:
            try:
                run_once(cfg, inbox, outboxes, kill_switch, conn, notifier)
            except Exception:  # noqa: BLE001 -- a bad poll cycle must not crash the service
                logger.exception("unexpected error in run_once, continuing")

            now = time.time()
            if cfg.market_scan.enabled and now - last_market_scan >= cfg.market_scan.poll_interval_seconds:
                last_market_scan = now
                try:
                    scan_and_notify(cfg, conn, notifier)
                except Exception:  # noqa: BLE001 -- a bad scan must not crash the service
                    logger.exception("unexpected error in market scan, continuing")

            time.sleep(cfg.poll_interval_seconds)

    logger.info("edgeflow engine stopped")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m engine.main <path-to-config.yaml>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
