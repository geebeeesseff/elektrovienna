"""Provider-independent discovery records; never original archives or knowledge."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
EXCLUDED_LABELS = frozenset({"SPAM", "TRASH", "DRAFT"})


def historical_cutoff_ms() -> int:
    return int(datetime(2026, 1, 1, tzinfo=ZoneInfo("Europe/Vienna")).timestamp() * 1000)


class ProviderError(Exception):
    """Sanitized error code suitable for durable state and operator output."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Attachment:
    part_id: str
    attachment_id: str | None
    filename: str
    mime_type: str
    size: int


@dataclass(frozen=True)
class Message:
    message_id: str
    thread_id: str
    internal_ms: int
    headers: tuple[tuple[str, str], ...]
    labels: tuple[str, ...]
    attachments: tuple[Attachment, ...]

    def qualifies(self, cutoff_ms: int) -> bool:
        return self.internal_ms >= cutoff_ms and not EXCLUDED_LABELS.intersection(self.labels)


@dataclass(frozen=True)
class Page:
    message_ids: tuple[str, ...]
    next_token: str | None


class MailReader(Protocol):
    def mailbox(self) -> str: ...
    def list_messages(self, cutoff_ms: int, token: str | None, size: int) -> Page: ...
    def get_message(self, message_id: str) -> Message: ...
