"""SQLite log and cache.

Alpaca is the source of truth. This store is append-only audit logging
plus a small cache for indicators / earnings / VIX.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    market_open INTEGER NOT NULL,
    paused INTEGER NOT NULL,
    drawdown_suspended INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES cycles(id)
);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    received_at TEXT NOT NULL,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id)
);
"""
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL,
    stop_loss_pct REAL,
    take_profit_pct REAL,
    reasoning TEXT,
    received_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES cycles(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    approved INTEGER NOT NULL,
    shares INTEGER,
    entry_price REAL,
    stop_price REAL,
    target_price REAL,
    reason TEXT,
    config_hash TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES cycles(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    qty REAL,
    limit_price REAL,
    stop_price REAL,
    take_profit_price REAL,
    client_order_id TEXT UNIQUE,
    status TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    filled_at TEXT,
    stop_live_at TEXT,
    FOREIGN KEY(cycle_id) REFERENCES cycles(id)
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    buying_power REAL NOT NULL,
    daily_start_equity REAL,
    high_water_mark REAL NOT NULL,
    drawdown_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);


class Store:
    def __init__(self, db_path: str | Path = "data/bot.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    def insert_cycle(self, mode: str, config_hash: str, market_open: bool,
                     paused: bool, drawdown_suspended: bool) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO cycles (started_at, mode, config_hash, market_open, "
                "paused, drawdown_suspended) VALUES (?,?,?,?,?,?)",
                (self.now_iso(), mode, config_hash,
                 int(market_open), int(paused), int(drawdown_suspended)),
            )
            return cur.lastrowid

    def insert_prompt(self, cycle_id: int, provider: str, model: str,
                      prompt_text: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO prompts (cycle_id, provider, model, prompt_text, sent_at) "
                "VALUES (?,?,?,?,?)",
                (cycle_id, provider, model, prompt_text, self.now_iso()),
            )
            return cur.lastrowid

    def insert_response(self, prompt_id: int, raw_text: str,
                        input_tokens: int | None, output_tokens: int | None,
                        cost_usd: float | None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO responses (prompt_id, raw_text, input_tokens, "
                "output_tokens, cost_usd, received_at) VALUES (?,?,?,?,?,?)",
                (prompt_id, raw_text, input_tokens, output_tokens, cost_usd,
                 self.now_iso()),
            )
            return cur.lastrowid
    def insert_proposal(self, cycle_id: int, ticker: str, action: str,
                        confidence: float | None, stop_loss_pct: float | None,
                        take_profit_pct: float | None, reasoning: str | None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO proposals (cycle_id, ticker, action, confidence, "
                "stop_loss_pct, take_profit_pct, reasoning, received_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cycle_id, ticker, action, confidence, stop_loss_pct,
                 take_profit_pct, reasoning, self.now_iso()),
            )
            return cur.lastrowid

    def insert_decision(self, cycle_id: int, ticker: str, approved: bool,
                        shares: int | None, entry_price: float | None,
                        stop_price: float | None, target_price: float | None,
                        reason: str, config_hash: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO decisions (cycle_id, ticker, approved, shares, "
                "entry_price, stop_price, target_price, reason, config_hash, decided_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, ticker, int(approved), shares, entry_price,
                 stop_price, target_price, reason, config_hash, self.now_iso()),
            )
            return cur.lastrowid
    def insert_order(self, cycle_id: int, ticker: str, side: str, order_type: str,
                     qty: float | None, limit_price: float | None,
                     stop_price: float | None, take_profit_price: float | None,
                     client_order_id: str | None, status: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO orders (cycle_id, ticker, side, order_type, qty, "
                "limit_price, stop_price, take_profit_price, client_order_id, "
                "status, submitted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, ticker, side, order_type, qty, limit_price,
                 stop_price, take_profit_price, client_order_id, status,
                 self.now_iso()),
            )
            return cur.lastrowid

    def update_order_fill(self, client_order_id: str, filled_at: str,
                          stop_live_at: str | None = None) -> None:
        with self.transaction() as conn:
            if stop_live_at is not None:
                conn.execute(
                    "UPDATE orders SET filled_at=?, stop_live_at=? WHERE client_order_id=?",
                    (filled_at, stop_live_at, client_order_id),
                )
            else:
                conn.execute(
                    "UPDATE orders SET filled_at=? WHERE client_order_id=?",
                    (filled_at, client_order_id),
                )

    def insert_equity_snapshot(self, equity: float, cash: float, buying_power: float,
                               daily_start_equity: float | None,
                               high_water_mark: float, drawdown_pct: float) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO equity_snapshots (taken_at, equity, cash, buying_power, "
                "daily_start_equity, high_water_mark, drawdown_pct) VALUES (?,?,?,?,?,?,?)",
                (self.now_iso(), equity, cash, buying_power, daily_start_equity,
                 high_water_mark, drawdown_pct),
            )
            return cur.lastrowid

    def insert_alert(self, kind: str, message: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO alerts (sent_at, kind, message) VALUES (?,?,?)",
                (self.now_iso(), kind, message),
            )
            return cur.lastrowid
"""