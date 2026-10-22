"""Current conversation reconciliation, separate from durable Gmail history ingestion."""

import base64
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from html import escape
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

from .archive_store import SourceArchive, source_reference
from .blind_eval import require
from .gmail import parse_message
from .models import EXCLUDED_LABELS, ProviderError
from .pending_suggestions import generate, SIGNOFF, VERSION, INSTRUCTIONS
from .pilot_parse import html_text, parse_document, parse_email
from .pilot_sources import canonical, digest
from .shadow_input import MAILBOX, save_checkpoint

DAYS = 30
MAX_THREADS = 500
SIGNATURE_HEADERS = ("from", "to", "cc", "bcc", "reply-to", "subject", "auto-submitted",
                     "list-id", "list-unsubscribe", "precedence", "delivered-to", "message-id", "in-reply-to", "references")
RELATED_RULE = """Related recent Gmail threads are supplemental current evidence, not a merged case.
They share an evidenced customer email address. That alone does NOT prove the same job.
Compare actual scope and chronology. If another thread contains the substantive reply to this
primary inquiry, or continues that same inquiry with a newer customer turn, do not propose a
duplicate first response here: mark this older primary thread no_reply_needed. The newer
unresolved turn is evaluated under its own primary thread. Keep genuinely different jobs
separate. Do not infer a reply from shared identity alone. Use uncertain if the relationship
cannot be established. Refer to the supporting thread/message IDs in the reason.
For pending, latest_customer_id must still belong to this primary thread."""


def addresses(value):
    return {address.casefold() for _, address in getaddresses([value]) if address}


def is_form(headers, body):
    sender = headers.get("from", "").lower()
    # Wix delivery can use EV's own envelope. Require submission evidence in that case.
    wix = any(domain in sender for domain in ("wix.com", "wixforms.com", "wixsite.com"))
    form = re.search(r"(?im)^\s*(?:e-?mail(?: address)?|e-mail-adresse)\s*[:：]?\s*\n?\s*[^\s@]+@[^\s@]+", body)
    return bool(wix and form or form and re.search(
        r"(?i)wix|formular|form submission|neue nachricht|new message|hat.*formular", headers.get("subject", "") + "\n" + body))


def top_text(body):
    """Display the newest turn; the untouched full text remains in source/model context."""
    return re.split(r"(?im)^(?:>|\s*On .{5,200}wrote:|\s*Am .{5,200}schrieb.*:|"
                    r"\s*-{2,}\s*(?:Original|Forwarded|Weitergeleitete)|\s*Von:\s.+@)", body, maxsplit=1)[0].strip()


def body_from_payload(payload):
    if payload.get("filename"):
        return ""
    if payload.get("mimeType", "").startswith("multipart/"):
        parts = payload.get("parts", [])
        if payload["mimeType"] == "multipart/alternative":
            parts = ([p for p in parts if p.get("mimeType") == "text/plain"] or
                     [p for p in parts if p.get("mimeType") == "text/html"])[:1]
        return "\n".join(body_from_payload(p) for p in parts)
    if payload.get("mimeType") not in ("text/plain", "text/html"):
        return ""
    data = payload.get("body", {}).get("data")
    if not data:
        return ""
    content_type = next((h["value"] for h in payload.get("headers", [])
                         if h["name"].lower() == "content-type"), payload["mimeType"])
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    part = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + content_type + "\r\n\r\n").encode() + raw)
    try:
        text = raw.decode(part.get_content_charset() or "utf-8")
    except (UnicodeError, LookupError):
        text = raw.decode("utf-8", errors="replace")
    return html_text(text) if payload["mimeType"] == "text/html" else text


def conversation(raw):
    messages = []
    for value in raw["messages"]:
        m = parse_message(value)
        if EXCLUDED_LABELS.intersection(m.labels):
            continue
        headers = {h["name"].lower(): h["value"] for h in value["payload"].get("headers", [])
                   if h["name"].lower() in SIGNATURE_HEADERS}
        body = body_from_payload(value["payload"])
        form = is_form(headers, body)
        senders = addresses(headers.get("from", ""))
        ours = any(a == MAILBOX or a.endswith("@elektrovienna.at") for a in senders)
        automatic = (headers.get("auto-submitted", "").lower() not in ("", "no")
                     or bool(re.search(r"(?i)automatische antwort|automatic reply|out of office|abwesenheit",
                                       headers.get("subject", ""))))
        direction = "incoming" if form else "outgoing" if ours or "SENT" in m.labels else "incoming"
        messages.append({"id": m.message_id, "internal_ms": m.internal_ms, "headers": headers,
                         "text": body, "latest_text": top_text(body), "direction": direction,
                         "form_submission": form, "automatic": bool(automatic and not form),
                         "attachments": [asdict(a) for a in m.attachments]})
    return sorted(messages, key=lambda m: (m["internal_ms"], m["id"]))


def incoming_addresses(message):
    if message["form_submission"]:
        values = re.findall(r"(?im)^\s*e-?mail(?: address|-adresse)?\s*[:：]?\s*\n?\s*([^\s<>]+@[^\s<>]+)", message["text"])
        return {v.casefold() for v in values}
    return addresses(message["headers"].get("from", "")) | addresses(message["headers"].get("reply-to", ""))


