"""Synthetic pilot sources only; no live customer data, credentials, or provider calls."""

import io
import json
import socket
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from elektro_vienna import archive_store, cli, pilot
from elektro_vienna.archive_store import SourceArchive, source_reference
from elektro_vienna.config import Config
from elektro_vienna.models import Attachment, Message, Page, ProviderError, historical_cutoff_ms
from elektro_vienna.pilot_parse import extract_mentions, parse_document, parse_email
from elektro_vienna.pilot_sources import (AirtableSnapshot, PilotStore, canonical, digest, load_snapshot,
                                        read_verified, select_tickets)
from elektro_vienna.state import SQLiteState

MAILBOX = "office@example.invalid"
BASE = "app" + "a" * 14
TICKET = "rec" + "a" * 14
CUSTOMER = "rec" + "c" * 14


def snapshot_data():
    names = {"Tickets (main)": ["Name", "Customer", "Craftsman", "Service type", "Gewerk", "Status",
             "Customer request", "Notes", "Customer Request Date", "Ticket volume (net)", "Closed Date"],
             "Customers": ["Name", "Email", "Adress"], "Craftsmen": ["Name", "Email"]}
    tables = [{"id": "tbl" + str(i) * 14, "name": name,
               "fields": [{"id": f"fld{i}{j:013}", "name": field, "type": "singleLineText"}
                          for j, field in enumerate(fields)]} for i, (name, fields) in enumerate(names.items())]
    def record(table, identity, values):
        return {"id": identity, "createdTime": "2026-01-02T12:00:00Z", "cellValuesByFieldId": {
            next(f["id"] for f in tables[table]["fields"] if f["name"] == name): value for name, value in values.items()}}
    return {"format_version": 1, "base_id": BASE, "captured_at": "2026-02-01T10:00:00+00:00",
        "capture_method": "synthetic fixture", "schema": {"tables": tables},
        "tickets": [record(0, TICKET, {"Name": "Synthetic wallbox", "Customer": [{"id": CUSTOMER, "name": "Alex Example"}],
            "Service type": {"name": "Wallbox"}, "Gewerk": {"name": "Elektrotechnik"}, "Status": {"name": "Abgeschlossen"},
            "Customer request": "Wallbox 11 kW, Leitung 12 m. Fotos fehlen.", "Notes": "150 EUR netto, Anfahrt offen.",
            "Customer Request Date": "2026-01-02", "Ticket volume (net)": 150})],
        "customers": [record(1, CUSTOMER, {"Name": "Alex Example", "Email": "alex@example.invalid", "Adress": "Testgasse 12"})],
        "craftsmen": []}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    monkeypatch.setattr(archive_store, "AUTHORIZED_ROOT", root)
    monkeypatch.setattr(archive_store, "local_app_data", lambda: tmp_path / "local")
    # No accidental provider/network side effects during any pilot test.
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("network forbidden")))
    monkeypatch.setattr(cli, "authenticate", Mock(side_effect=AssertionError("Gmail auth forbidden")))
    path = tmp_path / "snapshot.json"
    path.write_bytes(canonical(snapshot_data()))
    state = SQLiteState(tmp_path / "pipeline.sqlite3")
    state.begin("test", MAILBOX, historical_cutoff_ms())
    store = SourceArchive()
    for mid, to, subject in (("a", "alex@example.invalid", "Angebot"), ("b", "other@example.invalid", "Unrelated")):
        raw = (f"From: office@example.invalid\r\nTo: {to}\r\nSubject: {subject}\r\n"
               f"Content-Type: text/plain; charset=utf-8\r\n\r\n11 kW, 400 V\nPreis 150 EUR netto\n").encode()
        data = b"Material 50 EUR brutto\nArbeitszeit 2 Stunden\n"
        att = Attachment("1", "provider-id", "offer.txt", "text/plain", len(data))
        message = Message(mid, "thread-" + mid, historical_cutoff_ms() + 2 * 86400000,
                          (("To", to),), (), (att,))
        state.queue_page("test", Page((mid,), None))
        state.record("test", MAILBOX, message, True)
        for kind, part_id, value in (("message", "", raw), ("attachment", "1", data)):
            ref = source_reference(MAILBOX, mid, None if kind == "message" else part_id)
            record = store.publish(ref, value)
            state.record_archive(MAILBOX, mid, kind, part_id, record, "gmail://synthetic/" + mid)
        state.archive_result(MAILBOX, mid)
    state.close()
    return path, tmp_path / "pipeline.sqlite3", root


