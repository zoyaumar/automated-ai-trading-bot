"""Telegram notifications + heartbeat dead-man's-switch.

Long polling only — no public web endpoint. Commands accepted only from
the client's chat ID.
"""

from __future__ import annotations

import asyncio
import html
import os
import time
from typing import Callable

import requests


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str | int,
                 heartbeat_url: str | None = None,
                 heartbeat_minutes: int = 5,
                 alert_after_minutes: int = 15) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.heartbeat_url = heartbeat_url
        self.heartbeat_minutes = heartbeat_minutes
        self.alert_after_minutes = alert_after_minutes
        self._last_ping: float | None = None
        self._offset: int | None = None

    # --- send ---
    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        escaped = html.escape(text) if parse_mode == "HTML" else text
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": escaped, "parse_mode": parse_mode},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def send_trade_notification(self, ticker: str, side: str, qty: float,
                                entry: float, stop: float, target: float) -> None:
        msg = (
            f"*Order placed*\n{symbol_side}\n"
            f"Ticker: `{ticker}`\nQty: {qty}\nEntry: ${entry:.4f}\n"
            f"Stop: ${stop:.4f}\nTarget: ${target:.4f}"
        ).replace("symbol_side", f"Side: {side}")
        self.send_message(msg)

    def send_alert(self, kind: str, message: str) -> None:
        self.send_message(f"*[{kind}]*\n{message}")

    def send_daily_summary(self, equity: float, day_pnl: float,
                           positions: list[str], proposals: int,
                           approvals: int, rejections: int,
                           top_rejection_reasons: list[str], ai_cost: float,
                           config_hash: str) -> None:
        lines = [
            "*Daily Summary*",
            f"Equity: ${equity:,.2f}\nDay P&L: ${day_pnl:+.2f}",
            f"Open positions: {', '.join(positions) or 'none'}",
            f"Proposals: {proposals}, Approved: {approvals}, Rejected: {rejections}",
        ]
        if top_rejection_reasons:
            lines.append("*Top rejection reasons:*")
            lines.extend(f"• {r}" for r in top_rejection_reasons[:5])
        lines += [f"AI cost: ${ai_cost:.4f}", f"Config hash: `{config_hash}`"]
        self.send_message("\n".join(lines))

    # --- heartbeat ---
    def ping_heartbeat(self) -> bool:
        if not self.heartbeat_url:
            return True
        self._last_ping = time.time()
        try:
            resp = requests.get(self.heartbeat_url, timeout=10)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def heartbeat_stale(self) -> bool:
        if self._last_ping is None:
            return False
        return (time.time() - self._last_ping) > (self.alert_after_minutes * 60)

    # --- command polling ---
    def poll_commands(self) -> list[dict[str, Any]]:
        """Long-poll Telegram for commands. Returns only allowed chat ID messages."""
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self._offset, "timeout": 30, "allowed_updates": ["message"]},
                timeout=35,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            commands: list[dict[str, Any]] = []
            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                msg = update.get("message", {})
                if str(msg.get("chat", {}).get("id")) != self.chat_id:
                    continue  # ignore unauthorized
                text = msg.get("text", "").strip()
                if text.startswith("/"):
                    commands.append({"command": text, "chat_id": self.chat_id})
            return commands
        except requests.RequestException:
            return []