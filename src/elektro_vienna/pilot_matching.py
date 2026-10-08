"""Explainable proposals for the selected Airtable anchors; no automatic links."""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .pilot_sources import digest, canonical

EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def emails(value: str) -> set[str]:
    return {s.casefold() for s in EMAIL.findall(value)}


def identities(source, ticket, mailbox):
    links = source.value("tickets", ticket, "Customer") or []
    records = {r["id"]: r for r in source.data["customers"]}
    result = []
    for link in links:
        record = records.get(link["id"])
        if record is None:
            continue
        for name, kind in (("Email", "customer_email"), ("Name", "customer_name"), ("Adress", "customer_address")):
            value = source.value("customers", record, name)
            if not isinstance(value, str):
                continue
            values = sorted(emails(value) - {mailbox.casefold()}) if kind == "customer_email" else [value.strip()]
            for text in values:
                if kind == "customer_name" and (len(text.split()) < 2 or len(text) < 7):
                    continue
                if kind == "customer_address" and (len(text) < 8 or not re.search(r"\d", text)):
                    continue
                if text:
                    result.append({"kind": kind, "text": text, "source_ref": source.ref("customers", record, name)})
    return result


def propose(ticket: dict, identity: list[dict], source, message: dict, parsed: dict, source_id: str) -> dict | None:
    signals = []
    contact_signal = False
    for item in identity:
        expression = re.escape(item["text"])
        if item["kind"] == "customer_email":
            expression = r"(?<![\w.+-])" + expression + r"(?![\w.-])"
        else:
            expression = r"(?<!\w)" + expression.replace(r"\ ", r"\s+") + r"(?!\w)"
        for part in parsed["sections"]:
            match = re.search(expression, part["text"], re.I)
            if match:
                contact_signal = True
                signals.append({"signal": item["kind"], "anchor_ref": item["source_ref"],
                                "message_ref": {"source_id": source_id, "locator": part["locator"],
                                                "start": match.start(), "end": match.end(), "quote": match.group()}})
                break
    if not contact_signal:
        return None
    requested = source.value("tickets", ticket, "Customer Request Date")
    delta = None
    if requested:
        try:
            received = datetime.fromtimestamp(message["internal_ms"] / 1000, ZoneInfo("Europe/Vienna")).date()
            delta = (received - date.fromisoformat(requested[:10])).days
        except ValueError:
            pass
    near = delta is not None and -21 <= delta <= 120
    kinds = {s["signal"] for s in signals}
    # Weak textual identity outside the case window is not enough; email remains inspectable.
    if not near and "customer_email" not in kinds:
        return None
    if near:
        signals.append({"signal": "date_window", "days_from_request": delta,
                        "anchor_ref": source.ref("tickets", ticket, "Customer Request Date"),
                        "message_ref": {"source_id": source_id, "locator": "inventory:internal_ms"}})
    score = (5 * ("customer_email" in kinds) + 4 * ("customer_address" in kinds)
             + 2 * ("customer_name" in kinds) + int(near))
    return {"message_id": message["message_id"], "thread_id": message["thread_id"],
            "status": "candidate", "confidence": "strong_candidate" if score >= 6 else "weak_candidate",
            "score": score, "score_is_probability": False, "signals": signals,
            "days_from_request": delta, "competing_case_ids": [], "review": None}


def match_messages(source, selected, metadata, parsed_by_id, mailbox):
    matches = {r["id"]: [] for r in selected}
    for ticket in selected:
        identity = identities(source, ticket, mailbox)
        for message in metadata:
            parsed = parsed_by_id.get(message["message_id"])
            if parsed is None:
                continue
            candidate = propose(ticket, identity, source, message, parsed, message["source_id"])
            if candidate:
                matches[ticket["id"]].append(candidate)
        seeds = {m["thread_id"]: m["message_id"] for m in matches[ticket["id"]]}
        existing = {m["message_id"] for m in matches[ticket["id"]]}
        for message in metadata:
            if message["thread_id"] in seeds and message["message_id"] not in existing and message["message_id"] in parsed_by_id:
                matches[ticket["id"]].append({"message_id": message["message_id"], "thread_id": message["thread_id"],
                    "status": "candidate", "confidence": "thread_candidate", "score": 1, "score_is_probability": False,
                    "signals": [{"signal": "same_provider_thread", "seed_message_id": seeds[message["thread_id"]],
                                 "message_ref": {"source_id": message["source_id"], "locator": "inventory:thread_id"}}],
                    "days_from_request": None, "competing_case_ids": [], "review": None})
    owners = {}
    for case_id, candidates in matches.items():
        for candidate in candidates:
            owners.setdefault(candidate["message_id"], []).append(case_id)
    for case_id, candidates in matches.items():
        for candidate in candidates:
            candidate["competing_case_ids"] = [i for i in owners[candidate["message_id"]] if i != case_id]
            if candidate["competing_case_ids"]:
                candidate["confidence"] = "ambiguous"
        candidates.sort(key=lambda c: c["message_id"])
    return matches


def source_identity(row: dict) -> str:
    # Occurrence identity is distinct even when two files have identical content.
    return digest(canonical([row["mailbox"], row["message_id"], row["kind"], row["part_id"], row["sha256"]]))