def test_end_to_end_provenance_read_only_and_idempotency(setup):
    path, state, root = setup
    before = state.read_bytes()
    originals = {p: p.read_bytes() for p in (root / "00_raw").rglob("*") if p.is_file()}
    first = pilot.reconstruct(path, state, MAILBOX)
    result = json.loads(Path(first["cases"]).read_bytes())
    case = result["cases"][0]
    assert first["counts"]["candidate_messages"] == 1
    assert first["counts"]["confirmed_matches"] == 0
    assert case["outcome"]["value"] == "completed"
    assert case["outcome"]["certainty"] == "derived_inferred"
    assert case["outcome_reason"]["certainty"] == "missing_information"
    assert case["knowledge_items"] == []
    assert case["matches"][0]["message_id"] == "a"
    assert {e["event_type"] for e in case["timeline"]} == {"inquiry_recorded", "email", "status_snapshot"}
    assert case["offer_candidates"]
    assert all(i["source_refs"] and i["processing_run"] == first["processing_run"] for i in case["technical_candidates"] + case["offer_candidates"] + case["evidence"])
    extraction = json.loads(Path(first["extraction"]).read_bytes())
    sources = {s["id"] for s in extraction["sources"]} | {extraction["snapshot_sha256"]}
    for item in case["technical_candidates"] + case["offer_candidates"]:
        for ref in item["source_refs"]:
            assert ref["source_id"] in sources
            assert ref["quote"] and ref["end"] > ref["start"]
    files = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    second = pilot.reconstruct(path, state, MAILBOX)
    assert first == second
    assert files == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == state.read_bytes()
    assert originals == {p: p.read_bytes() for p in originals}
    assert not (root / "40_knowledge").exists()


