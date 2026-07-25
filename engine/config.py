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


class ConfigError(ValueError):
    """Raised for a malformed or incomplete config.yaml, with a message
    pointing at the missing/bad field -- a raw KeyError here is useless to
    whoever is debugging a VPS deployment at 2am."""


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


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"missing required field '{key}' in {where}")
    return d[key]


def _load_symbol_specs(raw: dict, target_id: str) -> dict[str, SymbolSpec]:
    specs: dict[str, SymbolSpec] = {}
    required = ("contract_size", "tick_size", "tick_value", "volume_step", "volume_min", "volume_max")
    for symbol, s in raw.items():
        where = f"targets[{target_id}].symbol_specs[{symbol}]"
        for key in required:
            _require(s, key, where)
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
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")

    source_raw = _require(raw, "source_account", "config")
    source = SourceAccountConfig(
        id=_require(source_raw, "id", "source_account"),
        files_dir=Path(_require(source_raw, "files_dir", "source_account")).expanduser(),
    )

    targets_raw = _require(raw, "targets", "config")
    if not targets_raw:
        raise ConfigError("'targets' is empty -- at least one target account is required")

    targets = []
    for t in targets_raw:
        target_id = _require(t, "id", "targets[]")
        targets.append(
            TargetAccountConfig(
                id=target_id,
                files_dir=Path(_require(t, "files_dir", f"targets[{target_id}]")).expanduser(),
                risk_pct=float(_require(t, "risk_pct", f"targets[{target_id}]")),
                symbol_specs=_load_symbol_specs(_require(t, "symbol_specs", f"targets[{target_id}]"), target_id),
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
