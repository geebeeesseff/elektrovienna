"""Historical Case Reconstruction Pilot. Local, bounded, reviewable interpretations."""

import json
import re
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .archive_store import SourceArchive
from .models import ProviderError
from .pilot_matching import match_messages, source_identity
from .pilot_parse import MAX_SOURCE_BYTES, MAX_DOCUMENT_BYTES, PARSER_VERSION, extract_mentions, parse_document, parse_email
from .pilot_sources import (AirtableSnapshot, PilotStore, archived_inputs, canonical, digest,
                            display, load_snapshot, read_verified, select_tickets)

PROCESSOR_VERSION = "historical-case-v1"
MAX_CANDIDATE_MESSAGES = 300
OUTCOMES = {"Abgeschlossen": "completed", "Beauftragt": "commissioned", "Verloren": "lost",
            "Stand By": "stalled", "Anfrage": "unknown", "Angebot": "unknown", "Mahnung": "unknown"}


def fact(value, refs, certainty="explicit_source_fact"):
    return {"value": value, "certainty": certainty if value is not None else "missing_information",
            "source_refs": refs, "review_status": "pending"}


def dependency_versions():
    result = {}
    for name in ("pypdf",):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "unavailable"
    import platform
    result["python"] = platform.python_version()
    return result


def processor_fingerprint():
    # A code change always changes the interpretation identity, even before a version bump.
    names = ("pilot.py", "pilot_sources.py", "pilot_parse.py", "pilot_matching.py")
    return {name: digest(Path(__file__).with_name(name).read_bytes()) for name in names}


def apply_review(matches, raw: bytes | None, baseline_id: str) -> dict | None:
    if raw is None:
        return None
    try:
        review = json.loads(raw)
        if review["base_run_id"] != baseline_id or not review["reviewer"].strip():
            raise ValueError
        when = datetime.fromisoformat(review["timestamp"])
        if when.tzinfo is None:
            raise ValueError
        seen = set()
        for decision in review["decisions"]:
            key = (decision["case_id"], decision["message_id"])
            if key in seen or decision["decision"] not in ("accept", "reject") or not decision["rationale"].strip():
                raise ValueError
            seen.add(key)
            candidate = next(m for m in matches[key[0]] if m["message_id"] == key[1])
            candidate["status"] = "confirmed" if decision["decision"] == "accept" else "rejected"
            candidate["review"] = {"source_id": digest(raw), "reviewer": review["reviewer"],
                                   "timestamp": review["timestamp"], "rationale": decision["rationale"]}
    except (KeyError, ValueError, TypeError, AttributeError, StopIteration):
        raise ProviderError("pilot_review_invalid_or_stale") from None
    return review


