"""Only synthetic bytes, identities, and temporary local archive roots."""

import hashlib
import base64
import json
import os
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from elektro_vienna import archive_store, cli
from elektro_vienna.archive import archive
from elektro_vienna.archive_store import SourceArchive, source_reference
from elektro_vienna.config import Config
from elektro_vienna.gmail import GmailReader
from elektro_vienna.models import Attachment, Message, OriginalMessage, Page, ProviderError, historical_cutoff_ms
from elektro_vienna.state import ARCHIVE_SCHEMA, SCHEMA, SQLiteState, statistics

MAILBOX = "synthetic@example.invalid"
CUTOFF = historical_cutoff_ms()
RAW = b"From: synthetic@example.invalid\r\nSubject: synthetic\r\n\r\nOriginal\xff\r\n"
DATA = b"synthetic attachment\x00\xff"


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    monkeypatch.setattr(archive_store, "AUTHORIZED_ROOT", root)
    monkeypatch.setattr(archive_store, "local_app_data", lambda: tmp_path / "local")
    return SourceArchive()


def seed(state, mailbox=MAILBOX, identity="a", *, timestamp=CUTOFF, labels=(), included=True):
    attachment = Attachment("0.1", "opaque", "../../CON:private.pdf", "application/octet-stream", len(DATA))
    message = Message(identity, "thread", timestamp, (), labels, (attachment,))
    state.begin("synthetic", mailbox, CUTOFF)
    state.queue_page("synthetic", Page((identity,), "next"))
    state.record("synthetic", mailbox, message, included)
    return message


@pytest.fixture
def state(tmp_path):
    state = SQLiteState(tmp_path / "state.sqlite3")
    seed(state)
    yield state
    state.close()


def reader():
    source = Mock()
    source.mailbox.return_value = MAILBOX
    source.get_original.side_effect = lambda identity: OriginalMessage(identity, "thread", CUTOFF, RAW)
    source.get_attachment.return_value = DATA
    return source


def run(source, state, store, limit=None):
    return archive(source, state, store, MAILBOX, CUTOFF, limit)


def test_exact_root_rejects_alternatives_and_does_not_create(tmp_path, store):
    for path in (tmp_path, tmp_path / "OneDrive", tmp_path / "local", Path("relative"), store.root / "child"):
        with pytest.raises(ProviderError, match="archive_root_not_authorized"):
            SourceArchive(path)
    assert not store.root.exists()


@pytest.mark.parametrize("reference", ["../escape", "/absolute", "00_raw/gmail/../escape", "00_raw/gmail/a:secret",
    "00_raw\\gmail\\escape", "00_raw/gmail/CON", "00_raw/gmail/./x", "10_documents/file", "00_raw/gmail/x."])
def test_path_traversal_and_unsafe_names_rejected(store, reference):
    with pytest.raises(ProviderError, match="archive_path_unsafe"):
        store.path(reference)
    assert not store.root.exists()


def test_git_and_localappdata_roots_rejected(store, monkeypatch):
    store.root.mkdir()
    (store.root / ".git").write_text("gitdir: synthetic")
    with pytest.raises(ProviderError, match="archive_path_unsafe"):
        SourceArchive()
    (store.root / ".git").unlink()
    monkeypatch.setattr(archive_store, "local_app_data", lambda: store.root.parent)
    with pytest.raises(ProviderError, match="archive_root_not_authorized"):
        SourceArchive()


def test_redirected_root_and_child_rejected(store, monkeypatch):
    original = Path.resolve
    ref = source_reference(MAILBOX, "a")
    target = store.root.joinpath(*ref.split("/"))
    for redirected in (store.root, target):
        with monkeypatch.context() as patch:
            patch.setattr(Path, "resolve", lambda path, *args, **kwargs:
                          store.root.parent / "escape" if path == redirected else original(path, *args, **kwargs))
            with pytest.raises(ProviderError, match="archive_path_unsafe"):
                store.publish(ref, RAW) if redirected == target else SourceArchive()


def test_detectable_junction_rejected_even_when_resolution_is_unchanged(store, monkeypatch):
    store.root.mkdir()
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        return Mock(st_reparse_tag=0xA0000003, st_mode=result.st_mode) if path == store.root else result
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ProviderError, match="archive_path_unsafe"):
        SourceArchive()


