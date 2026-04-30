"""SQLite 历史数据存储 — 探测结果 + 告警记录。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "monitor.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_name TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    success     INTEGER NOT NULL,
    health      TEXT NOT NULL,
    payload     TEXT NOT NULL  -- JSON
);

CREATE INDEX IF NOT EXISTS idx_probes_server_time
    ON probes(server_name, timestamp DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    server_name TEXT NOT NULL,
    severity    TEXT NOT NULL,  -- info / warning / critical
    alert_key   TEXT NOT NULL,  -- e.g. "service_down", "license_degraded"
    message     TEXT NOT NULL,
    sent        INTEGER NOT NULL DEFAULT 0,  -- 邮件是否发送成功
    acked       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_time
    ON alerts(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_key
    ON alerts(server_name, alert_key);
"""


def init_db(path: Path = DB_PATH) -> None:
    """创建表 + 索引(幂等)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn(path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---- Probe ----

def insert_probe(probe_dict: dict[str, Any], path: Path = DB_PATH) -> int:
    with get_conn(path) as conn:
        cur = conn.execute(
            "INSERT INTO probes (server_name, timestamp, success, health, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                probe_dict["server_name"],
                probe_dict["timestamp"],
                int(probe_dict.get("success", False)),
                probe_dict.get("health", "unknown"),
                json.dumps(probe_dict, ensure_ascii=False),
            ),
        )
        return cur.lastrowid or 0


def latest_probe(server_name: str, path: Path = DB_PATH) -> dict[str, Any] | None:
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT payload FROM probes WHERE server_name=? "
            "ORDER BY timestamp DESC LIMIT 1",
            (server_name,),
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def history_probes(
    server_name: str,
    hours: int = 24,
    path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """最近 N 小时的探测记录(用于趋势图)。"""
    with get_conn(path) as conn:
        rows = conn.execute(
            "SELECT payload FROM probes WHERE server_name=? "
            "AND datetime(timestamp) > datetime('now', ?) "
            "ORDER BY timestamp ASC",
            (server_name, f"-{hours} hours"),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def cleanup_old_probes(keep_days: int = 30, path: Path = DB_PATH) -> int:
    """删除 N 天前的探测记录(防止 DB 无限增长)。"""
    with get_conn(path) as conn:
        cur = conn.execute(
            "DELETE FROM probes WHERE datetime(timestamp) < datetime('now', ?)",
            (f"-{keep_days} days",),
        )
        return cur.rowcount


# ---- Alerts ----

def insert_alert(
    server_name: str,
    severity: str,
    alert_key: str,
    message: str,
    sent: bool,
    path: Path = DB_PATH,
) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    with get_conn(path) as conn:
        cur = conn.execute(
            "INSERT INTO alerts (timestamp, server_name, severity, alert_key, message, sent) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, server_name, severity, alert_key, message, int(sent)),
        )
        return cur.lastrowid or 0


def recent_alerts(limit: int = 50, path: Path = DB_PATH) -> list[dict[str, Any]]:
    with get_conn(path) as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def last_alert_of_key(
    server_name: str,
    alert_key: str,
    path: Path = DB_PATH,
) -> dict[str, Any] | None:
    """查同一 server+alert_key 上次告警(用于 cooldown 判断)。"""
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT * FROM alerts WHERE server_name=? AND alert_key=? "
            "ORDER BY timestamp DESC LIMIT 1",
            (server_name, alert_key),
        ).fetchone()
    return dict(row) if row else None