def build_case(source, ticket, candidates, messages, parsed, file_rows, documents):
    case_id = ticket["id"]

    def field(name):
        value = display(source.value("tickets", ticket, name))
        return fact(value, [source.ref("tickets", ticket, name)])

    identity = {"airtable_ticket": fact(case_id, [source.ref("tickets", ticket)]),
                "name": field("Name"), "service_type": field("Service type"), "status": field("Status"),
                "customers": field("Customer"), "craftsmen": field("Craftsman")}
    identity["linked_records"] = []
    issues = []
    for kind, name in (("customers", "Customer"), ("craftsmen", "Craftsman")):
        records = {r["id"]: r for r in source.data[kind]}
        for link in source.value("tickets", ticket, name) or []:
            record = records.get(link["id"])
            if record is None:
                issues.append(f"linked_{kind}_record_missing:{link['id']}")
                continue
            identity["linked_records"].append({"kind": kind, "id": record["id"],
                "fields": {f["name"]: fact(display(record["cellValuesByFieldId"].get(f["id"])),
                                          [source.ref(kind, record, f["name"])])
                           for f in source.tables[kind]["fields"] if f["name"] != "Tickets (main)"}})
    status = identity["status"]["value"]
    outcome = fact(OUTCOMES.get(status, "unknown"), identity["status"]["source_refs"], "derived_inferred")
    outcome["derivation"] = f"Current Airtable status mapping: {status!r}; not independently verified execution."
    events = []
    request_date = source.value("tickets", ticket, "Customer Request Date")
    requested = field("Customer request")
    notes = field("Notes")
    if request_date:
        events.append({"id": f"{case_id}:request", "event_type": "inquiry_recorded", "channel": "airtable",
            "timestamp": fact(request_date, [source.ref("tickets", ticket, "Customer Request Date")]),
            "time_precision": "date", "summary": requested, "actor": None, "association": "anchor"})
    # A current CRM status is a snapshot, not a fabricated historical status transition.
    events.append({"id": f"{case_id}:snapshot", "event_type": "status_snapshot", "channel": "airtable",
        "timestamp": fact(source.data["captured_at"], [{"source_id": source.sha256, "json_pointer": "/captured_at"}]),
        "time_precision": "observation", "summary": identity["status"], "actor": None, "association": "anchor"})
    technical, commercial, evidence = [], [], []
    for name in ("Customer request", "Notes"):
        text = source.value("tickets", ticket, name) or ""
        ref = source.ref("tickets", ticket, name)
        parts = [{"locator": ref["json_pointer"], "text": text}]
        tech, offers = extract_mentions(parts, source.sha256)
        for item in tech + offers:
            item["association"] = "anchor"
            item["source_refs"][0]["json_pointer"] = ref["json_pointer"]
        technical.extend(tech)
        commercial.extend(offers)
    for name in ("Customer request", "Notes", "Ticket volume (net)", "Closed Date"):
        item = field(name)
        if item["value"] is not None:
            evidence.append({"field": name, **item, "association": "anchor"})
    for candidate in candidates:
        if candidate["status"] == "rejected":
            continue
        message = messages[candidate["message_id"]]
        issues.extend(f"attachment_not_archived:{message['message_id']}:{part}"
                      for part in message.get("missing_attachment_parts", []))
        content = parsed[candidate["message_id"]]
        source_id = message["source_id"]
        subject = next((s for s in content["sections"] if s["locator"].endswith(":subject")), None)
        actor = next((s for s in content["sections"] if s["locator"].endswith(":from")), None)
        timestamp = datetime.fromtimestamp(message["internal_ms"] / 1000, timezone.utc).isoformat()
        association = candidate["status"]
        events.append({"id": f"{case_id}:email:{message['message_id']}", "event_type": "email", "channel": "gmail_archive",
            "timestamp": fact(timestamp, [{"source_id": source_id, "locator": "inventory:internal_ms"}], "derived_inferred"),
            "time_precision": "provider_timestamp", "summary": fact(subject["text"] if subject else None,
                [{"source_id": source_id, "locator": subject["locator"]}] if subject else []),
            "actor": fact(actor["text"] if actor else None,
                [{"source_id": source_id, "locator": actor["locator"]}] if actor else []),
            "association": association, "message_id": message["message_id"]})
        sections_to_extract = [(source_id, content["sections"])]
        for row in file_rows:
            if row["kind"] == "attachment" and row["message_id"] == message["message_id"]:
                doc = documents[source_identity(row)]
                sections_to_extract.append((doc["source_id"], doc["sections"]))
        for item_source, sections in sections_to_extract:
            tech, offers = extract_mentions(sections, item_source)
            for item in tech + offers:
                item["association"] = association
                item["message_id"] = message["message_id"]
            technical.extend(tech)
            commercial.extend(offers)
    events.sort(key=lambda e: (e["timestamp"]["value"] or "9999", e["id"]))
    for index, item in enumerate(technical + commercial + evidence):
        item["id"] = f"{case_id}:evidence:{index}"
    missing = ["Historical outcome reason requires source review.",
               "Unobserved calls, visits, appointments and execution must not be invented.",
               "Validate commercial role, net/gross basis, scope, labor/material/travel and estimate issuer."]
    if not candidates:
        missing.append("No archived email candidate found; this is not proof that no communication occurred.")
    if not requested["value"]:
        missing.append("Requested work absent in ticket snapshot.")
    return {"id": case_id, "identity": identity, "requested_work": requested, "notes": notes,
            "outcome": outcome, "outcome_reason": fact(None, []), "timeline": events,
            "technical_candidates": technical, "offer_candidates": commercial, "evidence": evidence,
            "matches": candidates, "missing_information": missing, "issues": issues,
            "review_status": "pending", "knowledge_items": []}