def test_deterministic_distinct_paths():
    assert source_reference(MAILBOX.upper(), "a") == source_reference(MAILBOX, "a")
    refs = {source_reference(MAILBOX, "a"), source_reference(MAILBOX, "a", ""),
            source_reference(MAILBOX, "a", "0"), source_reference(MAILBOX, "b", "0"),
            source_reference("other@example.invalid", "a", "0")}
    assert len(refs) == 5
    assert all(MAILBOX not in path for path in refs)


def test_raw_attachment_integrity_metadata_and_idempotency(store, state, tmp_path):
    source = reader()
    run(source, state, store)
    before = [tuple(row) for row in state.db.execute("SELECT * FROM archive_files ORDER BY kind")]
    for kind, part, data in (("message", "", RAW), ("attachment", "0.1", DATA)):
        record = state.archived_file(MAILBOX, "a", kind, part)
        assert store.path(record.reference).read_bytes() == data
        assert record.sha256 == hashlib.sha256(data).hexdigest()
        assert record.byte_length == len(data)
        assert record.archived_at.endswith("+00:00")
    source.get_original.reset_mock()
    source.get_attachment.reset_mock()
    run(source, state, store)
    source.get_original.assert_not_called()
    source.get_attachment.assert_not_called()
    assert [tuple(row) for row in state.db.execute("SELECT * FROM archive_files ORDER BY kind")] == before
    assert state.db.execute("SELECT filename FROM attachments").fetchone()[0] == "../../CON:private.pdf"
    assert state.db.execute("SELECT source_locator FROM archive_files WHERE kind='attachment'").fetchone()[0].endswith("/messages/a/parts/0.1")
    counts = statistics(tmp_path / "state.sqlite3", MAILBOX)["archive"]
    assert counts == dict(messages_eligible=1, originals_archived=1, attachment_occurrences_eligible=1,
                         attachment_occurrences_archived=1, bytes_archived=len(RAW)+len(DATA), failures=0,
                         incomplete=0, last_error=None, anomalies={"provider_size_mismatch": 0})
    assert not list(store.root.rglob("*.tmp"))


def test_existing_correct_target_is_adopted(store, state):
    record = store.publish(source_reference(MAILBOX, "a"), RAW)
    before = store.path(record.reference).stat().st_mtime_ns
    run(reader(), state, store)
    assert store.path(record.reference).stat().st_mtime_ns == before


@pytest.mark.parametrize("same", [False, True])
def test_publication_race_never_replaces_competing_target(store, monkeypatch, same):
    operation = "rename" if os.name == "nt" else "link"
    original = getattr(os, operation)
    ref = source_reference(MAILBOX, "a")
    def publish(source, destination):
        Path(destination).write_bytes(RAW if same else b"other synthetic bytes")
        return original(source, destination)
    monkeypatch.setattr(os, operation, publish)
    if same:
        store.publish(ref, RAW)
    else:
        with pytest.raises(ProviderError, match="archive_integrity_mismatch"):
            store.publish(ref, RAW)
    assert store.path(ref).read_bytes() == (RAW if same else b"other synthetic bytes")
    assert not list(store.root.rglob("*.tmp"))


def test_interruption_before_publication_leaves_no_target(store, monkeypatch):
    ref = source_reference(MAILBOX, "a")
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", Mock(side_effect=KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            store.publish(ref, RAW)
    assert not store.path(ref).exists()
    assert not list(store.root.rglob("*.tmp"))
    store.publish(ref, RAW)
    assert store.path(ref).read_bytes() == RAW


@pytest.mark.parametrize("recorded", [False, True])
def test_mismatch_never_overwritten_and_not_complete(store, state, tmp_path, recorded):
    if recorded:
        run(reader(), state, store)
    else:
        store.publish(source_reference(MAILBOX, "a"), RAW)
    target = store.path(source_reference(MAILBOX, "a"))
    target.write_bytes(b"different synthetic content")
    with pytest.raises(ProviderError, match="archive_integrity_mismatch"):
        run(reader(), state, store)
    assert target.read_bytes() == b"different synthetic content"
    stats = statistics(tmp_path / "state.sqlite3", MAILBOX)["archive"]
    assert stats["incomplete"] == 1 and stats["failures"] == 1 and stats["originals_archived"] == 0


def test_missing_recorded_original_not_silently_replaced(store, state, tmp_path):
    run(reader(), state, store)
    store.path(source_reference(MAILBOX, "a")).unlink()
    source = reader()
    with pytest.raises(ProviderError, match="archive_file_missing"):
        run(source, state, store)
    source.get_original.assert_not_called()
    assert statistics(tmp_path / "state.sqlite3", MAILBOX)["archive"]["incomplete"] == 1


