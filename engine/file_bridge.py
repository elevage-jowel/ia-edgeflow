"""File-based bridge between this Python engine and the MQL4/MQL5 EAs.

Why files instead of sockets/ZeroMQ: the terminals run under Wine on the
same Linux VPS as this engine (see deploy/hostinger-setup.md). A shared
directory is available for free, needs no extra DLL/library installed
inside Wine, and survives terminal restarts. Latency is a non-issue here --
polling every 200-500ms is far faster than most retail-broker execution
latency anyway.

Protocol:
  - The SignalPublisher EA writes one file per event to
    `<source_files_dir>/out/<ticket>_<event>_<uid>.json`, atomically (it
    writes to a `.tmp` name then renames -- renames are atomic on the same
    filesystem, so we never read a half-written file).
  - This bridge picks up new files, deserializes them, and moves them to
    `out/processed/` once handled (never deletes, for audit purposes).
  - Outgoing commands for a target are written the same way to
    `<target_files_dir>/in/<ticket>_<event>_<uid>.json`; the
    CommandExecutor EA on that terminal polls `in/` and moves handled files
    to `in/done/`.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from .models import Candle, CopyCommand, SignalEvent, Side, TradeSignal

logger = logging.getLogger(__name__)


def _atomic_write_json(directory: Path, filename: str, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = directory / f".{filename}.tmp"
    final_path = directory / filename
    tmp_path.write_text(json.dumps(payload, indent=2))
    tmp_path.rename(final_path)


class SignalInbox:
    """Reads TradeSignal files dropped by the source EA."""

    def __init__(self, source_files_dir: Path, source_account_id: str):
        self.out_dir = source_files_dir / "out"
        self.processed_dir = self.out_dir / "processed"
        self.error_dir = self.out_dir / "error"
        self.source_account_id = source_account_id

    def poll(self) -> Iterator[tuple[Path, TradeSignal]]:
        if not self.out_dir.exists():
            return
        for path in sorted(self.out_dir.glob("*.json")):
            if path.parent != self.out_dir:
                continue
            try:
                raw = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                # File is still mid-write (we didn't see the atomic rename
                # complete yet); try again next poll rather than quarantining.
                continue

            try:
                signal = self._parse_signal(raw)
            except (KeyError, TypeError, ValueError) as exc:
                # Valid JSON but the wrong shape -- an EA bug, not a torn
                # write. Reprocessing this every poll would wedge the whole
                # pipeline behind one bad file, so quarantine it instead.
                logger.error("malformed signal file %s, moving to error/: %s", path, exc)
                self._quarantine(path)
                continue

            yield path, signal

    def _parse_signal(self, raw: dict) -> TradeSignal:
        context_candles = [
            Candle(
                time=str(c["time"]), open=float(c["open"]), high=float(c["high"]),
                low=float(c["low"]), close=float(c["close"]),
            )
            for c in raw.get("context_candles", [])
        ]
        return TradeSignal(
            source_account_id=self.source_account_id,
            source_ticket=int(raw["ticket"]),
            event=SignalEvent(raw["event"]),
            symbol=str(raw["symbol"]),
            side=Side(raw["side"]),
            volume=float(raw["volume"]),
            entry_price=float(raw["entry_price"]),
            stop_loss=float(raw["stop_loss"]),
            take_profit=float(raw["take_profit"]),
            source_equity=float(raw["equity"]),
            timestamp=str(raw["timestamp"]),
            context_candles=context_candles,
        )

    def _quarantine(self, path: Path) -> None:
        self.error_dir.mkdir(parents=True, exist_ok=True)
        path.rename(self.error_dir / path.name)

    def mark_processed(self, path: Path) -> None:
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        path.rename(self.processed_dir / path.name)


class CommandOutbox:
    """Writes CopyCommand files for a target EA to pick up."""

    def __init__(self, target_files_dir: Path):
        self.in_dir = target_files_dir / "in"

    def send(self, command: CopyCommand) -> Path:
        filename = f"{command.source_ticket}_{command.event.value}_{uuid.uuid4().hex[:8]}.json"
        payload = asdict(command)
        payload["event"] = command.event.value
        payload["side"] = command.side.value
        payload["written_at"] = time.time()
        _atomic_write_json(self.in_dir, filename, payload)
        return self.in_dir / filename
