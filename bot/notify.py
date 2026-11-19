"""Telegram notifications + heartbeat dead-man's-switch.

Long polling only — no public web endpoint. Commands accepted only from
the client's chat ID.

Messages are sent with parse_mode=HTML. Anything building its own text for
send_message must escape dynamic values (see _e). Failures are logged without
exception messages or URLs, since both can contain the bot token.
"""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096
SAFE_MESSAGE_LEN = 4000
MAX_RETRY_DELAY = 10.0
POLL_TIMEOUT = 30
MAX_POLL_BACKOFF = 60.0
_TAG_RE = re.compile(r"<[^>]+>")


# --- helpers ---


def _e(value: Any) -> str:
    """Escape a dynamic value for Telegram HTML."""
    return html.escape(str(getattr(value, "value", value)), quote=False)


def _strip_html(text: str) -> str:
    """Convert our HTML-formatted text to plain text (real tags only; dynamic
    '<' was escaped, so unescape after stripping)."""
    return html.unescape(_TAG_RE.sub("", text))


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_qty(qty: float) -> str:
    return f"{qty:,.4f}".rstrip("0").rstrip(".")


def _fmt_signed_money(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}${abs(value):,.2f}"


class HeartbeatMonitor:
    """Independent dead-man's-switch HTTP monitor.

    Periodically pings an external monitor (Healthchecks.io, BetterStack, etc.).
    Decoupled from messaging channels like Telegram.
    """

    def __init__(self, heartbeat_url: str | None = None,
                 alert_after_minutes: int = 15) -> None:
        self.heartbeat_url = heartbeat_url.strip() if heartbeat_url else ""
        self.alert_after_minutes = alert_after_minutes
        self._last_ok_ping: float | None = time.monotonic() if self.heartbeat_url else None
        self._alert_sent: bool = False

    def ping(self) -> bool:
        """Ping the heartbeat URL. Returns True if ping succeeded or no URL configured."""
        if not self.heartbeat_url:
            return True
        try:
            resp = requests.get(self.heartbeat_url, timeout=10)
        except requests.RequestException as exc:
            # Don't log the URL: heartbeat ping URLs are effectively secrets.
            logger.error("Heartbeat ping failed: %s", type(exc).__name__)
            return False
        if not resp.ok:
            logger.error("Heartbeat ping failed: HTTP %s", resp.status_code)
            return False
        self._last_ok_ping = time.monotonic()
        self._alert_sent = False  # reset alert on successful ping
        return True

    def is_stale(self) -> bool:
        """True if no successful ping has occurred within alert_after_minutes."""
        if not self.heartbeat_url or self._last_ok_ping is None:
            return False
        return (time.monotonic() - self._last_ok_ping) > (self.alert_after_minutes * 60)

    @property
    def alert_sent(self) -> bool:
        return self._alert_sent

    def mark_alert_sent(self) -> None:
        self._alert_sent = True


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str | int,
                 heartbeat_url: str | None = None,
                 heartbeat_minutes: int = 5,
                 alert_after_minutes: int = 15,
                 allowed_user_id: str | int | None = None,
                 max_command_age_seconds: float = 120.0,
                 heartbeat_monitor: HeartbeatMonitor | None = None) -> None:
        if not bot_token or not str(chat_id):
            raise ValueError("bot_token and chat_id are required")
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.allowed_user_id = str(allowed_user_id) if allowed_user_id is not None else None
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.heartbeat_monitor = heartbeat_monitor or HeartbeatMonitor(
            heartbeat_url=heartbeat_url, alert_after_minutes=alert_after_minutes
        )
        self.heartbeat_url = heartbeat_url
        self.heartbeat_minutes = heartbeat_minutes
        self.alert_after_minutes = alert_after_minutes
        self.max_command_age_seconds = max_command_age_seconds

        self._offset: int | None = None
        self._poll_backoff: float = 0.0

        if self.chat_id.startswith("-") and self.allowed_user_id is None:
            logger.warning(
                "chat_id looks like a group/channel; any member could send commands. "
                "Pass allowed_user_id to restrict commands to one user."
            )

    # --- send ---

    def _send(self, text: str, parse_mode: str | None) -> tuple[bool, int | None, float]:
        """One sendMessage attempt. Returns (ok, http_status, retry_after)."""
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            resp = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=10)
        except requests.RequestException as exc:
            # Don't log the message: requests errors can include the URL (token).
            logger.error("Telegram sendMessage failed: %s", type(exc).__name__)
            return False, None, 0.0

        if resp.status_code == 200:
            return True, 200, 0.0

        retry_after = 0.0
        description = ""
        try:
            body = resp.json()
            description = str(body.get("description", ""))[:200]
            retry_after = float((body.get("parameters") or {}).get("retry_after", 0))
        except (ValueError, TypeError, AttributeError):
            pass
        logger.error("Telegram sendMessage failed: HTTP %s %s", resp.status_code, description)
        return False, resp.status_code, retry_after

    def send_message(self, text: str, parse_mode: str | None = "HTML") -> bool:
        """Send a message. Returns True if Telegram accepted it.

        With the default HTML mode, dynamic values in `text` must already be
        escaped (use _e). If formatting is rejected, the message is resent as
        plain text rather than dropped. Retries once on network errors, 5xx and 429.
        """
        if len(text) > MAX_MESSAGE_LEN:
            # Truncating HTML can cut a tag in half, so fall back to plain text.
            text = _truncate(_strip_html(text), SAFE_MESSAGE_LEN)
            parse_mode = None

        ok, status, retry_after = self._send(text, parse_mode)
        if ok:
            return True

        if status == 429 and retry_after > MAX_RETRY_DELAY:
            logger.error("Telegram rate limit: retry_after=%.0fs is too long; dropping message",
                         retry_after)
            return False
        if status is None or status == 429 or status >= 500:
            time.sleep(retry_after if status == 429 and retry_after else 1.0)
            ok, status, _ = self._send(text, parse_mode)
            if ok:
                return True

        if status == 400 and parse_mode is not None:
            # Formatting rejected. Don't lose the message; resend as plain text.
            ok, _, _ = self._send(_truncate(_strip_html(text), SAFE_MESSAGE_LEN), None)
            return ok
        return False

    def send_trade_notification(self, ticker: str, side: str, qty: float,
                                entry: float, stop: float, target: float) -> bool:
        msg = (
            f"<b>Order placed</b>\n<b>Side:</b> {_e(side)}\n"
            f"<b>Ticker:</b> <code>{_e(ticker)}</code>\n<b>Qty:</b> {_fmt_qty(qty)}\n"
            f"<b>Entry:</b> ${entry:.4f}\n<b>Stop:</b> ${stop:.4f}\n"
            f"<b>Target:</b> ${target:.4f}"
        )
        return self.send_message(msg)

    def send_order_filled(self, ticker: str, side: str, qty: float,
                          filled_price: float) -> bool:
        msg = (
            f"<b>Order filled</b>\n<b>Side:</b> {_e(side)}\n"
            f"<b>Ticker:</b> <code>{_e(ticker)}</code>\n<b>Qty:</b> {_fmt_qty(qty)}\n"
            f"<b>Fill price:</b> ${filled_price:.4f}"
        )
        return self.send_message(msg)

    def send_alert(self, kind: str, message: str) -> bool:
        # Truncate the raw text before escaping so we never cut an entity in half.
        return self.send_message(f"<b>[{_e(kind)}]</b>\n{_e(_truncate(message, 3500))}")

    def send_daily_summary(self, equity: float, day_pnl: float,
                           positions: list[str], proposals: int,
                           approvals: int, rejections: int,
                           top_rejection_reasons: list[str], ai_cost: float,
                           config_hash: str) -> bool:
        lines = [
            "<b>Daily Summary</b>",
            f"Equity: ${equity:,.2f}\nDay P&amp;L: {_fmt_signed_money(day_pnl)}",
            f"Open positions: {_e(', '.join(positions)) if positions else 'none'}",
            f"Proposals: {proposals}, Approved: {approvals}, Rejected: {rejections}",
        ]
        if top_rejection_reasons:
            lines.append("<b>Top rejection reasons:</b>")
            lines.extend(f"• {_e(_truncate(r, 300))}" for r in top_rejection_reasons[:5])
        lines += [f"AI cost: ${ai_cost:.4f}", f"Config hash: <code>{_e(config_hash)}</code>"]
        return self.send_message("\n".join(lines))

    # --- heartbeat (delegated to HeartbeatMonitor) ---

    def ping_heartbeat(self) -> bool:
        """Ping the heartbeat URL via the decoupled monitor."""
        return self.heartbeat_monitor.ping()

    def heartbeat_stale(self) -> bool:
        """True if there has been no successful ping for alert_after_minutes."""
        return self.heartbeat_monitor.is_stale()

    def heartbeat_alert_sent(self) -> bool:
        return self.heartbeat_monitor.alert_sent

    def mark_heartbeat_alert_sent(self) -> None:
        self.heartbeat_monitor.mark_alert_sent()

    # --- command polling ---

    @staticmethod
    def _parse_command(text: str) -> tuple[str, str]:
        """'/Halt@MyBot now please' -> ('/halt', 'now please')."""
        parts = text.split(None, 1)
        head = parts[0]
        args = parts[1].strip() if len(parts) > 1 else ""
        return head.split("@", 1)[0].lower(), args

    def _poll_failed(self) -> list[dict[str, Any]]:
        self._poll_backoff = min(max(self._poll_backoff * 2, 2.0), MAX_POLL_BACKOFF)
        time.sleep(self._poll_backoff)
        return []

    def poll_commands(self) -> list[dict[str, Any]]:
        """Long-poll Telegram for commands. Returns only authorized messages.

        Blocks for up to ~30s, so run it in its own thread, not in the trading
        loop. Commands older than max_command_age_seconds are consumed and
        ignored, so a restart can't replay stale commands. Sleeps with growing
        backoff after errors so a bad token or a 409 can't spin the caller.
        Each item: {"command": raw text, "name": "/halt", "args": "...", "chat_id": ...}.
        """
        payload: dict[str, Any] = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
        if self._offset is not None:
            payload["offset"] = self._offset

        try:
            resp = requests.post(
                f"{self.base_url}/getUpdates", json=payload, timeout=POLL_TIMEOUT + 5
            )
            if resp.status_code != 200:
                if resp.status_code == 409:
                    logger.error(
                        "getUpdates conflict (HTTP 409): another process is polling this "
                        "bot, or a webhook is set"
                    )
                else:
                    logger.error("getUpdates failed: HTTP %s", resp.status_code)
                return self._poll_failed()
            data = resp.json()
        except requests.Timeout:
            return []  # long poll expired without a response; just poll again
        except (requests.RequestException, ValueError) as exc:
            logger.error("getUpdates failed: %s", type(exc).__name__)
            return self._poll_failed()

        if not isinstance(data, dict) or not data.get("ok"):
            logger.error("getUpdates returned ok=false")
            return self._poll_failed()

        self._poll_backoff = 0.0
        commands: list[dict[str, Any]] = []
        for update in data.get("result", []):
            uid = update.get("update_id")
            if isinstance(uid, int):
                self._offset = uid + 1  # always advance, even for ignored messages

            msg = update.get("message") or {}
            chat = msg.get("chat") or {}
            sender = msg.get("from") or {}

            if str(chat.get("id")) != self.chat_id:
                logger.warning("Ignored message from unauthorized chat %s", chat.get("id"))
                continue
            if self.allowed_user_id is not None and str(sender.get("id")) != self.allowed_user_id:
                logger.warning("Ignored message from unauthorized user %s", sender.get("id"))
                continue

            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue

            try:
                age = time.time() - float(msg["date"])
            except (KeyError, TypeError, ValueError):
                age = float("inf")
            if age > self.max_command_age_seconds:
                logger.warning("Ignored stale command (%.0fs old)", age)
                continue

            name, args = self._parse_command(text)
            commands.append(
                {"command": text, "name": name, "args": args, "chat_id": self.chat_id}
            )
        return commands