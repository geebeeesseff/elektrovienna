"""Single-case, offline review above immutable pilot candidates. No extraction or state I/O."""

import base64
import hashlib
import html
import json
import re
from datetime import datetime
from email.utils import getaddresses
from importlib.resources import files
from zoneinfo import ZoneInfo

from .models import ProviderError
from .archive_store import SourceArchive
from .pilot_sources import PilotStore, canonical, digest

VERSION = 1
SECTIONS = {
    "summary": "Was wollte der Kunde?", "narrative": "Was ist passiert?",
    "technical": "Wichtige technische Angaben", "commercial": "Preise und Konditionen",
    "outcome": "Ergebnis nach Leistungsstufe", "uncertainties": "Klärungen und offene Fragen",
}
STAGES = {"completed", "commissioned", "declined", "lost", "not_performed", "unknown"}
STAGE_LABELS = {"commissioned": "beauftragt", "completed": "abgeschlossen", "declined": "abgelehnt",
                "lost": "verloren", "not_performed": "nicht ausgeführt", "unknown": "unklar"}
ITEM_CHOICES = {"correct": "Richtig", "incorrect": "Falsch", "partially_correct": "Teilweise richtig",
                "unsure": "Unsicher", "important_info_missing": "Wichtige Information fehlt"}
ASSOCIATIONS = {"belongs": "Gehört zum Fall", "does_not_belong": "Gehört nicht dazu", "unsure": "Unsicher"}


class ReviewStore(PilotStore):
    def path(self, reference):
        if re.fullmatch(r"(?:30_cases/review_ux/[0-9a-f]{64}/(?:view.json|review.html)|"
                        r"00_raw/manual_documents/[0-9a-f]{64}\.pdf|"
                        r"20_extractions/manual_documents/[0-9a-f]{64}\.json|"
                        r"30_cases/document_review/[0-9a-f]{64}/(?:index.json|index.html)|"
                        r"90_manual_review/(?:sessions|completed)/[0-9a-f]{64}/[0-9]{8}\.json)", reference):
            target = self.root.joinpath(*reference.split("/"))
            self._guard(target)
            return target
        return super().path(reference)


def checked_json(store, reference, expected=None):
    raw = store.path(reference).read_bytes()
    if expected and digest(raw) != expected:
        raise ProviderError("review_input_integrity_mismatch")
    return json.loads(raw), raw


def email_blocks(text):
    """Display-only folding. Unmarked inline answers remain visible; originals are untouched.

    Only fold an unmarked Outlook/history suffix if it duplicates earlier content elsewhere
    in the caller. Here explicit > lines are safe to fold individually. Forwarded material
    is deliberately visible, since it may be the only copy of an offer.
    """
    text = text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for line in text.splitlines(keepends=True):
        quoted = bool(re.match(r"^\s*>", line)) or (not line.strip() and bool(blocks) and blocks[-1]["quoted"])
        if blocks and blocks[-1]["quoted"] == quoted:
            blocks[-1]["text"] += line
        else:
            blocks.append({"quoted": quoted, "text": line})
    return blocks


def fold_history(text, earlier):
    """Fold repeated paragraphs after a history header; retain unmatched inline answers.

    Formatting normalization is for comparison only. All displayed/source text survives.
    Forwarded-only material without a matching earlier message stays readable.
    """
    blocks = email_blocks(text)
    clean = "".join(b["text"] for b in blocks)
    def normalize(value):
        value = re.sub(r"(?m)^\s*>+\s?", "", value)
        value = re.sub(r"<(?:mailto:|https?://)[^>]+>|\[cid:[^\]]+\]", "", value)
        return re.sub(r"\s+", " ", value.replace("*", "")).strip()
    old_texts = [normalize(old) for old in earlier]
    marker = re.search(r"(?mi)^(?:Von:|From:|On [\s\S]{0,250}?wrote:|Am [\s\S]{0,250}?schrieb .{0,150}:)", clean)
    if marker and old_texts:
        folded = email_blocks(clean[:marker.start()])
        paragraphs = re.split(r"(\n\s*\n)", clean[marker.start():])
        for i in range(0, len(paragraphs), 2):
            paragraph = paragraphs[i] + (paragraphs[i + 1] if i + 1 < len(paragraphs) else '')
            norm = normalize(paragraph)
            repeated = len(norm) >= 20 and any(norm in old for old in old_texts)
            # Header groups contain metadata only. Do not classify arbitrary following text.
            header_only = all(re.match(r"^(?:From:|Von:|Sent:|Gesendet:|To:|An:|Cc:|Subject:|Betreff:|[_-]{5,})", line.strip(), re.I)
                              for line in paragraph.splitlines() if line.strip())
            new_blocks = [{"quoted": True, "text": paragraph}] if repeated or header_only else email_blocks(paragraph)
            for block in new_blocks:
                if folded and folded[-1]["quoted"] == block["quoted"]:
                    folded[-1]["text"] += block["text"]
                else:
                    folded.append(block)
        return folded
    return blocks


