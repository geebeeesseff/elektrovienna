"""Read-only inputs and immutable pilot artifacts; no provider or state writers."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .archive_store import SourceArchive, source_reference
from .models import ProviderError, historical_cutoff_ms


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PilotStore(SourceArchive):
    """Reuse Phase 2's guarded atomic no-replace publication for a narrow namespace."""

    def path(self, reference: str) -> Path:
        if not re.fullmatch(
            r"(?:00_raw/airtable/[0-9a-f]{64}\.json|"
            r"20_extractions/historical_pilot/[0-9a-f]{64}\.json|"
            r"90_manual_review/historical_pilot/[0-9a-f]{64}\.json|"
            r"30_cases/historical_pilot/[0-9a-f]{64}/(?:cases\.json|review\.md))", reference
        ):
            raise ProviderError("pilot_path_unsafe")
        target = self.root.joinpath(*reference.split("/"))
        self._guard(target)
        return target


def load_snapshot(path: Path) -> tuple[dict, bytes]:
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ProviderError("pilot_snapshot_too_large")
    raw = path.read_bytes()
    try:
        snapshot = json.loads(raw)
        if snapshot["format_version"] != 1:
            raise ValueError
        if not re.fullmatch(r"app[A-Za-z0-9]{14}", snapshot["base_id"]):
            raise ValueError
        if not snapshot["captured_at"] or not snapshot["capture_method"]:
            raise ValueError
        tables = snapshot["schema"]["tables"]
        if len({t["id"] for t in tables}) != len(tables):
            raise ValueError
        for key in ("tickets", "customers", "craftsmen"):
            records = snapshot[key]
            if len({r["id"] for r in records}) != len(records):
                raise ValueError
            for record in records:
                if not re.fullmatch(r"rec[A-Za-z0-9]{14}", record["id"]):
                    raise ValueError
                if not isinstance(record["cellValuesByFieldId"], dict):
                    raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ProviderError("pilot_snapshot_invalid") from None
    return snapshot, raw


class AirtableSnapshot:
    """Explicit captured-source boundary. This adapter does not claim live access."""

    def __init__(self, snapshot: dict, sha256: str):
        self.data, self.sha256 = snapshot, sha256
        self.tables = {}
        for kind, name in (("tickets", "Tickets (main)"), ("customers", "Customers"),
                           ("craftsmen", "Craftsmen")):
            matches = [t for t in snapshot["schema"]["tables"] if t["name"] == name]
            if len(matches) != 1:
                raise ProviderError("pilot_airtable_schema_mismatch")
            table = matches[0]
            if len({f["name"] for f in table["fields"]}) != len(table["fields"]):
                raise ProviderError("pilot_airtable_schema_mismatch")
            self.tables[kind] = table
        required = {"tickets": {"Name", "Customer", "Craftsman", "Service type", "Gewerk",
                                "Status", "Customer request", "Notes", "Customer Request Date", "Ticket volume (net)", "Closed Date"},
                    "customers": {"Name", "Email", "Adress"}, "craftsmen": {"Name", "Email"}}
        for kind, names in required.items():
            if not names.issubset({f["name"] for f in self.tables[kind]["fields"]}):
                raise ProviderError("pilot_airtable_schema_mismatch")

    def field_id(self, kind: str, name: str) -> str:
        for field in self.tables[kind]["fields"]:
            if field["name"] == name:
                return field["id"]
        raise ProviderError("pilot_airtable_schema_mismatch")

    def value(self, kind: str, record: dict, name: str):
        return record["cellValuesByFieldId"].get(self.field_id(kind, name))

    def ref(self, kind: str, record: dict, name: str | None = None) -> dict:
        index = next(i for i, r in enumerate(self.data[kind]) if r["id"] == record["id"])
        pointer = f"/{kind}/{index}"
        locator = f"airtable://{self.data['base_id']}/{self.tables[kind]['id']}/{record['id']}"
        if name:
            field_id = self.field_id(kind, name)
            pointer += f"/cellValuesByFieldId/{field_id}"
            locator += f"/fields/{field_id}"
        return {"source_id": self.sha256, "locator": locator, "json_pointer": pointer}