def validate_provenance(result, extraction, snapshot):
    """Resolve every emitted fact/matching citation before publishing a completed run."""
    text = {(item["source_id"], part["locator"]): part["text"]
            for group in (extraction["messages"], extraction["documents"])
            for item in group.values() for part in item["sections"]}
    inventory = {m["source_id"]: m for m in extraction["inventory_scope"] if m.get("source_id")}

    def resolve(ref, missing=False):
        try:
            if ref["source_id"] == extraction["snapshot_sha256"]:
                value = snapshot
                for part in ref["json_pointer"].split("/")[1:]:
                    value = value[int(part)] if isinstance(value, list) else value[part]
            elif ref["locator"].startswith("inventory:"):
                value = inventory[ref["source_id"]][ref["locator"].split(":", 1)[1]]
            else:
                value = text[ref["source_id"], ref["locator"]]
            if "quote" in ref and (not isinstance(value, str) or ref["start"] < 0
                                   or ref["end"] > len(value) or ref["start"] >= ref["end"]
                                   or value[ref["start"]:ref["end"]] != ref["quote"]):
                raise ValueError
        except (KeyError, IndexError):
            if missing:
                return
            raise ProviderError("pilot_provenance_invalid") from None
        except (TypeError, ValueError):
            raise ProviderError("pilot_provenance_invalid") from None

    def walk(value):
        if isinstance(value, dict):
            if "certainty" in value and value["certainty"] != "missing_information" and not value.get("source_refs"):
                raise ProviderError("pilot_provenance_missing")
            for ref in value.get("source_refs", []):
                resolve(ref, value.get("certainty") == "missing_information")
            for name in ("anchor_ref", "message_ref"):
                if name in value:
                    resolve(value[name])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(result["cases"])