def header(message, key):
    return next((v for k, v in message["headers"] if k.casefold() == key), "")


def build_view(store, baseline, case_id, presentation):
    if not re.fullmatch(r"[0-9a-f]{64}", baseline):
        raise ProviderError("review_baseline_invalid")
    base_ref = f"30_cases/historical_pilot/{baseline}/cases.json"
    result, base_raw = checked_json(store, base_ref)
    if result["processing_run"]["id"] != baseline:
        raise ProviderError("review_baseline_mismatch")
    extraction_ref = result["extraction_reference"]
    extraction, _ = checked_json(store, extraction_ref, extraction_ref.split("/")[-1][:-5])
    snapshot_ref = result["snapshot_reference"]
    checked_json(store, snapshot_ref, extraction["snapshot_sha256"])
    case = next((c for c in result["cases"] if c["id"] == case_id), None)
    if case is None or presentation["case_id"] != case_id or presentation["baseline_id"] != baseline:
        raise ProviderError("review_case_mismatch")
    feedback = presentation["human_feedback"]
    prior_review = presentation.get("prior_review")
    if prior_review:
        envelope, _ = checked_json(store, prior_review["reference"])
        record = envelope["record"]
        namespace = 'completed' if record.get('kind') == 'completed_case_review' else 'sessions'
        expected_ref = f'90_manual_review/{namespace}/{record["view_id"]}/{record["revision"]:08d}.json'
        previous, _ = checked_json(store, f'30_cases/review_ux/{record["view_id"]}/view.json', record["view_id"])
        if (prior_review["reference"] != expected_ref or digest(canonical(record)) != envelope["sha256"]
                or envelope["sha256"] != prior_review["sha256"] or previous["case_id"] != case_id
                or previous["baseline_id"] != baseline):
            raise ProviderError("review_prior_feedback_mismatch")
        if namespace == 'completed' and (record.get('status') != 'completed' or record.get('whole_case_acknowledged') is not True):
            raise ProviderError("review_prior_feedback_mismatch")
    if not feedback.get("reviewer") or not feedback.get("verbatim") or not feedback.get("received_on"):
        raise ProviderError("review_feedback_missing")
    feedback_raw = canonical({"format_version": VERSION, "kind": "human_case_evaluation",
                              "case_id": case_id, "baseline_id": baseline, **feedback})
    feedback_id = digest(feedback_raw)
    feedback_ref = f"90_manual_review/historical_pilot/{feedback_id}.json"
    ids = {m["message_id"] for m in case["matches"]}
    sources = {s["id"]: s for s in extraction["sources"] if s["message_id"] in ids}
    for source in sources.values():
        SourceArchive(store.root).path(source["reference"])
    sections = { (m["source_id"], s["locator"]): s["text"]
                 for m in list(extraction["messages"].values()) + list(extraction["documents"].values())
                 if m["source_id"] in sources for s in m["sections"] }
    sections[("human_feedback", "verbatim")] = feedback["verbatim"]
    collection_ref = presentation.get("document_collection_reference")
    if collection_ref:
        collection_id = collection_ref.split("/")[-2]
        collection, _ = checked_json(store, collection_ref, collection_id)
        for document in collection["documents"]:
            if document.get("case_id") != case_id:
                continue
            row = document["source"]
            store.path(row["reference"])
            extracted, _ = checked_json(store, document["extraction_reference"], document["extraction_sha256"])
            sources[row["id"]] = row
            for section in extracted["sections"]:
                sections[(row["id"], section["locator"])] = section["text"]
    seen = set()
    for kind in SECTIONS:
        for item in presentation["sections"][kind]:
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,70}", item["id"]) or item["id"] in seen:
                raise ProviderError("review_item_id_invalid")
            seen.add(item["id"])
            if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("title", "text", "certainty")):
                raise ProviderError("review_item_invalid")
            if kind == "outcome" and item.get("status") not in STAGES:
                raise ProviderError("review_stage_invalid")
            if not item["source_refs"]:
                raise ProviderError("review_item_source_missing")
            for ref in item["source_refs"]:
                content = sections.get((ref["source_id"], ref["locator"]))
                start, end = ref.get("start"), ref.get("end")
                if (content is None or type(start) is not int or type(end) is not int
                        or not 0 <= start < end <= len(content) or content[start:end] != ref["quote"]):
                    raise ProviderError("review_citation_invalid")
    inventory = {m["message_id"]: m for m in extraction["inventory_scope"]}
    conversations = {}
    for match in sorted(case["matches"], key=lambda m: (inventory[m["message_id"]]["internal_ms"], m["message_id"])):
        mid = match["message_id"]
        message = extraction["messages"][mid]
        tid = match["thread_id"] or "message-" + mid
        thread = conversations.setdefault(tid, {"id": tid, "subject": header(message, "subject"), "messages": [], "participants": []})
        participants = getaddresses([header(message, "from"), header(message, "to"), header(message, "cc")])
        thread["participants"] = sorted(set(thread["participants"] + [name or addr for name, addr in participants if name or addr]))
        body = next((s for s in message["sections"] if ":decoded-text" in s["locator"]), None)
        if body is None:
            body = next((s for s in message["sections"] if s["locator"].startswith("mime:")), {"text": "Kein lesbarer Nachrichtentext in der vorhandenen Extraktion.", "locator": "unavailable"})
        text = body["text"]
        previous = [m["text"] for m in thread["messages"]]
        thread["messages"].append({"id": mid, "sender": header(message, "from"), "subject": header(message, "subject"),
            "date": datetime.fromtimestamp(inventory[mid]["internal_ms"] / 1000, ZoneInfo("Europe/Vienna")).isoformat(),
            "text": text, "blocks": fold_history(text, previous), "source_id": message["source_id"],
            "locator": body["locator"], "match": match,
            "documents": [d for sid, d in extraction["documents"].items() if sid in sources and sources[sid]["message_id"] == mid]})
    assets = {name: files("elektro_vienna").joinpath(name).read_text(encoding="utf-8") for name in ("review.css", "review.js")}
    view = {"format_version": VERSION, "operation": "single_case_review_view", "case_id": case_id,
            "baseline_id": baseline, "baseline_reference": base_ref, "baseline_sha256": digest(base_raw),
            "extraction_reference": extraction_ref, "snapshot_reference": snapshot_ref,
            "feedback_reference": feedback_ref, "feedback_sha256": feedback_id,
            "renderer_sha256": digest(files("elektro_vienna").joinpath("review_ui.py").read_bytes()),
            "assets_sha256": {k: digest(v.encode()) for k, v in assets.items()},
            "identity": {k: case["identity"][k] for k in ("name", "customers", "service_type", "status")},
            "curation": presentation.get("curation", "Manuell kuratierter Review-Entwurf"),
            "sections": presentation["sections"], "conversations": list(conversations.values()),
            "sources": sources, "human_feedback": feedback,
            "prior_review": prior_review,
            "document_collection_reference": collection_ref,
            "candidate_counts": {"technical": len(case["technical_candidates"]), "commercial": len(case["offer_candidates"])}}
    return view, feedback_ref, feedback_raw, assets