def display(value):
    return value.get("name") if isinstance(value, dict) and "name" in value else value


def select_tickets(source: AirtableSnapshot, limit: int) -> list[dict]:
    """Nonrandom learning sample: observed service/status coverage, then context richness."""
    if not 1 <= limit <= 30:
        raise ProviderError("pilot_limit_must_be_1_to_30")
    pool = [r for r in source.data["tickets"]
            if display(source.value("tickets", r, "Gewerk")) == "Elektrotechnik"
            and (source.value("tickets", r, "Customer Request Date") or r["createdTime"])[:10] >= "2026-01-01"]
    selected, services, statuses, pairs = [], set(), set(), set()

    def categories(record):
        return tuple(display(source.value("tickets", record, f)) or "unknown"
                     for f in ("Service type", "Status"))

    def rank(record):
        service, status = categories(record)
        novelty = 4 * (service not in services) + 3 * (status not in statuses) + 2 * ((service, status) not in pairs)
        quality = 1000 * bool(source.value("tickets", record, "Customer"))
        quality += 200 * bool(source.value("tickets", record, "Craftsman"))
        quality += min(800, sum(len(source.value("tickets", record, f) or "") for f in ("Customer request", "Notes")))
        return -novelty, -quality, record["id"]

    while pool and len(selected) < limit:
        record = min(pool, key=rank)
        pool.remove(record)
        selected.append(record)
        service, status = categories(record)
        services.add(service)
        statuses.add(status)
        pairs.add((service, status))
    if not selected:
        raise ProviderError("pilot_no_eligible_tickets")
    return selected


def archived_inputs(path: Path, mailbox: str) -> tuple[list[dict], list[dict]]:
    """One read transaction, mode=ro; never instantiate SQLiteState or migrate."""
    if not path.is_file():
        raise ProviderError("pilot_archive_inventory_missing")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] not in (2, 3):
            raise ProviderError("pilot_archive_schema_unsupported")
        messages = []
        for row in db.execute("SELECT * FROM messages WHERE mailbox=? AND status='discovered' AND internal_ms>=? ORDER BY internal_ms,message_id",
                              (mailbox, historical_cutoff_ms())):
            if not {"SPAM", "TRASH", "DRAFT"}.intersection(json.loads(row["labels"])):
                messages.append(dict(row))
        ids = {m["message_id"] for m in messages}
        files = [dict(row) for row in db.execute("""SELECT f.*, a.filename, a.mime_type, a.size AS provider_size,
                    a.attachment_id FROM archive_files f LEFT JOIN attachments a
                    ON f.mailbox=a.mailbox AND f.message_id=a.message_id AND f.part_id=a.part_id AND f.kind='attachment'
                    WHERE f.mailbox=? ORDER BY f.message_id,f.kind,f.part_id""", (mailbox,))
                 if row["message_id"] in ids]
        archived_parts = {(row["message_id"], row["part_id"]) for row in files if row["kind"] == "attachment"}
        missing_parts = {}
        for row in db.execute("SELECT message_id,part_id FROM attachments WHERE mailbox=?", (mailbox,)):
            if row["message_id"] in ids and (row["message_id"], row["part_id"]) not in archived_parts:
                missing_parts.setdefault(row["message_id"], []).append(row["part_id"])
        for message in messages:
            message["missing_attachment_parts"] = sorted(missing_parts.get(message["message_id"], []))
    for row in files:
        expected = source_reference(mailbox, row["message_id"], None if row["kind"] == "message" else row["part_id"])
        if row["reference"] != expected:
            raise ProviderError("archive_reference_mismatch")
    return messages, files


def read_verified(store: SourceArchive, row: dict) -> bytes:
    """Hash the same bytes handed to parsers (no verification/read race)."""
    raw = store.path(row["reference"]).read_bytes()
    if len(raw) != row["byte_length"] or digest(raw) != row["sha256"]:
        raise ProviderError("archive_integrity_mismatch")
    return raw