def reconstruct(snapshot_path: Path, state_path: Path, mailbox: str, *, limit=25,
                review_path: Path | None = None, dry_run=False) -> dict:
    snapshot, raw = load_snapshot(snapshot_path)
    code_fingerprint = processor_fingerprint()
    source = AirtableSnapshot(snapshot, digest(raw))
    selected = select_tickets(source, limit)
    archive, output = SourceArchive(), PilotStore()
    metadata, files = archived_inputs(state_path, mailbox)
    originals = {r["message_id"]: r for r in files if r["kind"] == "message"}
    parsed, skipped = {}, []
    for message in metadata:
        row = originals.get(message["message_id"])
        if row is None:
            skipped.append({"message_id": message["message_id"], "issue": "original_not_archived"})
            continue
        message["source_id"] = source_identity(row)
        if row["byte_length"] > MAX_SOURCE_BYTES:
            skipped.append({"message_id": message["message_id"], "issue": "source_size_limit"})
            continue
        parsed[message["message_id"]] = parse_email(read_verified(archive, row))
    matches = match_messages(source, selected, metadata, parsed, mailbox)
    candidate_ids = {c["message_id"] for values in matches.values() for c in values}
    if len(candidate_ids) > MAX_CANDIDATE_MESSAGES:
        raise ProviderError("pilot_too_many_candidates_reduce_ticket_limit")
    selected_files = [r for r in files if r["message_id"] in candidate_ids]
    documents = {}
    for row in selected_files:
        if row["kind"] != "attachment":
            continue
        # Text MIME occurrences without filenames duplicate the canonical RFC822 body.
        if not row["filename"] and (row["mime_type"].startswith("text/") or row["mime_type"].startswith("multipart/")):
            content = {"sections": [], "issues": ["body_occurrence_covered_by_original"]}
        elif row["byte_length"] > MAX_DOCUMENT_BYTES:
            content = {"sections": [], "issues": ["document_size_limit"]}
        else:
            content = parse_document(read_verified(archive, row), row["mime_type"] or "", row["filename"] or "")
        documents[source_identity(row)] = {"source_id": source_identity(row), **content}
    # Persist only candidate text; the lexical mailbox scan has no classification output.
    extraction = {"format_version": 1, "processor_version": PROCESSOR_VERSION, "parser_version": PARSER_VERSION,
        "processor_code": code_fingerprint, "dependencies": dependency_versions(),
        "snapshot_sha256": source.sha256, "selected_ticket_ids": [r["id"] for r in selected],
        "mailbox": mailbox, "selection_method": "coverage-richness-v1",
        "matching_config": {"window_days": [-21, 120], "automatic_acceptance": False,
                            "max_candidate_messages": MAX_CANDIDATE_MESSAGES},
        "inventory_scope": [{"message_id": m["message_id"], "thread_id": m["thread_id"],
                             "internal_ms": m["internal_ms"], "source_id": m.get("source_id"),
                             "missing_attachment_parts": m["missing_attachment_parts"]}
                            for m in metadata],
        "matching_scan_issues": skipped,
        "messages": {i: {"source_id": source_identity(originals[i]), **parsed[i]}
                     for i in sorted(candidate_ids)},
        "documents": documents,
        "sources": [{"id": source_identity(row), **row} for row in selected_files]}
    extraction_bytes = canonical(extraction)
    extraction_id = digest(extraction_bytes)
    extraction_ref = f"20_extractions/historical_pilot/{extraction_id}.json"
    baseline_id = digest(canonical({"extraction": extraction_id, "review": None}))
    review_raw = review_path.read_bytes() if review_path else None
    review = apply_review(matches, review_raw, baseline_id)
    run_id = digest(canonical({"extraction": extraction_id, "review": digest(review_raw) if review_raw else None}))
    cases = [build_case(source, ticket, matches[ticket["id"]], {m["message_id"]: m for m in metadata},
                        parsed, selected_files, documents) for ticket in selected]
    for case in cases:
        case["processing_run"] = run_id
        for event in case["timeline"]:
            event["case_id"] = case["id"]
            event["processing_run"] = run_id
        for item in case["technical_candidates"] + case["offer_candidates"] + case["evidence"]:
            item["processing_run"] = run_id
            item["extraction_reference"] = extraction_ref
    counts = {"cases": len(cases), "candidate_messages": len(candidate_ids),
        "cases_with_email_candidates": sum(bool(c["matches"]) for c in cases),
        "match_proposals": sum(len(c["matches"]) for c in cases),
        "ambiguous_proposals": sum(bool(m["competing_case_ids"]) for c in cases for m in c["matches"]),
        "confirmed_matches": sum(m["status"] == "confirmed" for c in cases for m in c["matches"]),
        "technical_candidates": sum(len(c["technical_candidates"]) for c in cases),
        "commercial_candidates": sum(len(c["offer_candidates"]) for c in cases),
        "documents": len(documents), "documents_with_text": sum(any(s["text"].strip() for s in d["sections"]) for d in documents.values()),
        "images_for_visual_review": sum(d["issues"] == ["image_requires_visual_review"] for d in documents.values()),
        "documents_requiring_attention": sum(bool(d["issues"]) and d["issues"] != ["body_occurrence_covered_by_original"] for d in documents.values()),
        "unarchived_candidate_attachments": sum(len(m["missing_attachment_parts"]) for m in metadata if m["message_id"] in candidate_ids),
        "skipped_originals": len(skipped), "knowledge_items": 0}
    result = {"format_version": 1, "processing_run": {"id": run_id, "operation": "historical_case_reconstruction",
        "processor_version": PROCESSOR_VERSION, "baseline_id": baseline_id, "supersedes": baseline_id if review else None,
        "snapshot_observed_at": snapshot["captured_at"], "status": "awaiting_human_review", "counts": counts,
        "selection": {"method": "coverage-richness-v1", "population": len(snapshot["tickets"]),
                      "services": dict(Counter(c["identity"]["service_type"]["value"] for c in cases)),
                      "statuses": dict(Counter(c["identity"]["status"]["value"] for c in cases))},
        "limitations": ["Current Airtable snapshots do not provide historical field edits.",
                        "Literal mentions require semantic review; no automatic knowledge promotion.",
                        "Matching is heuristic; absence of a candidate is not absence of evidence.",
                        "PDF text does not verify visual layout; scans/images are flagged, no OCR."]},
        "snapshot_reference": f"00_raw/airtable/{source.sha256}.json", "extraction_reference": extraction_ref,
        "review_reference": f"90_manual_review/historical_pilot/{digest(review_raw)}.json" if review_raw else None,
        "cases": cases}
    validate_provenance(result, extraction, snapshot)
    if processor_fingerprint() != code_fingerprint:
        raise ProviderError("pilot_code_changed_during_run")
    report_ref = f"30_cases/historical_pilot/{run_id}/review.md"
    case_ref = f"30_cases/historical_pilot/{run_id}/cases.json"
    if not dry_run:
        output.publish(result["snapshot_reference"], raw)
        output.publish(extraction_ref, extraction_bytes)
        if review_raw:
            output.publish(result["review_reference"], review_raw)
        output.publish(report_ref, render_report(result, extraction).encode("utf-8"))
        # cases.json is the completion manifest, published last. Retry adopts identical bytes.
        output.publish(case_ref, canonical(result))
    return {"processing_run": run_id, "dry_run": dry_run, "counts": counts,
            "report": str(output.path(report_ref)), "cases": str(output.path(case_ref)),
            "extraction": str(output.path(extraction_ref))}


def safe_text(value):
    text = str(value if value is not None else "unknown")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_[\]#|])", r"\\\1", text)


