"""Synthetic provider data only; these tests never access a real mailbox."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from elektro_vienna.inventory import inventory, scan_key
from elektro_vienna.models import Attachment, Message, Page, ProviderError, historical_cutoff_ms
from elektro_vienna.state import SQLiteState, statistics, single_operator

MAILBOX = "office@elektrovienna.at"
CUTOFF = historical_cutoff_ms()


def message(identity="a", labels=(), timestamp=CUTOFF):
    return Message(identity, "shared-thread", timestamp, (("from", "synthetic@example.invalid"),), labels,
                   (Attachment("1", "same-provider-attachment", "fixture.txt", "text/plain", 12),))


class Reader:
    def __init__(self, messages=None, pages=None):
        self.messages = messages or {"a": message("a"), "b": message("b"), "c": message("c")}
        self.pages = pages or {None: Page(("a", "b"), "second"), "second": Page(("c",), None)}
        self.listed = []
        self.fetched = []
        self.fail_id = None

    def mailbox(self):
        return MAILBOX

    def list_messages(self, cutoff_ms, token, size):
        self.listed.append(token)
        return self.pages[token]

    def get_message(self, identity):
        self.fetched.append(identity)
        if identity == self.fail_id:
            raise ProviderError("gmail_http_503")
        return self.messages[identity]


@pytest.fixture
def state(tmp_path):
    state = SQLiteState(tmp_path / "pipeline.sqlite3")
    yield state
    state.close()


def run(reader, state, **kwargs):
    return inventory(reader, state, MAILBOX, CUTOFF, **kwargs)


def test_vienna_cutoff_and_dst():
    assert datetime.fromtimestamp(CUTOFF / 1000, timezone.utc).isoformat() == "2025-12-31T23:00:00+00:00"
    assert datetime(2026, 7, 1, tzinfo=ZoneInfo("Europe/Vienna")).utcoffset().total_seconds() == 7200


@pytest.mark.parametrize("delta,expected", [(-1, False), (0, True), (1, True)])
def test_exact_boundary(delta, expected):
    assert message(timestamp=CUTOFF + delta).qualifies(CUTOFF) is expected


@pytest.mark.parametrize("labels,expected", [((), True), (("INBOX",), True), (("SENT",), True),
    (("SPAM",), False), (("TRASH",), False), (("DRAFT",), False), (("SENT", "TRASH"), False)])
def test_coverage_labels(labels, expected):
    assert message(labels=labels).qualifies(CUTOFF) is expected


def test_pagination_idempotency_and_identity(state, tmp_path):
    reader = Reader()
    assert run(reader, state) == {"examined": 3, "qualifying": 3, "completed": True}
    assert reader.listed == [None, "second"]
    run(reader, state)
    assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 3
    assert state.db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 3
    assert state.db.execute("SELECT count(*) FROM work").fetchone()[0] == 3
    assert state.db.execute("SELECT count(*) FROM scans").fetchone()[0] == 1
    assert {row[0] for row in state.db.execute("SELECT thread_id FROM messages")} == {"shared-thread"}
    assert statistics(tmp_path / "pipeline.sqlite3", MAILBOX)["originals_archived"] == 0
    other = Reader()
    other.mailbox = lambda: "other@example.invalid"
    inventory(other, state, "other@example.invalid", CUTOFF)
    assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 6
    assert state.db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 6


def test_failure_and_resume_after_reopen(tmp_path):
    path = tmp_path / "pipeline.sqlite3"
    state = SQLiteState(path)
    reader = Reader()
    reader.fail_id = "b"
    with pytest.raises(ProviderError, match="503"):
        run(reader, state)
    key = scan_key(MAILBOX, CUTOFF, "historical")
    assert state.scan(key)["token"] is None
    assert state.pending(key) == ["b"]
    assert statistics(path, MAILBOX)["scans"][0]["incomplete"] == 1
    state.close()
    state = SQLiteState(path)
    try:
        fresh = Reader()
        run(fresh, state)
        assert fresh.listed == ["second"]
        assert fresh.fetched == ["b", "c"]
        row = state.db.execute("SELECT attempts,last_error FROM work WHERE message_id='b'").fetchone()
        assert tuple(row) == (2, None)
        assert state.scan(key)["failures"] == 1
    finally:
        state.close()


def test_interruption_after_page_commit(state):
    reader = Reader()
    reader.get_message = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run(reader, state)
    fresh = Reader()
    run(fresh, state)
    assert fresh.listed == ["second"]
    assert fresh.fetched == ["a", "b", "c"]


def test_atomic_message_and_attachments(state):
    state.db.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON attachments BEGIN SELECT RAISE(ABORT,'test'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        run(Reader(), state)
    assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
    assert state.pending(scan_key(MAILBOX, CUTOFF, "historical")) == ["a", "b"]


def test_validation_limit_resumes_without_marking_historical_complete(state):
    reader = Reader()
    assert run(reader, state, limit=1)["qualifying"] == 1
    assert reader.fetched == ["a"]
    assert not state.scan(scan_key(MAILBOX, CUTOFF, "validation"))["completed"]
    assert state.scan(scan_key(MAILBOX, CUTOFF, "historical")) is None
    run(reader, state, limit=1)
    assert reader.fetched == ["a", "b"]
    assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2


def test_excluded_candidates_are_accounted_for_not_archived(state):
    reader = Reader(messages={"a": message(labels=("DRAFT",)), "b": message("b", timestamp=CUTOFF - 1), "c": message("c")})
    assert run(reader, state)["qualifying"] == 1
    assert state.db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 1
    assert state.db.execute("SELECT count(*) FROM messages WHERE status='excluded'").fetchone()[0] == 2
    assert state.db.execute("SELECT headers FROM messages WHERE message_id='a'").fetchone()[0] == "[]"


def test_wrong_account_has_no_inventory_state(state):
    reader = Reader()
    reader.mailbox = lambda: "wrong@example.invalid"
    with pytest.raises(ProviderError, match="mailbox_identity"):
        run(reader, state)
    assert state.db.execute("SELECT count(*) FROM scans").fetchone()[0] == 0


def test_page_failure_and_expired_token_restart_preserve_data(state):
    reader = Reader()
    original = reader.list_messages
    def fail_second(cutoff, token, size):
        if token:
            raise ProviderError("gmail_http_400")
        return original(cutoff, token, size)
    reader.list_messages = fail_second
    with pytest.raises(ProviderError):
        run(reader, state)
    key = scan_key(MAILBOX, CUTOFF, "historical")
    assert state.scan(key)["token"] == "second"
    state.restart_checkpoint(key)
    assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    run(Reader(), state)
    assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 3


def test_pagination_cycle_fails_without_advancing(state):
    reader = Reader(pages={None: Page((), "repeat"), "repeat": Page((), "repeat")})
    with pytest.raises(ProviderError, match="pagination_cycle"):
        run(reader, state)
    assert state.scan(scan_key(MAILBOX, CUTOFF, "historical"))["token"] == "repeat"


def test_duplicate_page_ids_and_empty_pages(state):
    reader = Reader(pages={None: Page((), "next"), "next": Page(("a", "a"), None)})
    run(reader, state)
    assert reader.fetched == ["a"]


def test_message_identity_mismatch_is_pending(state):
    reader = Reader(messages={"a": message("unexpected")})
    with pytest.raises(ProviderError, match="identity_mismatch"):
        run(reader, state)
    assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_single_operator_lock(tmp_path):
    path = tmp_path / "inventory.lock"
    with single_operator(path):
        with pytest.raises(ValueError, match="Another inventory"):
            with single_operator(path):
                pytest.fail("Two writers were admitted")
    with single_operator(path):
        pass


def test_missing_statistics_does_not_create_database(tmp_path):
    path = tmp_path / "absent.sqlite3"
    assert statistics(path, MAILBOX)["discovered"] == 0
    assert not path.exists()
