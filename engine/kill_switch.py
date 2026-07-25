"""Hard stop for the copier.

Two independent triggers, either one halts new copies immediately:
  1. A manual kill file (touched by the dashboard's /kill endpoint, or by
     hand over SSH -- `touch data/KILL_SWITCH`).
  2. An automatic daily-drawdown breach on the source account's equity.

The engine must check is_active() before writing any new CopyCommand.
Closing existing positions is still allowed while killed -- only new
exposure is blocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DailyEquityTracker:
    day_start_equity: float | None = None
    day: str | None = None

    def update(self, equity: float, today: str) -> float:
        """Record equity and return the current drawdown in % from today's open."""
        if self.day != today or self.day_start_equity is None:
            self.day = today
            self.day_start_equity = equity
            return 0.0
        if self.day_start_equity <= 0:
            return 0.0
        drawdown_pct = (self.day_start_equity - equity) / self.day_start_equity * 100.0
        return max(0.0, drawdown_pct)


class KillSwitch:
    def __init__(self, kill_file: Path, max_daily_drawdown_pct: float):
        self.kill_file = kill_file
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self._tracker = DailyEquityTracker()
        self._auto_triggered = False

    def manual_active(self) -> bool:
        return self.kill_file.exists()

    def check_drawdown(self, equity: float, today: str) -> bool:
        drawdown_pct = self._tracker.update(equity, today)
        if drawdown_pct >= self.max_daily_drawdown_pct:
            self._auto_triggered = True
        return self._auto_triggered

    def is_active(self, equity: float | None = None, today: str | None = None) -> bool:
        if self.manual_active():
            return True
        if equity is not None and today is not None:
            return self.check_drawdown(equity, today)
        return self._auto_triggered

    def reset_auto_trigger(self) -> None:
        self._auto_triggered = False