def render_report(result, extraction):
    run = result["processing_run"]
    lines = ["# Historical Case Reconstruction Pilot", "", f"Run: `{run['id']}`", "",
        "Status: awaiting human review. Source-reported facts, inferred outcomes and uncertain candidates are labeled separately.",
        "No prices, communications or knowledge rules are approved by this report.", "",
        f"[Full cases and field-level provenance](cases.json) · [Versioned extraction](../../../{result['extraction_reference']})",
        f"[Preserved Airtable snapshot](../../../{result['snapshot_reference']})", "",
        "## Coverage", "", f"```json\n{json.dumps(run['counts'], indent=2)}\n```", "",
        "The sample prioritizes observed service/status diversity and available context. It is not a statistical sample.", "",
        "## Review procedure", "",
        "Review each match using its signals and original sources. Candidate timeline entries and all evidence copied from them remain conditional on that association.",
        "Record accept/reject decisions with reviewer, timestamp and rationale using the documented pilot --review JSON format.",
        "Reviewing a match does not confirm extracted prices or technical interpretations. Rejected matches stay in history and leave the new case timeline.", ""]
    sources = {s["id"]: s for s in extraction["sources"]}

    def citation(ref):
        row = sources.get(ref["source_id"])
        loc = safe_text(ref.get("json_pointer") or ref.get("locator", ""))
        if row:
            return f"[original](../../../{row['reference']}) `{loc}` (SHA-256 `{row['sha256']}`)"
        return f"[Airtable snapshot](../../../{result['snapshot_reference']}) `{loc}`"

    for case in result["cases"]:
        lines += [f"## {safe_text(case['identity']['name']['value'])}", "", f"Case: `{case['id']}`", "",
            f"Service: {safe_text(case['identity']['service_type']['value'])}; CRM status: {safe_text(case['identity']['status']['value'])}; inferred outcome: **{case['outcome']['value']}**.", "",
            "### Source-reported scope", "", safe_text(case["requested_work"]["value"]), "",
            citation(case["requested_work"]["source_refs"][0]), "", "### Source-reported notes", "",
            safe_text(case["notes"]["value"]), "", citation(case["notes"]["source_refs"][0]), "", "### Timeline", ""]
        for event in case["timeline"]:
            lines.append(f"- {safe_text(event['timestamp']['value'])} [{event['time_precision']}; {event['association']}] {event['event_type']}: {safe_text(event['summary']['value'])}")
            if event["summary"]["source_refs"]:
                lines.append("  " + citation(event["summary"]["source_refs"][0]))
        lines += ["", "### Matching review", ""]
        for match in case["matches"]:
            lines.append(f"- `{match['message_id']}`: {match['status']}, {match['confidence']}; signals: "
                         + ", ".join(s["signal"] for s in match["signals"]) + "; competing cases: "
                         + (", ".join(match["competing_case_ids"]) or "none"))
        if not case["matches"]:
            lines.append("No email candidate found.")
        for title, key in (("Technical candidates", "technical_candidates"), ("Commercial candidates", "offer_candidates")):
            lines += ["", f"### {title}", ""]
            items = case[key]
            for item in items[:20]:
                lines.append(f"- [{item['association']}; uncertain] {safe_text(item.get('value', item.get('context')))}")
                lines.append("  " + citation(item["source_refs"][0]))
            if len(items) > 20:
                lines.append(f"{len(items) - 20} further candidates are retained in cases.json.")
            if not items:
                lines.append("None extracted; inspect source material before treating this as absence.")
        lines += ["", "### Gaps", ""] + [f"- {safe_text(s)}" for s in case["missing_information"] + case["issues"]]
        lines += [""]
    lines += ["## Document exceptions", "",
              "All document occurrences and their issues are retained in the extraction JSON. Images may include signatures, photos or plans; they have not been interpreted.", ""]
    image_count = sum(d["issues"] == ["image_requires_visual_review"] for d in extraction["documents"].values())
    lines.append(f"{image_count} image occurrences need visual review if relevant to the case. No OCR was run.")
    lines.append("")
    for document in extraction["documents"].values():
        if document["issues"] and document["issues"] not in (["body_occurrence_covered_by_original"], ["image_requires_visual_review"]):
            lines.append("- " + citation({"source_id": document["source_id"], "locator": "document"}) + ": " + ", ".join(document["issues"]))
    lines += ["", "## Limitations", ""] + [f"- {s}" for s in run["limitations"]]
    return "\n".join(lines) + "\n"