def recipients(message):
    return set().union(*(addresses(message["headers"].get(k, "")) for k in ("to", "cc", "bcc")))


def gate(messages):
    meaningful = [m for m in messages if not m["automatic"] and
                  (m["latest_text"].strip() or m["attachments"])]
    if not meaningful:
        return "non_customer", None
    latest = meaningful[-1]
    if latest["direction"] == "outgoing":
        incoming = [m for m in meaningful[:-1] if m["direction"] == "incoming"]
        if not incoming:
            return "non_customer", latest
        correspondents = set().union(*(incoming_addresses(m) for m in incoming))
        # An internal forward/reply does not prove the customer received an answer.
        # With multiple parties, defer recipient/scope interpretation to the model.
        if correspondents and correspondents <= recipients(latest):
            return "answered", latest
        return "candidate", latest
    if not latest["form_submission"] and (
            latest["headers"].get("list-id") or latest["headers"].get("list-unsubscribe")):
        return "non_customer", latest
    # Narrow acknowledgement rule: do not let quoted old questions reopen a resolved turn.
    acknowledgement = re.split(r"(?im)^(?:mit freundlichen|viele|liebe|beste|herzliche)\s+grü",
                               latest["latest_text"])[0].strip()
    if not latest["attachments"] and re.fullmatch(
            r"(?is)\s*(?:vielen|besten|herzlichen)?\s*dank(?:e)?(?:\s+(?:sehr|schön|vielmals|im voraus))?[.!\s]*",
            acknowledgement):
        return "no_reply_needed", latest
    return "candidate", latest


def recent_ids(reader, cutoff):
    ids, token, seen = [], None, set()
    while True:
        page = reader.list_threads(cutoff, token, 100)
        ids.extend(i for i in page.message_ids if i not in ids)
        require(len(ids) <= MAX_THREADS, "pending_recent_window_exceeds_500_threads")
        token = page.next_token
        if not token:
            return ids
        require(token not in seen and len(seen) < 10, "pending_thread_pagination_incomplete")
        seen.add(token)


def evidence(reader, store, raw, messages):
    """Preserve provider snapshot and source originals; reuse immutable bytes when available."""
    snapshot = store.document("input", {"kind": "pending_thread_source", "mailbox": MAILBOX,
                                       "gmail_thread": raw})
    archive = SourceArchive(store.root)
    source_refs, images, documents = [], [], []
    for message in messages:
        mid = message["id"]
        ref = source_reference(MAILBOX, mid)
        path = archive.path(ref)
        if path.exists():
            original = path.read_bytes()
        else:
            original_message = reader.get_original(mid)
            require(original_message.thread_id == raw["id"] and original_message.message_id == mid
                    and original_message.internal_ms == message["internal_ms"], "pending_source_identity")
            original = original_message.data
        record = archive.publish(ref, original)
        source_refs.append(asdict(record))
        parsed = parse_email(original)
        full = "\n".join(s["text"] for s in parsed["sections"] if s["locator"].startswith("mime:"))
        message["text"], message["latest_text"] = full, top_text(full)
        # Prefer correctly decoded RFC headers over the API display representation.
        message["headers"] = {k.lower(): v for k, v in parsed["headers"] if k.lower() in SIGNATURE_HEADERS}
        for attachment in message["attachments"]:
            from .models import Attachment
            a = Attachment(**attachment)
            # Embedded empty containers/text are not useful document evidence.
            if not a.filename and not a.mime_type.startswith("image/"):
                continue
            ref = source_reference(MAILBOX, mid, a.part_id)
            path = archive.path(ref)
            data = path.read_bytes() if path.exists() else reader.get_attachment(mid, a)
            saved = archive.publish(ref, data)
            source_refs.append(asdict(saved))
            if a.mime_type in ("image/jpeg", "image/png", "image/webp") and len(data) <= 10_000_000:
                images.append({"path": str(path), "filename": a.filename, "message_id": mid,
                               "sha256": saved.sha256})
            else:
                documents.append({"message_id": mid, "filename": a.filename,
                                  "sha256": saved.sha256, **parse_document(data, a.mime_type, a.filename)})
    return {"thread_id": raw["id"], "messages": messages, "source_reference": snapshot,
            "sources": source_refs, "images": images, "documents": documents}


