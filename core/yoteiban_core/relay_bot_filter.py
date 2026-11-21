"""Configured relay-bot utterances are classified and counted, never made candidate inputs."""
from __future__ import annotations

import unicodedata


def classify_relay_bot(text: str, sender: str, config: list[dict] | None) -> str | None:
    body = unicodedata.normalize("NFKC", str(text or "")).lstrip()
    sender_id = str(sender or "").strip().casefold()
    for bot in config or []:
        if not isinstance(bot, dict):
            continue
        sender_ids = {str(x).strip().casefold() for x in bot.get("sender_handles", []) if x}
        owner = bot.get("owner")
        if owner not in {"self", "family"}:
            continue
        if sender_id and sender_id in sender_ids:
            return "self_relay_bot_notice" if owner == "self" else "relay_bot_message"
        calls = [str(x) for x in bot.get("call_markers", []) if x]
        replies = [str(x) for x in bot.get("reply_markers", []) if x]
        if any(body.startswith(marker) for marker in calls):
            return "relay_bot_call"
        # Reply marks are emitted at the start of the bot's own text. A marker
        # quoted or discussed in the middle of a normal message is not a bot reply.
        if any(body.startswith(marker) for marker in replies):
            return "relay_bot_message"
    return None
