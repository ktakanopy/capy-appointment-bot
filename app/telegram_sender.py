"""Send saved replies through the Telegram Bot API."""

from __future__ import annotations

import random
import time

from app.channels.telegram import TelegramApiError
from app.channels.telegram_delivery import TelegramOutbox
from app.observability import debug_event, log_event

MAX_ATTEMPTS = 5


def deliver_claimed(outbox: TelegramOutbox, logger, job: dict) -> None:
    """Call Telegram once for a reserved send and persist its classified outcome."""
    started = time.monotonic()
    kind = "reply"
    debug_event("telegram.outbox.claimed", bot_id=job["bot_id"], update_id=job.get("update_id"),
                kind=kind, attempt=job["attempts"])
    try:
        outbox.bot.client().send_message(job["chat_id"], job["message"], job["reply_markup"])
    except TelegramApiError as error:
        if error.code == "telegram_transport_error":
            outcome = "ambiguous"
        elif error.is_permanent_rejection or job["attempts"] >= MAX_ATTEMPTS:
            outcome = "failed"
        else:
            outcome = "ready"
        delay = error.retry_after or min(2 ** job["attempts"], 60) + random.random()
        recorded = outbox.finish(job, outcome, error=error.code, retry_after=delay)
    except RuntimeError:
        outcome = "failed"
        recorded = outbox.finish(job, outcome, error="delivery_configuration")
    else:
        outcome = "sent"
        recorded = outbox.finish(job, outcome)
    debug_event("telegram.outbox.result", bot_id=job["bot_id"], update_id=job.get("update_id"),
                kind=kind, outcome=outcome if recorded else "claim_lost")
    log_event(logger, "telegram.outbox.finished", bot_id=job["bot_id"],
              outcome=outcome if recorded else "claim_lost", provider_outcome=outcome,
              kind=kind,
              elapsed_since_origin_ms=round((time.time() - job["origin_at"].timestamp()) * 1000),
              duration_ms=round((time.monotonic() - started) * 1000))