def validate_decision(decision, packet):
    require(isinstance(decision, dict) and decision.get("thread_id") == packet["thread_id"], "pending_decision_identity")
    require(decision.get("state") in {"pending", "no_reply_needed", "non_customer", "uncertain"}, "pending_decision_state")
    for field in ("latest_customer_id", "customer", "customer_email", "reason", "previous_context", "suggested_response"):
        require(isinstance(decision.get(field), str), "pending_decision_fields")
    require(isinstance(decision.get("internal_checks"), list) and
            all(isinstance(v, str) for v in decision["internal_checks"]), "pending_internal_checks")
    if decision["state"] == "pending":
        messages = packet["messages"]
        latest = next((m for m in messages if m["id"] == decision["latest_customer_id"]), None)
        email = decision["customer_email"].strip().casefold()
        require(latest is not None and latest["direction"] == "incoming" and not latest["automatic"]
                and gate([latest])[0] == "candidate" and email in incoming_addresses(latest),
                "pending_decision_must_reference_latest_unanswered_turn")
        later = messages[messages.index(latest) + 1:]
        require(not any(not m["automatic"] and (m["latest_text"].strip() or m["attachments"])
                    and ((m["direction"] == "incoming" and email in incoming_addresses(m))
                         or (m["direction"] == "outgoing" and email in recipients(m))) for m in later),
                "pending_decision_must_reference_latest_unanswered_turn")
        draft = decision["suggested_response"].strip()
        require(len(draft) > len(SIGNOFF) + 30 and draft.endswith(SIGNOFF), "pending_draft_required")
        require(bool(decision["customer"].strip()), "pending_customer_required")
        identity_evidence = "\n".join(latest["headers"].values()) + "\n" + latest["text"]
        require(bool(re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", email))
                and email in identity_evidence.casefold() and email != MAILBOX,
                "pending_customer_email_must_be_evidenced")
        require(not any(token in draft for token in ("[Name]", "[Preis]", "[Adresse]", "TODO")), "pending_placeholder")
    else:
        require(not decision["suggested_response"].strip(), "excluded_thread_must_not_have_draft")


def resolve_identity(decision, packet):
    """Unknown identity is an explicit review item, never an empty card or invented name."""
    if decision.get("state") != "pending":
        return decision
    latest = next((m for m in packet["messages"] if m["id"] == decision.get("latest_customer_id")), None)
    email = decision.get("customer_email", "").strip().casefold()
    text = "\n".join(latest["headers"].values()) + "\n" + latest["text"] if latest else ""
    if (not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", email)
            or email not in text.casefold() or email == MAILBOX):
        return {**decision, "state": "uncertain", "suggested_response": "",
                "reason": decision.get("reason", "") + " Customer reply identity could not be verified from the latest turn."}
    if not decision.get("customer", "").strip():
        return {**decision, "customer": email}
    return decision


def fingerprint(raw):
    # Read state/category changes do not change the reply state or invalidate a suggestion.
    copy = json.loads(json.dumps(raw))
    def stable_parts(part):
        # Gmail rotates opaque attachment retrieval handles for immutable message parts.
        # Identity is message ID + partId; original/attachment bytes are separately verified.
        part.get("body", {}).pop("attachmentId", None)
        for child in part.get("parts", []):
            stable_parts(child)
    for m in copy["messages"]:
        m["labelIds"] = sorted(set(m.get("labelIds", [])) & {"SENT", "DRAFT", "SPAM", "TRASH"})
        m.pop("historyId", None)
        stable_parts(m["payload"])
    copy.pop("historyId", None)
    return digest(canonical(copy))


def atomic_view(store, html):
    target = store.root / "30_cases/live_shadow/pending_replies.html"
    store._guard(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(html.encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        store._guard(target)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(target)


def has_correspondent(raw, email):
    """Exact addressing evidence only; quoted addresses never create an association."""
    for m in conversation(raw):
        direct = set().union(*(addresses(m["headers"].get(k, "")) for k in ("from", "to", "cc", "reply-to")))
        if email in direct:
            return True
        if m["form_submission"]:
            fields = re.findall(r"(?im)^\s*e-?mail(?: address|-adresse)?\s*[:：]\s*([^\s<>]+@[^\s<>]+)", m["text"])
            if email in {value.casefold() for value in fields}:
                return True
    return False


def render(rows, now, cutoff, uncertain, incomplete=False, batch_reference=None):
    cards = []
    operational = any("operational" in r for r in rows)
    groups = {}
    for index, row in enumerate(rows):
        d, latest = row["decision"], row["latest"]
        op = row.get("operational")
        date = datetime.fromtimestamp(latest["internal_ms"] / 1000, ZoneInfo("Europe/Vienna")).strftime("%d.%m.%Y %H:%M")
        previous = (f'<details><summary>Relevant previous context</summary><p>{escape(d["previous_context"])}</p></details>'
                    if d["previous_context"] and row.get("has_previous", True) else "")
        display_text = latest["latest_text"]
        if not display_text:
            display_text = "Attachments: " + ", ".join(a["filename"] or a["mime_type"] for a in latest["attachments"])
        if latest["form_submission"]:
            submission = re.search(
                r"(?is)(?:Ihre Anfrage|Nachricht|Message)\s*:\s*(.*?)(?=\n\s*(?:Name|E-Mail|Email|Telefonnummer)\s*:|$)",
                display_text)
            if submission:
                display_text = submission.group(1).strip()
        response = (f'<textarea id="reply-{index}" readonly aria-label="Suggested response">'
                    f'{escape(d["suggested_response"])}</textarea><button data-copy="reply-{index}">Copy response</button>'
                    '<span class="copy-status" role="status"></span>' if d["suggested_response"].strip() else
                    '<p class="unavailable">Suggested response currently unavailable</p>')
        controls = ""
        if op:
            from .operational_replies import STATES
            choices = ''.join(f'<option value="{s}">{s.replace("_", " ").title()}</option>' for s in STATES)
            matches = '; '.join(m['title'] + ' — ' + m['status'] + ' — ' + ', '.join(m['craftsmen']) for m in op['matches'])
            controls = (f'<p>Airtable: {escape(matches or "No confident match")}</p>'
                        f'<p class="muted">Captured {escape(op["airtable_captured_at"])} · read-only snapshot</p>'
                        f'<p>{escape(op["reason"])}</p>'
                        + ''.join(f'<p class="attention">Source conflict: {escape(c)}</p>' for c in op['conflicts']))
            if op['waiting_on']:
                controls += f'<p><b>Waiting on:</b> {escape(op["waiting_on"])}<br><b>Next internal action:</b> {escape(op["next_internal_action"])}</p>'
            if op.get('owner_evidence'):
                owner = op['owner_evidence']
                controls += f'<p>Giovanni ({escape(owner["recorded_at"])}): {escape(owner["note"])} — {"applied" if op["owner_applied"] else "previous evidence; recheck required"}</p>'
            controls += (f'<div data-feedback="{escape(row["thread_id"], quote=True)}">'
                         f'<label>Your correction <select aria-label="Operational state"><option value="">Leave unchanged</option>{choices}</select></label>'
                         '<label> Optional note <input aria-label="Operational note" maxlength="4000"></label></div>')
            if op['state'] != 'NEEDS_YOUR_REPLY':
                response = ""
        card = f'''<article><header><h2>{escape(d["customer"])}</h2><span>{escape(date)}</span></header>
<p class="subject">{escape(latest["headers"].get("subject", ""))}</p>
<p class="muted">{escape(d["customer_email"])}</p>
<h3>Latest customer message</h3><div class="message">{escape(display_text)}</div>
{previous}{controls}{'<h3>Suggested response</h3>' if response else ''}{response}</article>'''
        cards.append(card)
        if op:
            groups.setdefault(op['state'], []).append(card)
    if operational:
        from .operational_replies import STATES
        cards = []
        for state in STATES:
            section = f'<h2>{state.replace("_", " ")} · {len(groups.get(state, []))}</h2>' + ''.join(groups.get(state, []))
            if state in {'NO_ACTION_REQUIRED', 'HANDLED_ELSEWHERE', 'CLOSED_OR_LOST'}:
                label = 'CLOSED / LOST' if state == 'CLOSED_OR_LOST' else state.replace('_', ' ')
                section = f'<details><summary>{label} · {len(groups.get(state, []))}</summary>' + ''.join(groups.get(state, [])) + '</details>'
            cards.append(section)
    count = len(rows)
    status = f"{count} {'email' if count == 1 else 'emails'} waiting for your response"
    if not rows:
        status = ("No confirmed pending customer replies in the inspected threads; some threads need checking."
                  if incomplete or uncertain else "No customer emails currently appear to require a reply.")
    attention = ""
    if uncertain:
        attention = '<details class="attention"><summary>' + str(len(uncertain)) + ' conversations need checking</summary>'
        attention += "".join("<p>" + escape(x["subject"]) + " — " + escape(x["reason"]) + "</p>" for x in uncertain)
        attention += "</details>"
    script = """document.querySelectorAll('[data-copy]').forEach(button=>button.addEventListener('click',async()=>{
const text=document.getElementById(button.dataset.copy);let status=button.nextElementSibling;
try{await navigator.clipboard.writeText(text.value);status.textContent='Copied';}
catch(e){text.focus();text.select();status.textContent='Selected — press Ctrl+C';}}));
document.querySelectorAll('textarea').forEach(t=>{t.style.height='auto';t.style.height=(t.scrollHeight+4)+'px';});"""
    feedback_controls = ""
    if operational:
        status = f'{len(groups.get("NEEDS_YOUR_REPLY", []))} need your reply · {len(rows)} Gmail candidates reconciled'
        feedback_controls = ('<p>Corrections stay local. Export selected corrections, then import the downloaded file with '
            '<code>.\\.venv\\Scripts\\python.exe -m elektro_vienna shadow-feedback --file "$env:USERPROFILE\\Downloads\\operational-feedback.json"</code>. '
            'They apply on the next check; changed evidence requires review again.</p>'
            '<button id="export-feedback">Export corrections</button><span id="feedback-status" role="status"></span>')
        script += """
document.getElementById('export-feedback').addEventListener('click',()=>{
const items=Array.from(document.querySelectorAll('[data-feedback]')).filter(e=>e.querySelector('select').value).map(e=>({thread_id:e.dataset.feedback,state:e.querySelector('select').value,note:e.querySelector('input').value}));
if(!items.length){document.getElementById('feedback-status').textContent='Choose a correction first.';return;}
const data={kind:'operational_feedback',source:'Giovanni',recorded_at:new Date().toISOString(),batch_reference:BATCH_REFERENCE,items};
const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
const a=document.createElement('a');a.href=url;a.download='operational-feedback.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
document.getElementById('feedback-status').textContent='Exported. Import the file with the command above to preserve your decisions.';});
""".replace('BATCH_REFERENCE', json.dumps(batch_reference))
    import hashlib
    csp = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{csp}'; base-uri 'none'; form-action 'none'">
<title>Pending Customer Replies · Elektro Vienna</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f4;color:#192d2b;font:16px/1.55 system-ui,sans-serif}}
main{{max-width:900px;margin:48px auto;padding:0 24px 64px}}h1{{font-size:30px;margin-bottom:6px}}
.intro{{font-size:20px;margin-top:0}}.muted,header span{{color:#596b68;font-size:14px}}
article{{background:white;border:1px solid #dbe3df;border-radius:12px;padding:28px;margin:24px 0}}
header{{display:flex;justify-content:space-between;align-items:baseline;gap:20px}}h2{{font-size:22px;margin:0}}
.subject{{font-size:18px;font-weight:600}}h3,summary{{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#516662}}
.message{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f8f7;padding:18px;border-radius:8px}}
textarea{{display:block;width:100%;min-height:220px;resize:vertical;border:1px solid #c9d6d0;border-radius:8px;padding:18px;
font:16px/1.6 system-ui;color:#192d2b;background:#fff;overflow:hidden}}
button{{border:0;border-radius:7px;background:#165e50;color:white;padding:11px 18px;margin-top:14px;font:600 15px system-ui;cursor:pointer}}
.copy-status{{margin-left:12px;font-size:13px}}details{{margin:18px 0}}summary{{cursor:pointer}}.attention{{color:#725418}}
@media(max-width:600px){{main{{margin:24px auto;padding:0 12px}}article{{padding:18px}}header{{display:block}}}}
</style><main><h1>Pending Customer Replies</h1><p class="intro">{escape(status)}</p>
<p class="muted">Checked {escape(now.astimezone(ZoneInfo("Europe/Vienna")).strftime("%d.%m.%Y %H:%M"))} ·
Conversations active since {escape(cutoff.strftime("%d.%m.%Y"))}, plus previously pending replies.<br>
Suggestions for your review. Copying does not send an email.</p>
{feedback_controls}{''.join(cards)}{attention}</main><script>{script}</script></html>'''


def timeless_guidance(guidance):
    return {k: v for k, v in guidance.items() if k != "today_vienna"}


def needs_date_refresh(record, today):
    if record["guidance"].get("today_vienna") == today or record["decision"]["state"] != "pending":
        return False
    # Only time-sensitive wording needs fresh generation when unchanged mail crosses midnight.
    # The original generation date remains in the immutable decision record.
    return bool(re.search(
        r"(?i)\b(?:heute|morgen|gestern|näch\w*|kommend\w*|diese[rm]?\s+(?:Woche|Monat)|"
        r"Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|today|tomorrow|yesterday|"
        r"next|this\s+(?:week|month)|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
        r"Jänner|Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|"
        r"January|February|March|May|June|July|October|December)\b|"
        r"\b\d{1,2}[./]\d{1,2}|\b\d{1,2}:\d{2}|\b\d{4}-\d{2}-\d{2}",
        record["decision"]["suggested_response"]))


def queue_decision(messages, hint=None):
    """Determine reply state from message facts; a suggestion is never required."""
    state, last = gate(messages)
    if state != "candidate":
        return state, None, None
    incoming = [m for m in messages if m["direction"] == "incoming" and not m["automatic"]]
    if not incoming:
        return "non_customer", None, None
    forms = [m for m in incoming if m["form_submission"]]
    first = forms[0] if forms else incoming[0]
    email = (hint or {}).get("customer_email", "").casefold()
    if not email or not any(email in incoming_addresses(m) for m in incoming):
        choices = incoming_addresses(first)
        if len(choices) != 1:
            return "uncertain", last, None
        email = next(iter(choices))
    turns = [m for m in incoming if email in incoming_addresses(m)]
    latest = turns[-1]
    later = messages[messages.index(latest) + 1:]
    if any(m["direction"] == "outgoing" and not m["automatic"] and email in recipients(m)
           and (m["latest_text"].strip() or m["attachments"]) for m in later):
        return "answered", latest, None
    state, _ = gate([latest])
    if state != "candidate":
        return state, latest, None
    text = latest["latest_text"]
    subject = latest["headers"].get("subject", "")
    if not latest["form_submission"] and (
            re.search(r"(?i)^(?:no[._-]?reply|do[._-]?not[._-]?reply|mailer-daemon|postmaster|notifications?)@", email)
            or re.search(r"(?i)newsletter|delivery (?:status|failure)|undeliverable|zustellung.*(?:fehl|verzögert)", subject)
            or re.search(r"(?i)unsubscribe|abmelden|werbung|personalvermittlung|passende kandidaten|"
                         r"suchmaschinenoptimierung|seo[- ]|zertifizierungsstelle", text)):
        return "non_customer", latest, None
    known = bool(hint and hint.get("state") == "pending")
    request = re.search(r"(?i)\?|\b(?:bitte|anfrage|kost\w*|montage|install\w*|anschluss|anschließen|"
                        r"steckdose|lampe|wallbox|elektriker|reparatur|rechnung|termin|angebot|"
                        r"please|could you|can you|quote|price|appointment)\b", subject + "\n" + text)
    if not (known or forms or request):
        return "uncertain", latest, None
    name = (hint or {}).get("customer", "")
    if not name and latest["form_submission"]:
        match = re.search(r"(?im)^\s*Name\s*[:：]\s*([^\r\n]+)", latest["text"])
        name = match.group(1).strip() if match else ""
    if not name:
        name = next((n for n, a in getaddresses([latest["headers"].get("from", "")])
                     if a.casefold() == email and n), email)
    context = "\n".join(m["latest_text"][:350] for m in messages[max(0, messages.index(latest)-2):messages.index(latest)])
    decision = {"thread_id": "", "latest_customer_id": latest["id"], "state": "pending",
                "customer": name, "customer_email": email, "reason": "Unanswered customer turn in current Gmail chronology.",
                "previous_context": context, "suggested_response": "", "internal_checks": []}
    return "pending", latest, decision


def related_state(packet, raw_threads):
    email = packet["queue_decision"]["customer_email"]
    related = [{"thread_id": tid, "fingerprint": fingerprint(raw), "messages": conversation(raw)}
               for tid, raw in raw_threads.items() if tid != packet["thread_id"] and has_correspondent(raw, email)]
    return sorted(related, key=lambda r: r["thread_id"])


def explicitly_continued(packet, related):
    """Only exact reply references or quoted inquiry text prove cross-thread continuation."""
    d = packet["queue_decision"]
    latest = next(m for m in packet["messages"] if m["id"] == d["latest_customer_id"])
    text = latest["latest_text"]
    if latest["form_submission"]:
        match = re.search(r"(?is)(?:Ihre Anfrage|Nachricht|Message)\s*:\s*(.*?)(?=\n\s*(?:Name|E-Mail|Email|Telefonnummer)\s*:|$)", text)
        text = match.group(1) if match else text
    normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
    needle = normalize(text)
    message_id = latest["headers"].get("message-id", "")
    for thread in related:
        linked = False
        for m in thread["messages"]:
            if m["internal_ms"] <= latest["internal_ms"] or m["automatic"]:
                continue
            direct = (d["customer_email"] in recipients(m) if m["direction"] == "outgoing"
                      else d["customer_email"] in incoming_addresses(m))
            references = m["headers"].get("references", "") + m["headers"].get("in-reply-to", "")
            linked = linked or bool(direct and ((message_id and message_id in references)
                                   or (len(needle) >= 25 and needle in normalize(m["text"]))))
            if linked and direct:
                return thread["thread_id"]
    return None


def reconcile(reader, store, state_path, contexts, *, now=None, suggest=generate, feedback=None,
              operational=None, current_candidates_only=False):
    now = now or datetime.now(timezone.utc)
    require(reader.mailbox() == MAILBOX, "mailbox_identity_mismatch")
    cutoff = now.astimezone(ZoneInfo("Europe/Vienna")).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=DAYS)
    old = json.loads(state_path.read_bytes()) if state_path.exists() else {"tracked": [], "cache": {}}
    ids = list(dict.fromkeys(([] if current_candidates_only else recent_ids(reader, int(cutoff.timestamp()*1000)))
                            + old["tracked"] + old.get("related_tracked", [])))
    guidance = {**contexts, "owner_feedback": feedback or [], "today_vienna": now.astimezone(ZoneInfo("Europe/Vienna")).date().isoformat()}
    guidance_hash = digest(canonical([timeless_guidance(guidance), VERSION, INSTRUCTIONS]))
    cache, indexed, raw_threads, audit, uncertain, work = dict(old["cache"]), {}, {}, [], [], []
    refs = list(dict.fromkeys(cache.values()))
    if not old.get("latest_batch"):
        refs += [p.relative_to(store.root).as_posix() for p in (store.root/"30_cases/live_shadow").glob("*/card.json")]
    for ref in dict.fromkeys(refs):
        record = store.read(ref)
        if (record.get("kind") != "pending_reply_decision" or record.get("version") != VERSION
                or record.get("generator_prompt") != INSTRUCTIONS
                or timeless_guidance(record.get("guidance", {})) != timeless_guidance(guidance)):
            continue
        validate_decision(record["decision"], record["packet"])
        source = store.read(record["packet"]["source_reference"])["gmail_thread"]
        fp = fingerprint(source)
        rank = (record["guidance"].get("today_vienna", ""), bool(record["packet"].get("related_recent_threads")))
        if fp not in indexed or rank >= indexed[fp][0]:
            indexed[fp] = (rank, ref, record)
    for tid in ids:
        try:
            raw_threads[tid] = reader.get_thread(tid)
        except ProviderError as exc:
            if exc.code != "gmail_http_404":
                raise
            uncertain.append({"thread_id": tid, "subject": tid, "reason": "Thread currently unavailable from Gmail."})
    # Complete deterministic queue reconciliation before any attachment or model work.
    for tid, raw in raw_threads.items():
        source = store.document("input", {"kind": "pending_thread_source", "mailbox": MAILBOX, "gmail_thread": raw})
        cached = indexed.get(fingerprint(raw))
        record = cached[2] if cached else None
        dependencies_valid = bool(record) and all(r["thread_id"] in raw_threads and
            fingerprint(raw_threads[r["thread_id"]]) == r["fingerprint"]
            for r in record["packet"].get("related_recent_threads", []))
        messages = conversation(raw)
        hint = record["decision"] if record else None
        if record:
            decoded = {m["id"]: m for m in record["packet"]["messages"]}
            for m in messages:
                if m["id"] in decoded:
                    prior = decoded[m["id"]]
                    m.update(text=prior["text"], latest_text=prior["latest_text"],
                             headers={**m["headers"], **prior["headers"]})
        if record and dependencies_valid and hint["state"] in {"non_customer", "no_reply_needed", "uncertain"}:
            audit.append({"thread_id": tid, "state": hint["state"], "decision_reference": cached[1], "source_reference": source})
            if hint["state"] == "uncertain":
                uncertain.append({"thread_id": tid, "subject": messages[-1]["headers"].get("subject", tid),
                                  "reason": hint["reason"]})
            continue
        state, latest, decision = queue_decision(messages, hint)
        if state != "pending":
            audit.append({"thread_id": tid, "state": state, "source_reference": source})
            if state == "uncertain":
                uncertain.append({"thread_id": tid, "subject": latest["headers"].get("subject", tid) if latest else tid,
                                  "reason": "Customer identity or response need could not be established from message facts."})
            continue
        decision["thread_id"] = tid
        packet = {"thread_id": tid, "messages": messages, "source_reference": source,
                  "sources": [], "images": [], "documents": [], "queue_decision": decision}
        related = related_state(packet, raw_threads)
        continued = explicitly_continued(packet, related)
        if continued:
            audit.append({"thread_id": tid, "state": "continued_elsewhere", "source_reference": source,
                          "related_thread_id": continued})
            continue
        for r in related:
            r["source_reference"] = store.document("input", {"kind": "pending_thread_source", "gmail_thread": raw_threads[r["thread_id"]]})
        packet["related_recent_threads"] = related
        packet["related_context_key"] = digest(canonical([(r["thread_id"], r["fingerprint"]) for r in related]))
        cached_related = [(r["thread_id"], r["fingerprint"]) for r in record["packet"].get("related_recent_threads", [])] if record else []
        same_context = cached_related == [(r["thread_id"], r["fingerprint"]) for r in related]
        reusable = bool(record and dependencies_valid and same_context and hint["state"] == "pending"
                        and hint["latest_customer_id"] == latest["id"] and not needs_date_refresh(record, guidance["today_vienna"]))
        if reusable:
            from .models import ArchivedFile
            for saved in record["packet"]["sources"]:
                SourceArchive(store.root).verify(ArchivedFile(**saved))
            decision = hint
            packet["messages"] = record["packet"]["messages"]
        work.append({"thread_id": tid, "packet": packet, "decision": decision,
                     "decision_reference": cached[1] if reusable else None,
                     "suggestion_status": "cached" if reusable else "unavailable"})
    work.sort(key=lambda r: max(m["internal_ms"] for m in r["packet"]["messages"]), reverse=True)
    if operational:
        for row in work:
            row['operational'] = operational.classify(row['packet'])
    missing = [r for r in work if r["suggestion_status"] != "cached"
               and (not operational or r['operational']['state'] == 'NEEDS_YOUR_REPLY')]
    metrics = {"threads_requiring_new_suggestion": len(missing), "suggestion_calls": 0,
               "suggestion_threads_attempted": 0, "suggestion_failures": []}
    runtime_error = None
    def persist():
        save_checkpoint(state_path, {"tracked": list(dict.fromkeys(old["tracked"] + [r["thread_id"] for r in work])),
            "related_tracked": sorted({r["thread_id"] for row in work for r in row["packet"].get("related_recent_threads", [])}),
            "cache": cache, "latest_batch": old.get("latest_batch")})
    for offset in range(0, len(missing), 4):
        chunk = missing[offset:offset+4]
        if runtime_error:
            for row in chunk:
                row["suggestion_error"] = runtime_error
            continue
        try:
            for row in chunk:
                packet = row["packet"]
                enriched = evidence(reader, store, raw_threads[row["thread_id"]], packet["messages"])
                packet.update(enriched)
            packets = [r["packet"] for r in chunk]
            metrics["suggestion_calls"] += 1
            metrics["suggestion_threads_attempted"] += len(chunk)
            decisions = suggest(packets, {**guidance, "related_recent_thread_rule": RELATED_RULE})
            inference = store.document("input", {"kind": "pending_inference_output", "decisions": decisions,
                "thread_sources": [p["source_reference"] for p in packets], "guidance_hash": guidance_hash,
                "generator_prompt": INSTRUCTIONS, "related_recent_thread_rule": RELATED_RULE,
                "related_sources": [r["source_reference"] for p in packets for r in p.get("related_recent_threads", [])]})
            require(isinstance(decisions, list) and len(decisions) == len(chunk)
                    and len({d.get("thread_id") for d in decisions}) == len(chunk), "pending_decision_coverage")
            by_id = {d["thread_id"]: d for d in decisions}
            for row in chunk:
                packet = row["packet"]
                require(row["thread_id"] in by_id, "pending_decision_missing")
                d = resolve_identity(by_id[row["thread_id"]], packet)
                validate_decision(d, packet)
                ref = store.document("card", {"kind": "pending_reply_decision", "packet": packet,
                    "decision": d, "guidance": guidance, "guidance_hash": guidance_hash,
                    "generator_prompt": INSTRUCTIONS, "inference_reference": inference,
                    "version": VERSION, "external_actions": []})
                cache[digest(canonical([fingerprint(raw_threads[row["thread_id"]]), guidance_hash]))] = ref
                row.update(decision=d, decision_reference=ref, suggestion_status="generated")
                persist()
        except (ProviderError, ValueError, OSError) as exc:
            runtime_error = exc.code if isinstance(exc, ProviderError) else "suggestion_validation_or_evidence_unavailable"
            metrics["suggestion_failures"].append(runtime_error)
            for row in chunk:
                if row["suggestion_status"] != "generated":
                    row["suggestion_error"] = runtime_error
            persist()
    rows = []
    for row in work:
        tid, packet, d = row["thread_id"], row["packet"], row["decision"]
        audit.append({"thread_id": tid, "state": d["state"], "source_reference": packet["source_reference"],
                      "decision_reference": row["decision_reference"]})
        if d["state"] != "pending":
            if d["state"] == "uncertain":
                uncertain.append({"thread_id": tid, "subject": packet["messages"][-1]["headers"].get("subject", tid), "reason": d["reason"]})
            continue
        current = reader.get_thread(tid)
        if fingerprint(current) != fingerprint(raw_threads[tid]):
            messages = conversation(current)
            state, latest, fresh = queue_decision(messages, d)
            source = store.document("input", {"kind": "pending_thread_source", "gmail_thread": current})
            audit.append({"thread_id": tid, "state": state, "during_generation": True, "source_reference": source})
            if state != "pending":
                if state == "uncertain":
                    uncertain.append({"thread_id": tid, "subject": messages[-1]["headers"].get("subject", tid), "reason": "Conversation changed; response need is uncertain."})
                continue
            fresh["thread_id"] = tid
            d = fresh
            packet.update(messages=messages, source_reference=source)
            packet['queue_decision'] = fresh
            if operational:
                row['operational'] = operational.classify(packet)
            row.update(decision_reference=None, suggestion_status="unavailable", suggestion_error="conversation_changed")
        if any(fingerprint(reader.get_thread(r["thread_id"])) != r["fingerprint"] for r in packet.get("related_recent_threads", [])):
            uncertain.append({"thread_id": tid, "subject": packet["messages"][-1]["headers"].get("subject", tid),
                              "reason": "Related correspondence changed; response need must be rechecked."})
            continue
        latest = next(m for m in packet["messages"] if m["id"] == d["latest_customer_id"])
        if operational and row['operational']['state'] != 'NEEDS_YOUR_REPLY':
            d = {**d, 'suggested_response': ''}
            row['suggestion_status'] = 'suppressed'
        rows.append({"thread_id": tid, "latest": latest, "decision": d, "source_reference": packet["source_reference"],
            "has_previous": len(packet["messages"]) > 1, "decision_reference": row["decision_reference"],
            "suggestion_status": row["suggestion_status"], "suggestion_error": row.get("suggestion_error"),
            **({'operational': row['operational']} if operational else {})})
    rows.sort(key=lambda r: r["latest"]["internal_ms"], reverse=True)
    metrics.update(suggestions_reused=sum(r["suggestion_status"] == "cached" for r in rows),
                   suggestions_generated=sum(r["suggestion_status"] == "generated" for r in rows),
                   suggestions_unavailable=sum(r["suggestion_status"] == "unavailable" for r in rows))
    batch = {"kind": "pending_replies_batch", "checked_at": now.isoformat(), "cutoff": cutoff.isoformat(),
             "rows": rows, "uncertain": uncertain, "audit": audit, "feedback": feedback or [],
             "external_actions": [], "history_checkpoint_used": False, "suggestions": metrics}
    ref = store.document("batch", batch)
    page = render(rows, now, cutoff, uncertain, batch_reference=ref)
    store.publish(str(Path(ref).with_name("review.html")).replace("\\", "/"), page.encode("utf-8"))
    save_checkpoint(state_path, {"tracked": [r["thread_id"] for r in rows] + [r["thread_id"] for r in uncertain],
        "related_tracked": sorted({r["thread_id"] for row in work for r in row["packet"].get("related_recent_threads", [])}
            | {r["thread_id"] for _, _, rec in indexed.values() if rec["decision"]["thread_id"] in {u["thread_id"] for u in uncertain}
               for r in rec["packet"].get("related_recent_threads", [])}),
        "cache": cache, "latest_batch": ref})
    path = atomic_view(store, page)
    operational_counts = {}
    if operational:
        from .operational_replies import STATES
        operational.remember(rows)
        operational_counts = {s: sum(r['operational']['state'] == s for r in rows) for s in STATES}
    return {"pending_customer_threads": len(rows), "uncertain_threads": len(uncertain), "threads_checked": len(ids),
            "window_days": DAYS, "review_path": path, "batch_reference": ref, **metrics,
            **({'operational_counts': operational_counts} if operational else {}),
            "external_actions": []}
