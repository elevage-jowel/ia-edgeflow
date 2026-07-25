"""Minimal status dashboard + kill switch.

Run with: uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
Put it behind a reverse proxy with basic auth / your own auth before
exposing it beyond localhost (see deploy/hostinger-setup.md) -- this app
has no authentication of its own.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from engine import db
from engine.config import load_config
from engine.kill_switch import KillSwitch

CONFIG_PATH = os.environ.get("EDGEFLOW_CONFIG", "config/config.yaml")

app = FastAPI(title="EdgeFlow Copier")


def _cfg():
    return load_config(CONFIG_PATH)


@app.get("/api/status")
def status():
    cfg = _cfg()
    kill_switch = KillSwitch(cfg.kill_switch_file, cfg.max_daily_drawdown_pct)
    reason = kill_switch.reason()

    recent = []
    if cfg.db_path.exists():
        conn = sqlite3.connect(cfg.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM copy_log ORDER BY id DESC LIMIT 20"
        ).fetchall()
        recent = [dict(r) for r in rows]
        conn.close()

    return {
        "kill_switch_active": reason is not None,
        "kill_switch_reason": reason,
        "source_account": cfg.source.id,
        "targets": [t.id for t in cfg.targets],
        "recent_events": recent,
    }


@app.get("/api/positions")
def positions():
    cfg = _cfg()
    if not cfg.db_path.exists():
        return {"positions": []}
    with db.connect(cfg.db_path) as conn:
        rows = db.list_positions(conn, limit=100)
        return {"positions": [dict(r) for r in rows]}


@app.post("/api/kill")
def kill():
    cfg = _cfg()
    cfg.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.kill_switch_file.write_text("MANUAL")
    return {"kill_switch_active": True}


@app.post("/api/resume")
def resume():
    cfg = _cfg()
    if cfg.kill_switch_file.exists():
        cfg.kill_switch_file.unlink()
    return {"kill_switch_active": False}


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "templates" / "status.html").read_text()
