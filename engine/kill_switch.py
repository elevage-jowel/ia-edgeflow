"""Hard stop for the copier.

Two independent triggers, either one halts new copies immediately:
  1. A manual kill file (touched by the dashboard's /kill endpoint, or by
     hand over SSH -- `touch data/KILL_SWITCH`).
  2. An automatic daily-drawdown breach on the source account's equity.

Both triggers share the same kill file so the dashboard (a separate
process) can see either one -- an in-memory-only auto-trigger would be
invisible to `dashboard/app.py`, which only reads the filesystem. The
file's content records which reason set it (MANUAL vs AUTO_DRAWDOWN):
only the automatic trigger clears itself, at the start of the next
trading day; a manual kill is never cleared except by the dashboard's
/resume endpoint (or by hand).

The engine must check is_active() before writing any new CopyCommand.
Closing existing positions is still allowed while killed -- only new
exposure is blocked.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

MANUAL_REASON = "MANUAL"
AUTO_DRAWDOWN_REASON = "AUTO_DRAWDOWN"


class DailyEquityTracker:
    def __init__(self) -> None:
        self.day_start_equity: float | None = None
        self.day: str | None = None

    def update(self, equity: float, today: str) -> tuple[float, bool]:
        """Record equity; return (drawdown_pct_from_day_open, day_rolled_over)."""
        rolled_over = self.day is not None and self.day != today
        if self.day != today or self.day_start_equity is None:
            self.day = today
            self.day_start_equity = equity
            return 0.0, rolled_over
        if self.day_start_equity <= 0:
            return 0.0, False
        drawdown_pct = (self.day_start_equity - equity) / self.day_start_equity * 100.0
        return max(0.0, drawdown_pct), False


class KillSwitch:
    def __init__(self, kill_file: Path, max_daily_drawdown_pct: float,
                 on_auto_trigger: Callable[[float], None] | None = None):
        self.kill_file = kill_file
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self._tracker = DailyEquityTracker()
        # Called with the drawdown_pct exactly once, the moment the
        # automatic trigger fires -- lets main.py fire a proactive alert
        # instead of the trader only finding out from the dashboard.
        self.on_auto_trigger = on_auto_trigger

    def reason(self) -> str | None:
        if not self.kill_file.exists():
            return None
        try:
            content = self.kill_file.read_text().strip()
        except OSError:
            return MANUAL_REASON
        # A plain `touch` (dashboard, or by hand over SSH) leaves the file
        # empty -- treat that as a manual kill too.
        return content or MANUAL_REASON

    def _write_reason(self, reason: str) -> None:
        self.kill_file.parent.mkdir(parents=True, exist_ok=True)
        self.kill_file.write_text(reason)

    def check_drawdown(self, equity: float, today: str) -> None:
        drawdown_pct, rolled_over = self._tracker.update(equity, today)
        current_reason = self.reason()

        if rolled_over and current_reason == AUTO_DRAWDOWN_REASON:
            # New trading day: only the automatic trigger resets itself.
            self.kill_file.unlink(missing_ok=True)
            current_reason = None

        if drawdown_pct >= self.max_daily_drawdown_pct and current_reason is None:
            self._write_reason(AUTO_DRAWDOWN_REASON)
            if self.on_auto_trigger is not None:
                self.on_auto_trigger(drawdown_pct)

    def is_active(self, equity: float | None = None, today: str | None = None) -> bool:
        if equity is not None and today is not None:
            self.check_drawdown(equity, today)
        return self.kill_file.exists()
