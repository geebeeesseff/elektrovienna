"""Candidate-only operational evidence. Captured Airtable reads, never provider writes."""

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from .blind_eval import require
from .pilot_sources import AirtableSnapshot, PilotStore, canonical, digest, display, load_snapshot
from .shadow_input import save_checkpoint

STATES = ("NEEDS_YOUR_REPLY", "WAITING_INTERNAL", "WAITING_CUSTOMER", "UNCERTAIN",
          "NO_ACTION_REQUIRED", "HANDLED_ELSEWHERE", "CLOSED_OR_LOST")
CLOSED = {"abgeschlossen", "verloren"}


def normalized(text):
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", str(text or "").casefold()).encode("ascii", "ignore").decode()))


def link_ids(value):
    return [x["id"] if isinstance(x, dict) else x for x in value or []]


class OperationalEvidence:
    def __init__(self, store, state_path, *, snapshot_path=None, now=None):
        self.store, self.path = store, state_path
        self.now = now or datetime.now(timezone.utc)
        self.state = json.loads(state_path.read_bytes()) if state_path.exists() else {"owner_reviews": []}
        if snapshot_path:
            data, raw = load_snapshot(snapshot_path)
            require(data["base_id"] == "appeNai0w4Umr4Tm8", "operational_airtable_base")
            require(data.get("query_scope", {}).get("complete") is True, "operational_airtable_scope_incomplete")
            reference = f"00_raw/airtable/{digest(raw)}.json"
            PilotStore(store.root).publish(reference, raw)
            self.state["airtable_reference"] = reference
            save_checkpoint(self.path, self.state)
        require(bool(self.state.get("airtable_reference")), "operational_airtable_snapshot_required")
        raw = PilotStore(store.root).path(self.state["airtable_reference"]).read_bytes()
        require(digest(raw) == self.state["airtable_reference"].split("/")[-1][:-5], "operational_airtable_integrity")
        self.source = AirtableSnapshot(json.loads(raw), digest(raw))
        captured = datetime.fromisoformat(self.source.data["captured_at"].replace("Z", "+00:00"))
        require(captured.utcoffset() is not None, "operational_airtable_timestamp")
        self.fresh = timedelta(minutes=-5) <= self.now - captured <= timedelta(hours=24)
        self.owners = {}
        for ref in self.state["owner_reviews"]:
            for item in store.read(ref)["items"]:
                self.owners[item["thread_id"]] = {**item, "reference": ref}

    def match(self, packet):
        source, d = self.source, packet["queue_decision"]
        email, name = d["customer_email"].casefold(), normalized(d["customer"])
        text = normalized("\n".join(m["headers"].get("subject", "") + "\n" + m["text"] for m in packet["messages"]))
        matches = []
        for customer in source.data["customers"]:
            mail = (source.value("customers", customer, "Email") or "").strip().casefold()
            cname = normalized(source.value("customers", customer, "Name"))
            # A full name is only a candidate; project evidence is required below.
            if not ((email and email == mail) or (len(cname.split()) >= 2 and (cname == name or cname in text))):
                continue
            for ticket in source.data["tickets"]:
                if customer["id"] not in link_ids(source.value("tickets", ticket, "Customer")):
                    continue
                title = source.value("tickets", ticket, "Name") or ""
                request = source.value("tickets", ticket, "Customer request") or ""
                project_words = set(normalized(title + " " + request).split()) - set(cname.split())
                project_words -= {"elektro", "vienna", "anfrage", "kunde", "kundin", "gewunscht", "montage", "angebot", "wien", "vorhanden", "werden", "einer", "einem", "einen", "sollen", "bereits", "durch", "sowie"}
                overlap = sorted(w for w in project_words if len(w) >= 5 and w in text.split())
                matches.append({"id": ticket["id"], "title": title,
                    "status": display(source.value("tickets", ticket, "Status")) or "",
                    "notes": source.value("tickets", ticket, "Notes") or "", "request": request,
                    "closed_date": source.value("tickets", ticket, "Closed Date"),
                    "craftsmen": [x.get("name", x["id"]) if isinstance(x, dict) else x
                                  for x in source.value("tickets", ticket, "Craftsman") or []],
                    "project_signals": overlap, "identity_signal": "email" if mail == email else "name",
                    "source": source.ref("tickets", ticket),
                    "evidence_hash": digest(canonical([ticket, customer, [c for c in source.data["craftsmen"]
                        if c["id"] in link_ids(source.value("tickets", ticket, "Craftsman"))]]))})
        matches = list({m["id"]: m for m in matches}.values())
        # Even weak extra projects remain proposals. Never choose between several tickets.
        return sorted(matches, key=lambda m: m["id"])

    def classify(self, packet):
        from .pending_replies import fingerprint
        tid = packet["thread_id"]
        matches = self.match(packet)
        match = matches[0] if len(matches) == 1 and matches[0]["project_signals"] else None
        raw = self.store.read(packet["source_reference"])["gmail_thread"]
        gmail_key = digest(canonical([fingerprint(raw), [(r["thread_id"], r["fingerprint"])
                                                       for r in packet.get("related_recent_threads", [])]]))
        airtable_key = digest(canonical([(m["id"], m["evidence_hash"]) for m in matches]))
        key = digest(canonical([gmail_key, airtable_key]))
        owner = self.owners.get(tid)
        result = {"state": "UNCERTAIN", "reason": "No confident Airtable project match.", "matches": matches,
            "ticket_id": match["id"] if match else None, "conflicts": [], "waiting_on": "", "next_internal_action": "",
            "evidence_key": key, "gmail_key": gmail_key, "airtable_key": airtable_key,
            "airtable_reference": self.state["airtable_reference"], "airtable_captured_at": self.source.data["captured_at"],
            "owner_evidence": owner, "owner_applied": False}
        if not self.fresh or tid not in self.source.data.get("query_scope", {}).get("candidate_thread_ids", []):
            result["reason"] = "A fresh candidate-scoped Airtable read is required; the saved snapshot is not a live sync."
            return result
        if owner and owner["evidence_key"] != key:
            result["reason"] = "Material Gmail or Airtable evidence changed after the owner decision; recheck the previous feedback."
            return result
        if owner:
            result.update(state=owner["state"], reason=owner["note"] or "Owner operational decision.", owner_applied=True)
            if match and normalized(match["status"]) in CLOSED and owner["state"] in {"WAITING_INTERNAL", "NEEDS_YOUR_REPLY"}:
                result["conflicts"].append(f'Airtable: {match["status"]}; Giovanni: {owner["state"]}. Owner evidence controls this recommendation.')
            if owner["state"] == "WAITING_INTERNAL":
                result.update(waiting_on=owner["note"] or "Internal dependency not specified",
                              next_internal_action="Check the stated dependency with the responsible technician; no message is sent.")
            return result
        if len(matches) > 1:
            result["reason"] = "Several plausible Airtable tickets; choose the project before recommending a reply."
            return result
        if not match:
            return result
        status = normalized(match["status"])
        latest = next(m for m in packet["messages"] if m["id"] == packet["queue_decision"]["latest_customer_id"])
        # Reopening protection when a closure date is actually available. Missing dates stay unknown.
        closed = match["closed_date"]
        if status in CLOSED:
            baseline = self.state.get('closed_baselines', {}).get(tid)
            if baseline and baseline != gmail_key:
                result['reason'] = 'Gmail evidence changed since the closed/lost recommendation; review possible reopened work.'
            elif closed and datetime.fromtimestamp(latest["internal_ms"] / 1000, timezone.utc).date().isoformat() > closed[:10]:
                result["reason"] = "Customer wrote after the recorded closure date; possible reopened work or a new project."
            else:
                result.update(state="CLOSED_OR_LOST", reason=f'Airtable reports {match["status"]}; closure timing is {closed or "not recorded"}.')
            return result
        notes = match["notes"]
        dependency = re.search(r"(?i)(?:nächster schritt:\s*technische prüfung mit dem techniker|warten (?:auf|noch auf) [^.\n]+)", notes)
        if dependency:
            result.update(state="WAITING_INTERNAL", reason="Airtable records an outstanding internal step.", waiting_on=dependency.group(),
                          next_internal_action="Confirm the technical assessment with the responsible technician.")
        elif status in {"anfrage", "angebot", "beauftragt"}:
            result.update(state="NEEDS_YOUR_REPLY", reason="Unresolved customer turn and a matching open Airtable project; no contrary owner evidence.")
        else:
            result["reason"] = f'Airtable status {match["status"]} does not establish the next action.'
        return result

    def remember(self, rows):
        baselines = self.state.setdefault('closed_baselines', {})
        for row in rows:
            op = row['operational']
            if op['state'] in {'CLOSED_OR_LOST', 'HANDLED_ELSEWHERE'}:
                baselines.setdefault(row['thread_id'], op['gmail_key'])
            elif op['state'] == 'NEEDS_YOUR_REPLY':
                baselines.pop(row['thread_id'], None)
        save_checkpoint(self.path, self.state)

    def preserve_owner_items(self, items, original):
        """Store attributable evidence, with immutable bindings supplied by a reviewed batch."""
        original_ref = f"90_manual_review/live_shadow/{digest(original)}/original.json"
        self.store.publish(original_ref, original)
        value = {"kind": "operational_owner_evidence", "source": "Giovanni", "items": items,
                 "original_reference": original_ref, "external_actions": []}
        ref = self.store.document("result", value, review=True)
        if ref not in self.state["owner_reviews"]:
            self.state["owner_reviews"].append(ref)
            save_checkpoint(self.path, self.state)
        for item in items:
            self.owners[item["thread_id"]] = {**item, "reference": ref}
        return ref

    def import_feedback(self, raw):
        data = json.loads(raw)
        require(data.get("kind") == "operational_feedback" and data.get("source") == "Giovanni", "operational_feedback_format")
        timestamp = datetime.fromisoformat(data["recorded_at"].replace("Z", "+00:00"))
        require(timestamp.utcoffset() is not None and timestamp <= self.now + timedelta(minutes=5), "operational_feedback_timestamp")
        batch = self.store.read(data["batch_reference"])
        rows = {r["thread_id"]: r for r in batch["rows"]}
        require(data["items"] and len({i["thread_id"] for i in data["items"]}) == len(data["items"]), "operational_feedback_items")
        items = []
        for item in data["items"]:
            require(item["thread_id"] in rows and item["state"] in STATES and isinstance(item["note"], str)
                    and len(item["note"]) <= 4000, "operational_feedback_item")
            op = rows[item["thread_id"]]["operational"]
            items.append({**item, "source": "Giovanni", "recorded_at": data["recorded_at"],
                          "ticket_id": op["ticket_id"], "evidence_key": op["evidence_key"],
                          "batch_reference": data["batch_reference"]})
        return self.preserve_owner_items(items, raw)
