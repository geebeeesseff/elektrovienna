"""SQLite technical state. A page is durable before any of its messages are read."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Protocol

from .models import ArchivedFile, Attachment, Message, Page, historical_cutoff_ms


class InventoryState(Protocol):
    def scan(self, key: str): ...
    def begin(self, key: str, mailbox: str, cutoff: int): ...
    def queue_page(self, key: str, page: Page): ...
    def pending(self, key: str) -> list[str]: ...
    def record(self, key: str, mailbox: str, message: Message, included: bool): ...
    def fail(self, key: str, message_id: str | None, code: str): ...
    def advance(self, key: str): ...


SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  scan_key TEXT PRIMARY KEY, mailbox TEXT NOT NULL, cutoff_ms INTEGER NOT NULL,
  token TEXT, next_token TEXT, page_loaded INTEGER NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0, seen_tokens TEXT NOT NULL DEFAULT '[]',
  failures INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS work (
  scan_key TEXT NOT NULL REFERENCES scans(scan_key), message_id TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  PRIMARY KEY (scan_key, message_id)
);
CREATE TABLE IF NOT EXISTS messages (
  mailbox TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
  internal_ms INTEGER NOT NULL, headers TEXT NOT NULL, labels TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('discovered','excluded')),
  archived INTEGER NOT NULL DEFAULT 0 CHECK(archived = 0),
  first_discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (mailbox, message_id)
);
CREATE TABLE IF NOT EXISTS attachments (
  mailbox TEXT NOT NULL, message_id TEXT NOT NULL, part_id TEXT NOT NULL,
  attachment_id TEXT, filename TEXT NOT NULL, mime_type TEXT NOT NULL, size INTEGER NOT NULL,
  PRIMARY KEY (mailbox, message_id, part_id),
  FOREIGN KEY (mailbox, message_id) REFERENCES messages(mailbox, message_id)
);
PRAGMA user_version = 1;
"""