def esc(value):
    return html.escape(str(value), quote=True)


def details(label, content):
    return f"<details><summary>{esc(label)}</summary>{content}</details>"


def control(kind, identity, choices, live=None):
    if live:
        return (f'<div class="review-control comment-only" data-kind="{kind}" data-id="{esc(identity)}">'
                '<label>Korrekturkommentar <textarea rows="2" placeholder="Stichworte reichen. Was soll korrigiert oder ergänzt werden?"></textarea></label>'
                '<p class="comment-state muted"></p></div>')
    options = '<option value="">Noch nicht bewertet</option>' + ''.join(f'<option value="{esc(k)}">{esc(v)}</option>' for k, v in choices.items())
    return (f'<div class="review-control" data-kind="{kind}" data-id="{esc(identity)}">'
            f'<label>Bewertung <select>{options}</select></label>'
            '<label>Korrektur / Kommentar <textarea rows="2" placeholder="Optional: Was sollte anders stehen?"></textarea></label></div>')


def render_html(view, view_id, assets, live=None):
    parts = []
    source_labels = {sid: s.get("filename") or "E-Mail" for sid, s in view["sources"].items()}
    source_labels["human_feedback"] = "Giovannis Rückmeldung · " + view["human_feedback"]["received_on"]
    message_lookup = {m["id"]: m for t in view["conversations"] for m in t["messages"]}
    def citation(ref):
        sid = ref["source_id"]
        row = view["sources"].get(sid)
        links = ''
        label = source_labels[sid]
        if row:
            mid = row.get("message_id")
            if mid in message_lookup:
                message = message_lookup[mid]
                label += ' · ' + message["date"][:10] + ' · ' + message["sender"]
                links += f'<a class="source-jump" href="#message-{esc(mid)}">Zur Quellmail</a> · '
            links += f'<a href="../../../{esc(row["reference"])}" target="_blank" rel="noopener">Original öffnen</a>'
        return f'<blockquote><strong>{esc(label)}</strong><p>{links}</p><pre>{esc(ref["quote"])}</pre></blockquote>'
    for key, label in SECTIONS.items():
        items = []
        for item in view["sections"][key]:
            refs = ''.join(citation(r) for r in item["source_refs"])
            technical = details("Technischer Nachweis", '<pre>' + esc(json.dumps(item["source_refs"], ensure_ascii=False, indent=2)) + '</pre>')
            status = f'<span class="status">{esc(STAGE_LABELS[item["status"]])}</span>' if "status" in item else ''
            review_note = '<p class="human-correction">' + esc(item['review_note']) + '</p>' if item.get('review_note') else ''
            items.append(f'<article id="{esc(item["id"])}"><h3>{esc(item["title"])} {status}</h3><p class="case-text">{esc(item["text"])}</p><p class="human-correction"></p>'
                         f'<p class="certainty">{esc(item["certainty"])}</p>'
                         + review_note + details("Belege & Kommentar" if live else "Belege & Bewertung", refs + technical + control("item", item["id"], ITEM_CHOICES, live)) + '</article>')
        parts.append(f'<section id="section-{key}"><h2>{label}</h2>' + ''.join(items) + '</section>')
    threads = []
    for thread in view["conversations"]:
        msgs = thread["messages"]
        messages = []
        for msg in msgs:
            blocks = ''.join(details("Zitierter Verlauf anzeigen", '<pre>' + esc(b["text"]) + '</pre>') if b["quoted"] else '<pre>' + esc(b["text"]) + '</pre>' for b in msg["blocks"])
            docs = []
            for doc in msg["documents"]:
                row = view["sources"][doc["source_id"]]
                if not any(s["text"].strip() for s in doc["sections"]):
                    continue
                docs.append(details(row.get("filename") or "Anhang", ''.join('<pre>' + esc(s["text"]) + '</pre>' for s in doc["sections"])))
            source = view["sources"][msg["source_id"]]
            provenance = {"message_id": msg["id"], "source": source, "locator": msg["locator"], "match": msg["match"],
                          "attachments": [s for s in view["sources"].values() if s.get("message_id") == msg["id"] and s["kind"] != "message"]}
            raw_link = f'<a href="../../../{esc(source["reference"])}" download>Unverändertes E-Mail-Original (.eml)</a>'
            messages.append(f'<article class="message" id="message-{esc(msg["id"])}"><h4>{esc(msg["date"][:16].replace("T", " "))} · {esc(msg["sender"])}</h4>{blocks}'
                + ''.join(docs) + details("Kommentar zur Nachricht" if live else "Einzelne Nachricht abweichend bewerten", control("message", msg["id"], ASSOCIATIONS, live))
                + details("Original / technische Provenienz", raw_link + '<pre>' + esc(json.dumps(provenance, ensure_ascii=False, indent=2)) + '</pre>') + '</article>')
        label = f'{thread["subject"]} · {len(msgs)} Nachrichten · {msgs[0]["date"][:10]} – {msgs[-1]["date"][:10]}'
        threads.append(details(label, '<p class="muted">' + esc(', '.join(thread["participants"])) + '</p>'
            + control("thread", thread["id"], ASSOCIATIONS, live)
            + (f'<label class="ack"><input type="checkbox" data-ack="{esc(thread["id"])}"> Die Gesprächsbewertung gilt für alle {len(msgs)} hier gezeigten Nachrichten, außer meinen Einzelabweichungen.</label>' if not live else '')
            + '<p class="muted">Bei Themenwechseln einzelne Nachrichten abweichend bewerten oder das Gespräch als unsicher markieren. Die Gruppierung allein bestätigt keine Zuordnung.</p>'
            + ''.join(messages)))
    parts.append('<section id="sources"><h2>Quellen</h2>' + details(f'{len(threads)} Gespräche · {sum(len(t["messages"]) for t in view["conversations"])} Nachrichten', ''.join(threads)) + '</section>')
    identity = view["identity"]
    config = {"format_version": VERSION, "view_id": view_id, "case_id": view["case_id"], "baseline_id": view["baseline_id"]}
    if live:
        config["live"] = live
    config_json = json.dumps(config).replace('<', '\\u003c').replace('&', '\\u0026')
    script = 'const CONTEXT = ' + config_json + ';\n' + assets["review.js"]
    if live:
        script += '\n' + files("elektro_vienna").joinpath("review_live.js").read_text(encoding="utf-8")
    sha = lambda s: base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
    connect = "'self'" if live else "'none'"
    policy = f"default-src 'none'; script-src 'sha256-{sha(script)}'; style-src 'sha256-{sha(assets['review.css'])}'; connect-src {connect}; img-src 'none'; base-uri 'none'; form-action 'none'"
    extra_nav = ''
    if view.get("document_collection_reference"):
        extra_nav = '<p><a href="../../../' + esc(view["document_collection_reference"].replace('index.json', 'index.html')) + '">Alectra: Angebote und Rechnungen im Vergleich</a></p>'
    save_hint = ('Ein Korrekturkommentar genügt; Stichworte und Tippfehler sind in Ordnung. Automatisches Speichern sichert nur deinen Entwurf. Erst mit „Fallprüfung abschließen“ am Seitenende gelten unkommentierte Falltexte als richtig. Danach arbeitet der Agent deine Kommentare in die nächste Fallfassung ein.' if live else 'Bewertungen bleiben bis zum Download nur in diesem Fenster. Eine neue JSON-Datei dokumentiert jede Fassung; Quellen und Airtable bleiben unverändert. Auch ein Teilreview ist möglich.')
    completion = ('<div class="completion"><label class="ack"><input id="whole-case-ack" type="checkbox"> Ich habe den gesamten dargestellten Fall geprüft. Alle Falltexte ohne Korrekturkommentar können als fachlich richtig behandelt werden.</label>'
                  '<label class="ack"><input id="sources-ack" type="checkbox"> Die dargestellten Gespräche/Quellen gehören insgesamt zu diesem Fall.</label>'
                  '<p class="muted">Quellenbestätigung optional. Ein Kommentar am Gespräch nimmt dessen Nachrichten von der pauschalen Bestätigung aus; ein Kommentar an einer einzelnen Nachricht nimmt nur diese aus.</p>'
                  '<button id="complete-review" type="button">Fallprüfung abschließen</button><p id="completion-state" role="status">Noch nicht abgeschlossen.</p></div>') if live else ''
    live_bar = '<aside class="save-bar"><label>Reviewer <input id="live-reviewer" placeholder="Dein Name"></label><strong id="save-state" role="status">Gespeicherten Stand laden …</strong><button type="button" id="save-now">Jetzt speichern</button></aside>' if live else ''
    return ('<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        + f'<meta http-equiv="Content-Security-Policy" content="{esc(policy)}"><title>{esc(identity["name"]["value"])} · Fallreview</title><style>{assets["review.css"]}</style></head><body>'
        + '<header><p class="eyebrow">ELEKTRO VIENNA / FALLREVIEW</p><h1>' + esc(identity["name"]["value"]) + '</h1>'
        + f'<p>{esc(", ".join(c["name"] for c in identity["customers"]["value"] or []))} · {esc(identity["service_type"]["value"])} · CRM: <strong>{esc(identity["status"]["value"])}</strong></p>'
        + '<p class="notice">Der CRM-Status beschreibt den Fallabschluss. Den tatsächlich ausgeführten Umfang zeigen die Leistungsstufen unten.</p>'
        + '<nav><a href="#section-summary">Anliegen</a><a href="#section-narrative">Verlauf</a><a href="#section-commercial">Preise</a><a href="#section-outcome">Ergebnis</a><a href="#sources">Quellen</a></nav>' + extra_nav + '</header>' + live_bar + '<main>'
        + '<p class="muted">' + (save_hint if live else 'Review-Entwurf aus vorhandenen Quellen und Giovannis Rückmeldung. Belege und Korrekturfelder lassen sich je Eintrag öffnen.') + '</p>'
        + ''.join(parts)
        + '<section><h2>Review speichern</h2><p>' + save_hint + '</p>'
        + '<label>Reviewer <input id="reviewer" autocomplete="name" placeholder="Dein Name"></label>' + completion + '<div class="actions"><button id="download" type="button">Review als JSON herunterladen</button><label class="load">Gespeichertes Review fortsetzen <input id="resume" type="file" accept=".json,application/json"></label></div><p id="result" role="status" aria-live="polite"></p></section>'
        + details("Technische Grundlage und ursprüngliche Rückmeldung", '<pre>' + esc(json.dumps({k: view[k] for k in ("baseline_reference", "baseline_sha256", "extraction_reference", "feedback_reference", "candidate_counts", "curation", "human_feedback")}, ensure_ascii=False, indent=2)) + '</pre>')
        + '</main><footer>Lokal · keine automatische Wissensübernahme</footer>'
        + f'<script>{script}</script></body></html>')