@pytest.mark.parametrize("failure", [ProviderError("gmail_transport_failure"), KeyboardInterrupt()])
def test_resume_after_attachment_failure(store, state, tmp_path, failure):
    source = reader()
    source.get_attachment.side_effect = failure
    with pytest.raises(type(failure)):
        run(source, state, store)
    assert state.archived_file(MAILBOX, "a", "message")
    assert statistics(tmp_path / "state.sqlite3", MAILBOX)["archive"]["incomplete"] == 1
    state.close()
    reopened = SQLiteState(tmp_path / "state.sqlite3")
    try:
        source = reader()
        run(source, reopened, store)
        source.get_original.assert_not_called()
        assert reopened.db.execute("SELECT count(*) FROM archive_files").fetchone()[0] == 2
        stats = statistics(tmp_path / "state.sqlite3", MAILBOX)["archive"]
        assert stats["incomplete"] == 0 and stats["failures"] == 1 and stats["last_error"] is None
    finally:
        reopened.close()


def test_resume_file_published_before_state_commit(store, state, monkeypatch):
    original = state.record_archive
    monkeypatch.setattr(state, "record_archive", Mock(side_effect=sqlite3.OperationalError("synthetic private text")))
    with pytest.raises(ProviderError, match="^archive_local_io_failure$"):
        run(reader(), state, store)
    assert store.path(source_reference(MAILBOX, "a")).read_bytes() == RAW
    monkeypatch.setattr(state, "record_archive", original)
    run(reader(), state, store)
    assert state.archive_complete(MAILBOX, state.archive_candidates(MAILBOX, CUTOFF)[0])


def test_resume_after_inline_body_failure_preserves_completed_occurrences(store, state, tmp_path):
    with state.db:
        state.db.execute("DELETE FROM attachments")
        state.db.executemany("INSERT INTO attachments VALUES(?,?,?,?,?,?,?)", [
            (MAILBOX,"a","",None,"","multipart/alternative",0),
            (MAILBOX,"a","1",None,"","text/html",3)])
    available = [False]
    requests = []
    def get(url, *, params, **kwargs):
        requests.append(params)
        if url.endswith("/profile"):
            payload = {"emailAddress":MAILBOX}
        elif params["format"] == "raw":
            payload = {"id":"a","threadId":"thread","internalDate":str(CUTOFF),
                       "raw":base64.urlsafe_b64encode(RAW).decode()}
        else:
            body = {"size":3}
            if available[0]:
                body["data"] = base64.urlsafe_b64encode(b"abc").decode()
            payload = {"id":"a","payload":{"partId":"","mimeType":"multipart/alternative",
                "body":{"size":0},"parts":[{"partId":"1","mimeType":"text/html","body":body}]}}
        response = Mock(status_code=200)
        response.json.return_value = payload
        return response
    session = Mock()
    session.get.side_effect = get
    source = GmailReader(session, sleep=lambda _:None)
    with pytest.raises(ProviderError,match="^attachment_bytes_unavailable$"):
        run(source,state,store)
    completed = [tuple(r) for r in state.db.execute("SELECT * FROM archive_files ORDER BY kind")]
    assert len(completed) == 2  # Raw source and the declared empty root body are durable.
    available[0] = True
    requests.clear()
    state.close()
    reopened = SQLiteState(tmp_path / "state.sqlite3")
    try:
        run(source,reopened,store)
        assert all(p.get("format") != "raw" for p in requests)
        assert reopened.db.execute("SELECT count(*) FROM archive_files").fetchone()[0] == 3
        for record in completed:
            assert record in [tuple(r) for r in reopened.db.execute("SELECT * FROM archive_files")]
        stats = statistics(tmp_path / "state.sqlite3",MAILBOX)["archive"]
        assert stats["attachment_occurrences_archived"] == 2
        assert stats["incomplete"] == 0 and stats["last_error"] is None and stats["failures"] == 1
    finally:
        reopened.close()


def test_equal_bytes_keep_occurrence_and_mailbox_provenance(store, state):
    seed(state, identity="b")
    source = reader()
    run(source, state, store, limit=1)
    run(source, state, store, limit=1)
    assert [call.args[0] for call in source.get_original.call_args_list] == ["a", "b"]
    seed(state, mailbox="other@example.invalid", identity="a")
    source.mailbox.return_value = "other@example.invalid"
    archive(source, state, store, "other@example.invalid", CUTOFF)
    rows = state.db.execute("SELECT reference,sha256 FROM archive_files WHERE kind='attachment'").fetchall()
    assert len(rows) == len({r[0] for r in rows}) == 3
    assert len({r[1] for r in rows}) == 1


