"""YAML config loading for the copier engine.

See config/config.example.yaml for the expected shape. The real config
(config/config.yaml) is gitignored -- it will contain filesystem paths and,
later, broker credentials, so it must never be committed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import SymbolSpec


@dataclass(frozen=True)
class TargetAccountConfig:
    id: str
    files_dir: Path
    risk_pct: float
    symbol_specs: dict[str, SymbolSpec]


@dataclass(frozen=True)
class SourceAccountConfig:
    id: str
    files_dir: Path


@dataclass(frozen=True)
class EngineConfig:
    poll_interval_seconds: float
    max_daily_drawdown_pct: float
    kill_switch_file: Path
    db_path: Path
    source: SourceAccountConfig
    targets: list[TargetAccountConfig] = field(default_factory=list)


def _load_symbol_specs(raw: dict) -> dict[str, SymbolSpec]:
    specs: dict[str, SymbolSpec] = {}
    for symbol, s in raw.items():
        specs[symbol] = SymbolSpec(
            symbol=symbol,
            contract_size=float(s["contract_size"]),
            tick_size=float(s["tick_size"]),
            tick_value=float(s["tick_value"]),
            volume_step=float(s["volume_step"]),
            volume_min=float(s["volume_min"]),
            volume_max=float(s["volume_max"]),
        )
    return specs


def load_config(path: str | Path) -> EngineConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())

    source_raw = raw["source_account"]
    source = SourceAccountConfig(
        id=source_raw["id"],
        files_dir=Path(source_raw["files_dir"]).expanduser(),
    )

    targets = []
    for t in raw["targets"]:
        targets.append(
            TargetAccountConfig(
                id=t["id"],
                files_dir=Path(t["files_dir"]).expanduser(),
                risk_pct=float(t["risk_pct"]),
                symbol_specs=_load_symbol_specs(t["symbol_specs"]),
            )
        )

    risk_raw = raw.get("risk", {})

    return EngineConfig(
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 0.5)),
        max_daily_drawdown_pct=float(risk_raw.get("max_daily_drawdown_pct", 5.0)),
        kill_switch_file=Path(raw.get("kill_switch_file", "data/KILL_SWITCH")).expanduser(),
        db_path=Path(raw.get("db_path", "data/edgeflow.db")).expanduser(),
        source=source,
        targets=targets,
    )