def publish_view(store, baseline, case_id, presentation):
    try:
        view, feedback_ref, feedback_raw, assets = build_view(store, baseline, case_id, presentation)
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("review_presentation_invalid") from None
    raw = canonical(view)
    view_id = digest(raw)
    prefix = f"30_cases/review_ux/{view_id}"
    html_bytes = render_html(view, view_id, assets).encode()
    store.publish(feedback_ref, feedback_raw)
    store.publish(prefix + "/review.html", html_bytes)
    store.publish(prefix + "/view.json", raw)  # completion marker last
    return {"view_id": view_id, "html": str(store.path(prefix + "/review.html")), "feedback": str(store.path(feedback_ref))}


def validate_review(view, view_id, review):
    try:
        return _validate_review(view, view_id, review)
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("review_export_invalid") from None


def _validate_review(view, view_id, review):
    if any(review.get(k) != v for k, v in {"format_version": VERSION, "view_id": view_id,
           "case_id": view["case_id"], "baseline_id": view["baseline_id"]}.items()):
        raise ProviderError("review_export_context_mismatch")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ProviderError("review_reviewer_missing")
    try:
        if datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ProviderError("review_timestamp_invalid") from None
    items = {i["id"] for rows in view["sections"].values() for i in rows}
    threads = {t["id"]: t for t in view["conversations"]}
    messages = {m["id"] for t in threads.values() for m in t["messages"]}
    allowed = {"item": items, "thread": set(threads), "message": messages}
    decisions = {}
    for d in review.get("decisions", []):
        kind, identity = d.get("kind"), d.get("id")
        key = (kind, identity)
        if kind not in allowed or identity not in allowed[kind] or key in decisions:
            raise ProviderError("review_decision_scope_invalid")
        if d.get("status") not in (ITEM_CHOICES if kind == "item" else ASSOCIATIONS) or not isinstance(d.get("comment"), str):
            raise ProviderError("review_decision_invalid")
        if kind == "thread" and d["status"] != "unsure" and d.get("all_shown_messages_acknowledged") is not True:
            raise ProviderError("review_thread_acknowledgement_required")
        decisions[key] = d
    if not decisions:
        raise ProviderError("review_no_decisions")
    # Derive the actual message scope from the immutable view, never from browser-supplied IDs.
    expanded = []
    for tid, thread in threads.items():
        for msg in thread["messages"]:
            d = decisions.get(("message", msg["id"]), decisions.get(("thread", tid)))
            if d:
                expanded.append({"message_id": msg["id"], "thread_id": tid, "status": d["status"],
                                 "comment": d["comment"], "decision_level": d["kind"]})
    return expanded


def import_review(store, view_id, raw):
    view, _ = checked_json(store, f"30_cases/review_ux/{view_id}/view.json", view_id)
    review = json.loads(raw)
    expanded = validate_review(view, view_id, review)
    input_ref = f"90_manual_review/historical_pilot/{digest(raw)}.json"
    envelope = canonical({"format_version": VERSION, "kind": "case_ui_review", "view_id": view_id,
                          "original_export_reference": input_ref, "review": review,
                          "message_decisions": expanded, "effect": "evaluation_only_no_candidate_or_knowledge_mutation"})
    reference = f"90_manual_review/historical_pilot/{digest(envelope)}.json"
    store.publish(input_ref, raw)
    store.publish(reference, envelope)
    return {"review": str(store.path(reference)), "export": str(store.path(input_ref)), "message_decisions": len(expanded)}