@pytest.mark.parametrize("actual_size", [1111,1118])
def test_inline_size_anomaly_is_durable_and_bytes_immutable(store,state,tmp_path,actual_size):
    data = b"x" * (actual_size-2) + b"\x00\xff"
    with state.db:
        state.db.execute("UPDATE attachments SET attachment_id=NULL,size=1111")
    session = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {"id":"a","payload":{"partId":"0.1","mimeType":"application/octet-stream",
        "filename":"../../CON:private.pdf","body":{"size":1111,"data":base64.urlsafe_b64encode(data).decode()}}}
    session.get.return_value = response
    provider = GmailReader(session,sleep=lambda _:None)
    source = reader()
    source.get_attachment.side_effect = provider.get_attachment
    run(source,state,store)
    row = state.db.execute("SELECT * FROM archive_files WHERE kind='attachment'").fetchone()
    assert row['anomaly'] == ('provider_size_mismatch' if actual_size!=1111 else None)
    assert row['byte_length'] == actual_size
    assert row['sha256'] == hashlib.sha256(data).hexdigest()
    target = store.path(row['reference'])
    assert target.read_bytes() == data
    assert state.db.execute('SELECT size FROM attachments').fetchone()[0] == 1111
    saved = tuple(row)
    timestamp = target.stat().st_mtime_ns
    state.close()
    reopened = SQLiteState(tmp_path/'state.sqlite3')
    try:
        source.get_attachment.reset_mock()
        run(source,reopened,store)
        source.get_attachment.assert_not_called()
        assert tuple(reopened.db.execute("SELECT * FROM archive_files WHERE kind='attachment'").fetchone()) == saved
        assert target.stat().st_mtime_ns == timestamp
        counts=statistics(tmp_path/'state.sqlite3',MAILBOX)['archive']
        assert counts['anomalies']['provider_size_mismatch'] == int(actual_size!=1111)
        assert counts['incomplete']==0 and counts['failures']==0
        target.write_bytes(b'corrupt synthetic bytes')
        with pytest.raises(ProviderError,match='archive_integrity_mismatch'):
            run(source,reopened,store)
    finally:
        reopened.close()


def test_v2_to_v3_only_adds_nullable_anomaly(tmp_path):
    path=tmp_path/'v2.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.executescript(ARCHIVE_SCHEMA)
        db.execute("INSERT INTO messages(mailbox,message_id,thread_id,internal_ms,headers,labels,status) VALUES(?,'a','t',?,'[]','[]','discovered')",(MAILBOX,CUTOFF))
        db.execute("INSERT INTO archive_files(mailbox,message_id,kind,part_id,reference,sha256,byte_length,archived_at,source_locator) VALUES(?,'a','message','','synthetic','hash',7,'timestamp','locator')",(MAILBOX,))
        before=db.execute('SELECT * FROM archive_files').fetchone()
    assert statistics(path,MAILBOX)['archive']['anomalies']['provider_size_mismatch']==0
    migrated=SQLiteState(path)
    try:
        assert migrated.db.execute('PRAGMA user_version').fetchone()[0]==3
        assert tuple(migrated.db.execute('SELECT * FROM archive_files').fetchone())==before+(None,)
        assert migrated.db.execute('PRAGMA foreign_key_check').fetchall()==[]
    finally:
        migrated.close()


def test_root_attachment_is_distinct_from_raw_message_record(store, state):
    with state.db:
        state.db.execute("UPDATE attachments SET part_id='',attachment_id=NULL")
    run(reader(), state, store)
    assert state.archived_file(MAILBOX,"a","message","").reference != state.archived_file(MAILBOX,"a","attachment","").reference
    assert state.db.execute("SELECT count(*) FROM archive_files").fetchone()[0] == 2


def test_only_source_tables_and_explicit_commands(state, capsys):
    tables = {r[0] for r in state.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"messages", "attachments", "scans", "work", "archive_files", "archive_jobs"}
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])
    assert error.value.code == 0
    assert "{validate,inventory,stats,archive}" in capsys.readouterr().out


