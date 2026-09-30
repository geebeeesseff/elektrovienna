"""Resume durable pages without depending on Gmail API response shapes."""

import json

from .models import MailReader, ProviderError
from .state import InventoryState


def scan_key(mailbox: str, cutoff_ms: int, mode: str) -> str:
    return f"v1:{mailbox}:{cutoff_ms}:{mode}"


def inventory(reader: MailReader, state: InventoryState, mailbox: str, cutoff_ms: int,
              limit: int | None = None, page_size: int = 100) -> dict:
    if limit is not None and limit <= 0 or not 1 <= page_size <= 500:
        raise ValueError("Limit must be positive; page size must be between 1 and 500.")
    if reader.mailbox() != mailbox.casefold():
        raise ProviderError("mailbox_identity_mismatch")
    mode = "validation" if limit is not None else "historical"
    key = scan_key(mailbox, cutoff_ms, mode)
    state.begin(key, mailbox, cutoff_ms)
    qualifying = 0
    examined = 0
    while not state.scan(key)["completed"]:
        scan = state.scan(key)
        if not scan["page_loaded"]:
            try:
                page = reader.list_messages(cutoff_ms, scan["token"], min(page_size, limit or page_size))
                if page.next_token is not None and page.next_token in json.loads(scan["seen_tokens"]):
                    raise ProviderError("pagination_cycle")
                state.queue_page(key, page)
            except ProviderError as exc:
                state.fail(key, None, exc.code)
                raise
        for identity in state.pending(key):
            try:
                message = reader.get_message(identity)
                if message.message_id != identity:
                    raise ProviderError("message_identity_mismatch")
                included = message.qualifies(cutoff_ms)
                state.record(key, mailbox, message, included)
            except ProviderError as exc:
                state.fail(key, identity, exc.code)
                raise
            examined += 1
            qualifying += int(included)
            if limit is not None and qualifying >= limit:
                if not state.pending(key):
                    state.advance(key)
                return {"examined": examined, "qualifying": qualifying, "completed": bool(state.scan(key)["completed"])}
        state.advance(key)
    return {"examined": examined, "qualifying": qualifying, "completed": True}
