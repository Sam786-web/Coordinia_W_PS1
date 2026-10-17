"""Outbound notifications: Telegram (per person) and an optional Slack team feed.
Falls back to a 'simulated' delivery when no bot token / chat id is set."""
import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

TELEGRAM_API = "https://api.telegram.org"


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if value.lower().startswith("your-") else value


def telegram_token() -> str:
    return _env("TELEGRAM_BOT_TOKEN")


TELEGRAM_ATTEMPTS = 3  # short Wi-Fi / DNS blips are common at hackathons: retry before giving up


def send_telegram(name: str, chat_id: Optional[str], text: str) -> dict:
    """Send one message. `error` explains a failure: not_linked | network | telegram (rejected by Telegram)."""
    token = telegram_token()
    if not token or not chat_id:
        reason = "no TELEGRAM_BOT_TOKEN" if not token else f"{name} has no telegram_chat_id (ask them to verify with their PIN)"
        return {"to": name, "channel": "simulated", "delivered": False, "error": "not_linked", "reason": reason, "text": text}
    last = ""
    for attempt in range(TELEGRAM_ATTEMPTS):
        try:
            resp = httpx.post(f"{TELEGRAM_API}/bot{token}/sendMessage",
                              json={"chat_id": chat_id, "text": text}, timeout=6)
            if resp.status_code == 200 and resp.json().get("ok", False):
                return {"to": name, "channel": "telegram", "delivered": True, "reason": None, "text": text,
                        "attempts": attempt + 1}
            if resp.status_code < 500 and resp.status_code != 429:  # e.g. the person blocked the bot: retrying won't help
                return {"to": name, "channel": "telegram", "delivered": False, "error": "telegram",
                        "reason": resp.text[:200], "text": text}
            last = resp.text[:200]
        except httpx.HTTPError as exc:  # DNS failure, timeout, connection reset
            last = f"{type(exc).__name__}: {str(exc)[:160]}"
        time.sleep(1.0 * (attempt + 1))
    return {"to": name, "channel": "telegram", "delivered": False, "error": "network",
            "reason": f"Couldn't reach Telegram after {TELEGRAM_ATTEMPTS} tries ({last})", "text": text}


SETTINGS_FILE = Path(__file__).resolve().parent / ".settings.json"


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def slack_settings() -> dict:
    """Slack is an opt-in add-on: a webhook URL (from the dashboard or SLACK_WEBHOOK_URL) plus an on/off switch."""
    saved = _load_settings()
    url = saved.get("slack_webhook_url") or _env("SLACK_WEBHOOK_URL")
    return {"enabled": bool(saved.get("slack_enabled", bool(url))) and bool(url), "webhook_url": url or ""}


def save_slack_settings(enabled: bool, webhook_url: Optional[str]) -> dict:
    saved = _load_settings()
    if webhook_url is not None:
        saved["slack_webhook_url"] = webhook_url.strip()
    saved["slack_enabled"] = enabled
    SETTINGS_FILE.write_text(json.dumps(saved, indent=2))
    return slack_settings()


def send_slack(text: str) -> Optional[dict]:
    """Post to the Slack incoming webhook (team feed) when the add-on is enabled; otherwise do nothing."""
    cfg = slack_settings()
    url = cfg["webhook_url"] if cfg["enabled"] else ""
    if not url:
        return None
    try:
        resp = httpx.post(url, json={"text": text}, timeout=5)
        return {"channel": "slack", "delivered": resp.status_code == 200, "reason": None if resp.status_code == 200 else resp.text[:200]}
    except httpx.HTTPError as exc:
        return {"channel": "slack", "delivered": False, "reason": str(exc)[:200]}
