"""Manual original acquisition for the existing Phase 1 inventory only."""

import sqlite3
from urllib.parse import quote

from .archive_store import SourceArchive, source_reference
from .models import ProviderError, SourceReader
from .state import SQLiteState


def archive(reader: SourceReader, state: SQLiteState, store: SourceArchive,
            mailbox: str, cutoff: int, limit: int | None = None) -> dict:
    if reader.mailbox() != mailbox.casefold():
        raise ProviderError("mailbox_identity_mismatch")
    candidates = state.archive_candidates(mailbox, cutoff)
    # Pending/failed messages first, then completed messages for integrity checking.
    candidates.sort(key=lambda message: state.archive_complete(mailbox, message))
    checked = 0
    for message in candidates[:limit]:
        message_id = message.message_id
        locator = "gmail://" + quote(mailbox, safe="") + "/messages/" + quote(message_id, safe="")

        def acquire(kind, part_id, download, attachment=None):
            reference = source_reference(mailbox, message_id, None if kind == "message" else part_id)
            record = state.archived_file(mailbox, message_id, kind, part_id)
            if record is not None:
                try:
                    if record.reference != reference:
                        raise ProviderError("archive_reference_mismatch")
                    store.verify(record)
                except (ProviderError, OSError):
                    state.archive_verified(mailbox, message_id, kind, part_id, False)
                    raise
                state.archive_verified(mailbox, message_id, kind, part_id, True)
            else:
                record = store.publish(reference, download())
                source = locator if kind == "message" else locator + "/parts/" + quote(part_id, safe="")
                anomaly = ("provider_size_mismatch" if attachment is not None
                           and attachment.attachment_id is None
                           and record.byte_length != attachment.size else None)
                state.record_archive(mailbox, message_id, kind, part_id, record, source, anomaly)

        def original():
            source = reader.get_original(message_id)
            if (source.message_id != message_id or source.thread_id != message.thread_id
                    or source.internal_ms != message.internal_ms):
                raise ProviderError("message_identity_mismatch")
            return source.data

        try:
            acquire("message", "", original)
            for attachment in message.attachments:
                acquire("attachment", attachment.part_id, lambda: reader.get_attachment(message_id, attachment), attachment)
            state.archive_result(mailbox, message_id)
            checked += 1
        except KeyboardInterrupt:
            state.archive_result(mailbox, message_id, "archive_interrupted")
            raise
        except (ProviderError, OSError, sqlite3.Error) as exc:
            code = exc.code if isinstance(exc, ProviderError) else "archive_local_io_failure"
            state.archive_result(mailbox, message_id, code)
            raise ProviderError(code) from None
    return {"messages_checked": checked, "eligible": len(candidates)}