def test_only_existing_eligible_inventory_selected(store, state):
    seed(state, identity="before", timestamp=CUTOFF-1)
    seed(state, identity="excluded", included=False)
    for label in ("SPAM", "TRASH", "DRAFT"):
        seed(state, identity=label, labels=(label,))
    source = reader()
    run(source, state, store)
    source.get_original.assert_called_once_with("a")
    source.list_messages.assert_not_called()


def test_wrong_mailbox_and_source_identity_no_writes(store, state):
    source = reader()
    source.mailbox.return_value = "wrong@example.invalid"
    with pytest.raises(ProviderError, match="mailbox_identity_mismatch"):
        run(source, state, store)
    assert not store.root.exists()
    source = reader()
    source.get_original.side_effect = None
    source.get_original.return_value = OriginalMessage("a", "wrong-thread", CUTOFF, RAW)
    with pytest.raises(ProviderError, match="message_identity_mismatch"):
        run(source, state, store)
    assert not store.root.exists()


def test_v1_migration_preserves_inventory_and_checkpoint(tmp_path):
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO scans(scan_key,mailbox,cutoff_ms,token,failures) VALUES('old',?,?,'cursor',2)", (MAILBOX,CUTOFF))
        db.execute("INSERT INTO messages(mailbox,message_id,thread_id,internal_ms,headers,labels,status) VALUES(?,'a','t',?,'[]','[]','discovered')", (MAILBOX,CUTOFF))
        db.execute("INSERT INTO attachments VALUES(?,'a','0',NULL,'inline','image/png',0)", (MAILBOX,))
        db.execute("INSERT INTO work(scan_key,message_id,status,attempts) VALUES('old','a','failed',2)")
        before = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in ("scans", "messages", "attachments", "work")}
    assert statistics(path, MAILBOX)["archive"]["incomplete"] == 1
    state = SQLiteState(path)
    try:
        assert state.db.execute("PRAGMA user_version").fetchone()[0] == 3
        for table, rows in before.items():
            assert [tuple(r) for r in state.db.execute(f"SELECT * FROM {table}")] == rows
        assert state.db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert state.pending("old") == ["a"]
    finally:
        state.close()


def test_failed_migration_rolls_back_without_advancing_version(tmp_path):
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        # A conflicting preexisting table must not leave a half-applied migration.
        db.execute("CREATE TABLE archive_jobs(synthetic TEXT)")
    with pytest.raises(sqlite3.Error):
        SQLiteState(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='archive_files'").fetchone() is None


def test_cli_archive_limit_and_safe_error(store, state, tmp_path, monkeypatch, capsys):
    cfg = Config(tmp_path / "credentials", tmp_path / "state.sqlite3", mailbox=MAILBOX)
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    monkeypatch.setattr(cli, "local_app_data", lambda: tmp_path)
    monkeypatch.setattr(cli, "authenticate", lambda _: nullcontext(Mock()))
    source = reader()
    monkeypatch.setattr(cli, "GmailReader", lambda _: source)
    assert cli.main(["archive", "--limit", "1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["state"]["archive"]["incomplete"] == 0
    assert "synthetic attachment" not in json.dumps(output)
    store.path(source_reference(MAILBOX,"a")).write_bytes(b"private synthetic text")
    assert cli.main(["archive", "--limit", "1"]) == 1
    assert capsys.readouterr().err == "Archive stopped: archive_integrity_mismatch\n"


def test_cli_missing_inventory_does_not_authenticate(tmp_path, monkeypatch, capsys):
    cfg = Config(tmp_path / "credentials", tmp_path / "missing.sqlite3")
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    auth = Mock()
    monkeypatch.setattr(cli, "authenticate", auth)
    assert cli.main(["archive"]) == 1
    assert "archive_inventory_missing" in capsys.readouterr().err
    auth.assert_not_called()
    assert not cfg.state_path.exists()


def test_cli_wrong_mailbox_does_not_migrate_or_create_archive(store, tmp_path, monkeypatch, capsys):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
    before = path.read_bytes()
    cfg = Config(tmp_path / "credentials", path)
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    monkeypatch.setattr(cli, "local_app_data", lambda: tmp_path)
    monkeypatch.setattr(cli, "authenticate", lambda _: nullcontext(Mock()))
    source = reader()
    monkeypatch.setattr(cli, "GmailReader", lambda _: source)
    assert cli.main(["archive"]) == 1
    assert "mailbox_identity_mismatch" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert not store.root.exists()