class SQLiteState:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            self.db.close()
            raise ValueError("Unsupported state schema; preserve the database and upgrade the application.")
        if version == 0:
            self.db.executescript(SCHEMA)
        if version < 2:
            # Additive, transactional migration; Phase 1 tables/checkpoints are untouched.
            try:
                self.db.executescript(ARCHIVE_SCHEMA)
            except BaseException:
                self.db.rollback()
                self.db.close()
                raise
        if version < 3:
            try:
                self.db.executescript("""BEGIN IMMEDIATE;
                    ALTER TABLE archive_files ADD COLUMN anomaly TEXT
                        CHECK(anomaly IS NULL OR anomaly='provider_size_mismatch');
                    PRAGMA user_version = 3;
                    COMMIT;""")
            except BaseException:
                self.db.rollback()
                self.db.close()
                raise

    def close(self):
        self.db.close()

    def scan(self, key):
        row = self.db.execute("SELECT * FROM scans WHERE scan_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def begin(self, key, mailbox, cutoff):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO scans(scan_key,mailbox,cutoff_ms) VALUES(?,?,?)", (key, mailbox, cutoff))
            self.db.execute("""UPDATE scans SET token=NULL,next_token=NULL,page_loaded=0,
                completed=0,seen_tokens='[]',last_error=NULL WHERE scan_key=? AND completed=1""", (key,))

    def queue_page(self, key, page):
        with self.db:
            self.db.executemany("""INSERT INTO work(scan_key,message_id,status) VALUES(?,?,'pending')
                ON CONFLICT(scan_key,message_id) DO UPDATE SET status='pending',last_error=NULL""",
                                [(key, identity) for identity in page.message_ids])
            self.db.execute("UPDATE scans SET page_loaded=1,next_token=?,last_error=NULL WHERE scan_key=?", (page.next_token, key))

    def pending(self, key):
        return [row[0] for row in self.db.execute(
            "SELECT message_id FROM work WHERE scan_key=? AND status IN ('pending','failed') ORDER BY rowid", (key,))]

    def record(self, key, mailbox, message, included):
        status = "discovered" if included else "excluded"
        with self.db:
            # Excluded candidates retain only identity/timestamp/labels, not customer headers.
            self.db.execute("""INSERT INTO messages(mailbox,message_id,thread_id,internal_ms,headers,labels,status)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(mailbox,message_id) DO UPDATE SET
                labels=excluded.labels,status=excluded.status,last_seen_at=CURRENT_TIMESTAMP""",
                            (mailbox, message.message_id, message.thread_id, message.internal_ms,
                             json.dumps(message.headers if included else ()), json.dumps(message.labels), status))
            if included:
                # Fill metadata if an excluded candidate later becomes eligible. Original identity stays stable.
                self.db.execute("UPDATE messages SET headers=? WHERE mailbox=? AND message_id=?",
                                (json.dumps(message.headers), mailbox, message.message_id))
                for attachment in message.attachments:
                    self.db.execute("""INSERT OR IGNORE INTO attachments VALUES(?,?,?,?,?,?,?)""",
                                    (mailbox, message.message_id, attachment.part_id, attachment.attachment_id,
                                     attachment.filename, attachment.mime_type, attachment.size))
            self.db.execute("UPDATE work SET status=?,attempts=attempts+1,last_error=NULL WHERE scan_key=? AND message_id=?",
                            (status, key, message.message_id))
            self.db.execute("UPDATE scans SET last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE scan_key=?", (key,))

    def fail(self, key, message_id, code):
        with self.db:
            self.db.execute("UPDATE scans SET failures=failures+1,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE scan_key=?", (code, key))
            if message_id is not None:
                self.db.execute("UPDATE work SET status='failed',attempts=attempts+1,last_error=? WHERE scan_key=? AND message_id=?",
                                (code, key, message_id))

    def advance(self, key):
        with self.db:
            if self.pending(key):
                raise ValueError("Cannot advance past incomplete messages.")
            scan = self.scan(key)
            seen = json.loads(scan["seen_tokens"])
            if scan["next_token"] is not None:
                seen.append(scan["next_token"])
            self.db.execute("""UPDATE scans SET token=next_token,next_token=NULL,page_loaded=0,
                completed=?,seen_tokens=?,last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE scan_key=?""",
                            (int(scan["next_token"] is None), json.dumps(seen), key))

    def restart_checkpoint(self, key):
        """Explicit recovery for expired page tokens, preserving all inventory and failed work."""
        with self.db:
            if self.pending(key):
                raise ValueError("Finish or retry the queued page before restarting pagination.")
            self.db.execute("""UPDATE scans SET token=NULL,next_token=NULL,page_loaded=0,
                completed=0,seen_tokens='[]',last_error=NULL WHERE scan_key=?""", (key,))

    def archive_candidates(self, mailbox, cutoff):
        messages = []
        for row in self.db.execute("""SELECT * FROM messages WHERE mailbox=? AND
                status='discovered' AND internal_ms>=? ORDER BY internal_ms,message_id""", (mailbox, cutoff)):
            attachments = tuple(Attachment(a["part_id"], a["attachment_id"], a["filename"], a["mime_type"], a["size"])
                                for a in self.db.execute("SELECT * FROM attachments WHERE mailbox=? AND message_id=? ORDER BY part_id",
                                                         (mailbox, row["message_id"])))
            message = Message(row["message_id"], row["thread_id"], row["internal_ms"],
                              (), tuple(json.loads(row["labels"])), attachments)
            if message.qualifies(cutoff):
                messages.append(message)
        return messages

    def archived_file(self, mailbox, message_id, kind, part_id=""):
        row = self.db.execute("""SELECT reference,sha256,byte_length,archived_at FROM archive_files
            WHERE mailbox=? AND message_id=? AND kind=? AND part_id=?""",
                              (mailbox, message_id, kind, part_id)).fetchone()
        return ArchivedFile(*row) if row else None

    def archive_complete(self, mailbox, message):
        row = self.db.execute("SELECT last_error FROM archive_jobs WHERE mailbox=? AND message_id=?",
                              (mailbox, message.message_id)).fetchone()
        count = self.db.execute("""SELECT count(*) FROM archive_files WHERE mailbox=? AND message_id=?
            AND verified=1""", (mailbox, message.message_id)).fetchone()[0]
        return row is not None and row[0] is None and count == 1 + len(message.attachments)

    def record_archive(self, mailbox, message_id, kind, part_id, record, locator, anomaly=None):
        with self.db:
            self.db.execute("""INSERT INTO archive_files
                (mailbox,message_id,kind,part_id,reference,sha256,byte_length,archived_at,source_locator,anomaly)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (mailbox, message_id, kind, part_id,
                record.reference, record.sha256, record.byte_length, record.archived_at, locator, anomaly))

    def archive_verified(self, mailbox, message_id, kind, part_id, verified):
        with self.db:
            self.db.execute("""UPDATE archive_files SET verified=? WHERE mailbox=? AND message_id=?
                AND kind=? AND part_id=?""", (int(verified), mailbox, message_id, kind, part_id))

    def archive_result(self, mailbox, message_id, code=None):
        with self.db:
            self.db.execute("""INSERT INTO archive_jobs(mailbox,message_id,attempts,failures,last_error)
                VALUES(?,?,1,?,?) ON CONFLICT(mailbox,message_id) DO UPDATE SET
                attempts=attempts+1,failures=failures+excluded.failures,last_error=excluded.last_error,
                updated_at=CURRENT_TIMESTAMP""", (mailbox, message_id, int(code is not None), code))


ARCHIVE_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE archive_files (
  mailbox TEXT NOT NULL, message_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('message','attachment')), part_id TEXT NOT NULL,
  reference TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, byte_length INTEGER NOT NULL CHECK(byte_length>=0),
  archived_at TEXT NOT NULL, source_locator TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 1 CHECK(verified IN (0,1)),
  PRIMARY KEY(mailbox,message_id,kind,part_id),
  FOREIGN KEY(mailbox,message_id) REFERENCES messages(mailbox,message_id),
  CHECK(kind='attachment' OR part_id='')
);
CREATE TABLE archive_jobs (
  mailbox TEXT NOT NULL, message_id TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(mailbox,message_id),
  FOREIGN KEY(mailbox,message_id) REFERENCES messages(mailbox,message_id)
);
PRAGMA user_version = 2;
COMMIT;
"""


def archive_statistics(db, mailbox):
    eligible = """SELECT message_id FROM messages WHERE mailbox=:mailbox AND status='discovered'
        AND internal_ms>=:cutoff AND NOT EXISTS
        (SELECT 1 FROM json_each(messages.labels) WHERE value IN ('SPAM','TRASH','DRAFT'))"""
    params = {"mailbox": mailbox, "cutoff": historical_cutoff_ms()}
    messages = db.execute(f"SELECT count(*) FROM ({eligible})", params).fetchone()[0]
    attachments = db.execute(f"""SELECT count(*) FROM attachments WHERE mailbox=:mailbox
        AND message_id IN ({eligible})""", params).fetchone()[0]
    result = dict(messages_eligible=messages, originals_archived=0, attachment_occurrences_eligible=attachments,
                  attachment_occurrences_archived=0, bytes_archived=0, failures=0, incomplete=messages,
                  last_error=None, anomalies={"provider_size_mismatch": 0})
    if db.execute("PRAGMA user_version").fetchone()[0] < 2:
        return result
    if db.execute("PRAGMA user_version").fetchone()[0] >= 3:
        result["anomalies"]["provider_size_mismatch"] = db.execute("""SELECT count(*) FROM archive_files
            WHERE mailbox=? AND anomaly='provider_size_mismatch'""", (mailbox,)).fetchone()[0]
    for row in db.execute(f"""SELECT kind,count(*),sum(byte_length) FROM archive_files WHERE mailbox=:mailbox
            AND message_id IN ({eligible}) AND verified=1 GROUP BY kind""", params):
        result["originals_archived" if row[0] == "message" else "attachment_occurrences_archived"] = row[1]
        result["bytes_archived"] += row[2]
    result["failures"] = db.execute("SELECT coalesce(sum(failures),0) FROM archive_jobs WHERE mailbox=?", (mailbox,)).fetchone()[0]
    result["incomplete"] = db.execute(f"""SELECT count(*) FROM messages m WHERE m.mailbox=:mailbox
        AND m.message_id IN ({eligible}) AND (
          NOT EXISTS(SELECT 1 FROM archive_jobs j WHERE j.mailbox=m.mailbox AND j.message_id=m.message_id AND j.last_error IS NULL)
          OR NOT EXISTS(SELECT 1 FROM archive_files f WHERE f.mailbox=m.mailbox AND f.message_id=m.message_id AND f.kind='message' AND f.verified=1)
          OR EXISTS(SELECT 1 FROM attachments a WHERE a.mailbox=m.mailbox AND a.message_id=m.message_id
            AND NOT EXISTS(SELECT 1 FROM archive_files f WHERE f.mailbox=a.mailbox AND f.message_id=a.message_id
              AND f.kind='attachment' AND f.part_id=a.part_id AND f.verified=1)))""", params).fetchone()[0]
    error = db.execute("""SELECT last_error FROM archive_jobs WHERE mailbox=? AND last_error IS NOT NULL
        ORDER BY updated_at DESC,message_id LIMIT 1""", (mailbox,)).fetchone()
    result["last_error"] = error[0] if error else None
    return result


def statistics(path: Path, mailbox: str) -> dict:
    if not path.exists():
        return {"discovered": 0, "excluded": 0, "attachment_occurrences": 0, "originals_archived": 0,
                "archive": {"messages_eligible": 0, "originals_archived": 0,
                            "attachment_occurrences_eligible": 0, "attachment_occurrences_archived": 0,
                            "bytes_archived": 0, "failures": 0, "incomplete": 0, "last_error": None,
                            "anomalies": {"provider_size_mismatch": 0}}, "scans": []}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        counts = dict(db.execute("SELECT status,count(*) FROM messages WHERE mailbox=? GROUP BY status", (mailbox,)))
        archive_counts = archive_statistics(db, mailbox)
        return {
            "discovered": counts.get("discovered", 0), "excluded": counts.get("excluded", 0),
            "attachment_occurrences": db.execute("SELECT count(*) FROM attachments WHERE mailbox=?", (mailbox,)).fetchone()[0],
            "originals_archived": archive_counts["originals_archived"],
            "archive": archive_counts,
            "scans": [dict(row) for row in db.execute("""SELECT scan_key,completed,page_loaded,failures,last_error,
                (SELECT count(*) FROM work WHERE work.scan_key=scans.scan_key AND status IN ('pending','failed')) AS incomplete
                FROM scans WHERE mailbox=?""", (mailbox,))],
        }


@contextmanager
def single_operator(path: Path):
    """OS-held lock: released automatically even when the process crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            import os
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another inventory process is using this state; wait for it to finish.") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