def test_dry_run_and_cli_never_authenticate_or_write(setup, monkeypatch, capsys):
    path, state, root = setup
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(Config, "from_environment", lambda: Config(path.parent / "creds", state, MAILBOX))
    monkeypatch.setattr(cli, "local_app_data", lambda: path.parent)
    assert cli.main(["pilot", "--snapshot", str(path), "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"]
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    cli.authenticate.assert_not_called()


def test_ambiguous_reused_customer_remains_candidate(setup):
    path, state, root = setup
    data = json.loads(path.read_bytes())
    duplicate = json.loads(json.dumps(data["tickets"][0]))
    duplicate["id"] = "rec" + "b" * 14
    data["tickets"].append(duplicate)
    path.write_bytes(canonical(data))
    result = pilot.reconstruct(path, state, MAILBOX)
    cases = json.loads(Path(result["cases"]).read_bytes())["cases"]
    assert all(c["matches"][0]["confidence"] == "ambiguous" for c in cases)
    assert all(c["matches"][0]["status"] == "candidate" for c in cases)
    assert result["counts"]["confirmed_matches"] == 0


@pytest.mark.parametrize("decision,expected", [("accept", "confirmed"), ("reject", "rejected")])
def test_review_preserves_prior_outputs_and_reject_removes_timeline(setup, decision, expected):
    path, state, root = setup
    first = pilot.reconstruct(path, state, MAILBOX)
    original = Path(first["cases"]).read_bytes()
    review = path.with_name("review.json")
    review.write_bytes(canonical({"base_run_id": first["processing_run"], "reviewer": "Synthetic Reviewer",
        "timestamp": "2026-02-01T12:00:00+00:00", "decisions": [{"case_id": TICKET, "message_id": "a",
        "decision": decision, "rationale": "Checked original source and ticket."}]}))
    second = pilot.reconstruct(path, state, MAILBOX, review_path=review)
    assert first["processing_run"] != second["processing_run"]
    assert original == Path(first["cases"]).read_bytes()
    case = json.loads(Path(second["cases"]).read_bytes())["cases"][0]
    assert case["matches"][0]["status"] == expected
    assert any(e["event_type"] == "email" for e in case["timeline"]) == (decision == "accept")
    assert case["knowledge_items"] == []
    assert all(i["review_status"] == "pending" for i in case["offer_candidates"])
    review.write_bytes(canonical({"base_run_id": "stale", "reviewer": "Synthetic Reviewer"}))
    with pytest.raises(ProviderError, match="review_invalid_or_stale"):
        pilot.reconstruct(path, state, MAILBOX, review_path=review)


def test_source_corruption_fails_without_state_or_output_mutation(setup):
    path, state, root = setup
    before = state.read_bytes()
    source = SourceArchive().path(source_reference(MAILBOX, "a"))
    source.write_bytes(b"corrupt")
    with pytest.raises(ProviderError, match="archive_integrity_mismatch"):
        pilot.reconstruct(path, state, MAILBOX)
    assert state.read_bytes() == before
    assert not (root / "30_cases").exists()


def test_all_references_resolve_and_incorrect_spans_are_rejected(setup):
    path, state, root = setup
    run = pilot.reconstruct(path, state, MAILBOX)
    result = json.loads(Path(run["cases"]).read_bytes())
    extraction = json.loads(Path(run["extraction"]).read_bytes())
    snapshot = json.loads(path.read_bytes())
    pilot.validate_provenance(result, extraction, snapshot)
    ref = result["cases"][0]["technical_candidates"][0]["source_refs"][0]
    ref["quote"] = "an unsupported value"
    with pytest.raises(ProviderError, match="provenance_invalid"):
        pilot.validate_provenance(result, extraction, snapshot)


def test_no_matching_identity_does_not_force_a_link(setup):
    path, state, root = setup
    data = json.loads(path.read_bytes())
    data["customers"][0]["cellValuesByFieldId"] = {}
    path.write_bytes(canonical(data))
    run = pilot.reconstruct(path, state, MAILBOX)
    assert run["counts"]["candidate_messages"] == 0
    case = json.loads(Path(run["cases"]).read_bytes())["cases"][0]
    assert not any(e["event_type"] == "email" for e in case["timeline"])
    assert any("No archived email candidate" in s for s in case["missing_information"])


def test_missing_attachment_archive_record_is_an_explicit_gap(setup):
    path, state, root = setup
    with sqlite3.connect(state) as db:
        db.execute("DELETE FROM archive_files WHERE message_id='a' AND kind='attachment'")
    before = state.read_bytes()
    run = pilot.reconstruct(path, state, MAILBOX)
    case = json.loads(Path(run["cases"]).read_bytes())["cases"][0]
    assert "attachment_not_archived:a:1" in case["issues"]
    assert run["counts"]["unarchived_candidate_attachments"] == 1
    assert state.read_bytes() == before


def test_resume_after_partial_publication(setup, monkeypatch):
    path, state, root = setup
    publish = PilotStore.publish
    def crash(self, reference, data):
        if reference.endswith("cases.json"):
            raise OSError("synthetic interruption")
        return publish(self, reference, data)
    monkeypatch.setattr(PilotStore, "publish", crash)
    with pytest.raises(OSError):
        pilot.reconstruct(path, state, MAILBOX)
    old = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(PilotStore, "publish", publish)
    result = pilot.reconstruct(path, state, MAILBOX)
    assert Path(result["cases"]).is_file()
    assert all(p.read_bytes() == data for p, data in old.items())


def test_versioned_reprocessing_preserves_previous_interpretation(setup, monkeypatch):
    path, state, root = setup
    first = pilot.reconstruct(path, state, MAILBOX)
    monkeypatch.setattr(pilot, "PROCESSOR_VERSION", "synthetic-v2")
    second = pilot.reconstruct(path, state, MAILBOX)
    assert first["processing_run"] != second["processing_run"]
    assert Path(first["cases"]).is_file() and Path(second["cases"]).is_file()


@pytest.mark.parametrize("ref", ["../bad", "00_raw/gmail/aa/" + "a" * 64 + ".eml",
    "30_cases/historical_pilot/../cases.json", "40_knowledge/test.json", "00_raw/airtable/C:bad.json"])
def test_pilot_store_cannot_overwrite_sources_or_write_elsewhere(setup, ref):
    with pytest.raises(ProviderError, match="pilot_path_unsafe"):
        PilotStore().publish(ref, b"synthetic")


def test_snapshot_schema_validation_and_selection(setup):
    path, state, root = setup
    data, raw = load_snapshot(path)
    source = AirtableSnapshot(data, digest(raw))
    assert select_tickets(source, 25) == data["tickets"]
    with pytest.raises(ProviderError, match="limit"):
        select_tickets(source, 31)
    data["tickets"].append(data["tickets"][0])
    path.write_bytes(canonical(data))
    with pytest.raises(ProviderError, match="snapshot_invalid"):
        load_snapshot(path)


def test_mime_alternative_unicode_and_quoted_history():
    raw = ("From: a@example.invalid\r\nSubject: =?utf-8?q?Pr=C3=BCfung?=\r\nMIME-Version: 1.0\r\n"
        "Content-Type: multipart/alternative; boundary=part\r\n\r\n--part\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "Aktuell 400 V\n> Vorher 230 V\r\n--part\r\nContent-Type: text/html\r\n\r\n<p>duplicate</p>\r\n--part--\r\n").encode()
    parsed = parse_email(raw)
    assert parsed["headers"][1][1] == "Prüfung"
    text = "\n".join(p["text"] for p in parsed["sections"])
    assert "> Vorher 230 V" in text and "duplicate" not in text
    tech, _ = extract_mentions(parsed["sections"], "synthetic")
    assert len([t for t in tech if t["field"] == "electrical_capacity"]) == 2
    assert all(t["certainty"] == "uncertain_candidate" for t in tech)


def test_documents_unsupported_scans_html_docx_and_failure():
    assert parse_document(b"photo", "image/jpeg", "test.jpg")["issues"] == ["image_requires_visual_review"]
    assert "document_parse_failed" in parse_document(b"bad", "application/pdf", "x.pdf")["issues"]
    from pypdf import PdfWriter
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    assert "page:1:needs_visual_review_or_ocr" in parse_document(buffer.getvalue(), "application/pdf", "x.pdf")["issues"]
    result = parse_document(b'<script>fetch("https://invalid")</script><p>150 EUR</p>', "text/html", "x.html")
    assert "fetch" not in result["sections"][0]["text"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>11 kW</w:t></w:r></w:p></w:body></w:document>')
    result = parse_document(buffer.getvalue(), "application/octet-stream", "x.docx")
    assert result["sections"][0]["text"] == "11 kW"


def test_amounts_stay_contextual_not_global_pricing():
    text = "Kunde nennt Budget 1.500 EUR brutto\nArbeitszeit 2 Stunden\n"
    _, offers = extract_mentions([{"text": text, "locator": "page:1:text"}], "synthetic")
    assert len(offers) == 2
    assert offers[0]["amount_mentions"] == ["1.500 EUR"]
    assert offers[0]["net_gross"] == "unresolved"
    assert offers[0]["net_gross_mentions"] == ["brutto"]
    assert offers[0]["amount_role"] == "unresolved"
    assert offers[0]["issuer"] is None
    for item in offers:
        ref = item["source_refs"][0]
        assert text[ref["start"]:ref["end"]] == ref["quote"]
