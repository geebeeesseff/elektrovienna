"""Offline first-response calibration; evidence only, never policy promotion."""

import json
import re
import base64
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from hashlib import sha256
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive_store import SourceArchive, source_reference
from .blind_eval import load, require
from .pilot_sources import archived_inputs, canonical, digest, read_verified
from .pilot_parse import PARSER_VERSION, parse_email
from .policy_experiment import ExperimentStore, frozen_at, ref


class CalibrationStore(SourceArchive):
    def path(self, reference):
        require(bool(re.fullmatch(
            r'90_manual_review/response_calibration/[0-9a-f]{64}/(?:(?:ab-original|ab-result|owner-feedback|calibration-original|calibration-result)\.json|owner-request\.txt)|'
            r'30_cases/response_calibration/[0-9a-f]{64}/(?:set\.json|calibration\.html)', reference)),
            'calibration_path_invalid')
        target = self.root.joinpath(*reference.split('/'))
        self._guard(target)
        return target


def ingest_ab(store, raw, owner_feedback):
    """Preserve exact feedback bytes before reading/revealing the random mapping."""
    review = json.loads(raw)
    require(review['kind'] == 'blinded_response_preference', 'review_kind')
    packet, frozen = frozen_at(ExperimentStore(store.root), review['comparison'])
    ids = {i['case_id'] for i in packet['inquiries']}
    require(len(review['cases']) == len(ids) and {c['case_id'] for c in review['cases']} == ids,
            'review_cases')
    require(datetime.fromisoformat(review['recorded_at'].replace('Z', '+00:00')).utcoffset() is not None,
            'review_timestamp')
    for case in review['cases']:
        require(case['preference'] in {'A', 'B', 'equal'} and isinstance(case['comment'], str),
                'review_choice')
    require(owner_feedback['kind'] == 'owner_feedback' and owner_feedback['owner'] == 'Giovanni Superti-Furga'
            and owner_feedback['verbatim'] and owner_feedback['effect'] == 'evidence_only; policy_unchanged',
            'owner_feedback_invalid')
    raw_id = digest(raw)
    raw_ref = f'90_manual_review/response_calibration/{raw_id}/ab-original.json'
    store.publish(raw_ref, raw)
    owner_id = digest(canonical(owner_feedback))
    owner_ref = f'90_manual_review/response_calibration/{owner_id}/owner-feedback.json'
    store.publish(owner_ref, canonical(owner_feedback))
    # Exact original export and current owner instructions are now durable.
    mapping = load(ExperimentStore(store.root).path(ref(review['comparison'], 'mapping.json')))
    require(mapping['frozen_sha256'] == digest(canonical(frozen)), 'mapping_freeze_binding')
    require(set(mapping['cases']) == ids, 'mapping_cases')
    results = []
    for inquiry in packet['inquiries']:
        cid = inquiry['case_id']
        assignment = mapping['cases'][cid]
        require(set(assignment) == {'A', 'B'} and set(assignment.values()) == {'P', 'PH'}, 'mapping_invalid')
        feedback = next(c for c in review['cases'] if c['case_id'] == cid)
        results.append({'case_id': cid, 'name': inquiry['name'],
                        'policy_only': next(k for k, v in assignment.items() if v == 'P'),
                        'policy_history': next(k for k, v in assignment.items() if v == 'PH'),
                        'preference': feedback['preference'],
                        'preferred_variant': assignment.get(feedback['preference'], 'equal'),
                        'comment_verbatim': feedback['comment']})
    record = {'kind': 'attributed_blinded_review_result', 'reviewer': 'Giovanni Superti-Furga',
              'attribution_basis': 'Owner explicitly identified the exported review in the calibration request.',
              'comparison': review['comparison'], 'recorded_at': review['recorded_at'],
              'original_reference': raw_ref, 'original_sha256': raw_id,
              'owner_feedback_reference': owner_ref, 'owner_feedback_sha256': owner_id,
              'frozen_sha256': digest(canonical(frozen)), 'mapping_sha256': digest(canonical(mapping)),
              'results': results, 'effect': 'evidence_only; policy_unchanged'}
    result_id = digest(canonical(record))
    result_ref = f'90_manual_review/response_calibration/{result_id}/ab-result.json'
    store.publish(result_ref, canonical(record))
    return result_ref, record


def ingest_calibration(store, raw, owner_request):
    """Preserve a complete human export and the separate follow-up instruction."""
    review = json.loads(raw)
    require(review['kind'] == 'first_response_calibration_review' and review['format_version'] == 1,
            'calibration_review_kind')
    require(review['reviewer'] == 'Giovanni Superti-Furga' and review['complete'] is True,
            'calibration_review_incomplete')
    require(datetime.fromisoformat(review['recorded_at'].replace('Z', '+00:00')).utcoffset() is not None,
            'calibration_review_timestamp')
    set_ref = f'30_cases/response_calibration/{review["set"]}/set.json'
    set_raw = store.path(set_ref).read_bytes()
    require(digest(set_raw) == review['set'], 'calibration_set_integrity')
    source_set = json.loads(set_raw)
    ids = {e['id'] for e in source_set['examples']}
    require(len(review['cases']) == len(ids) and {c['example_id'] for c in review['cases']} == ids,
            'calibration_review_cases')
    for case in review['cases']:
        require(case['choice'] in {'same', 'different'} and isinstance(case['comment'], str),
                'calibration_review_choice')
    require(isinstance(owner_request, bytes) and bool(owner_request.strip()), 'owner_request_missing')
    raw_ref = f'90_manual_review/response_calibration/{digest(raw)}/calibration-original.json'
    request_ref = f'90_manual_review/response_calibration/{digest(owner_request)}/owner-request.txt'
    store.publish(raw_ref, raw)
    store.publish(request_ref, owner_request)
    result = {'kind': 'completed_first_response_calibration', 'format_version': 1,
              'reviewer': review['reviewer'], 'recorded_at': review['recorded_at'],
              'original_reference': raw_ref, 'original_sha256': digest(raw),
              'owner_request_reference': request_ref, 'owner_request_sha256': digest(owner_request),
              'set_reference': set_ref, 'set_sha256': digest(set_raw),
              'ab_result_reference': source_set['ab_result_reference'],
              'cases': review['cases'],
              'counts': {choice: sum(c['choice'] == choice for c in review['cases'])
                         for choice in ('same', 'different')},
              'effect': 'Human feedback preserved; price acceptance is not inferred from same-style choices.'}
    result_ref = f'90_manual_review/response_calibration/{digest(canonical(result))}/calibration-result.json'
    store.publish(result_ref, canonical(result))
    return result_ref, result


def scan_archive(state, archive):
    """Local lexical inspection only. No extraction publication or provider access."""
    before = digest(state.read_bytes())
    messages, files = archived_inputs(state, 'office@elektrovienna.at')
    sources = {f['message_id']: f for f in files if f['kind'] == 'message'}
    rows = []
    for message in messages:
        require(message['message_id'] in sources, 'archive_message_missing')
        source = sources[message['message_id']]
        parsed = parse_email(read_verified(archive, source))
        rows.append({**message, 'labels': json.loads(message['labels']), 'source': source,
                     'headers': {k.lower(): v for k, v in parsed['headers']}})
    require(digest(state.read_bytes()) == before, 'inventory_changed_during_scan')
    return {'state_sha256': before, 'messages': rows}


def addresses(headers, *names):
    return {a.casefold() for _, a in getaddresses([headers[n] for n in names if headers.get(n)]) if a}


def primary_text(text):
    """Display projection only; originals/parser outputs are never rewritten."""
    text = text.replace('\r\r\n', '\n').replace('\r\n', '\n').replace('\r', '\n')
    quoted = re.search(r'(?mi)^(?:_{5,}|From:|Von:|On .{0,200}wrote:|Am .{0,200}schrieb|>{1,})', text)
    end = quoted.start() if quoted else len(text)
    return text[:end], end


def message_view(archive, row):
    source = row['source']
    require(source['reference'] == source_reference('office@elektrovienna.at', row['message_id']),
            'message_source_identity')
    raw = read_verified(archive, source)
    parsed = parse_email(raw)
    require(not parsed['issues'], 'message_parse_uncertain')
    bodies = [s for s in parsed['sections'] if s['locator'].startswith('mime:')]
    require(len(bodies) == 1 and bodies[0]['text'].strip(), 'message_body_ambiguous')
    headers = {k.lower(): v for k, v in parsed['headers']}
    require(headers == row['headers'], 'inventory_headers_changed')
    text, end = primary_text(bodies[0]['text'])
    date = parsedate_to_datetime(headers['date'])
    require(date.utcoffset() is not None, 'message_date_timezone')
    return {'message_id': row['message_id'], 'thread_id': row['thread_id'],
            'internal_ms': row['internal_ms'], 'source': source, 'headers': headers,
            'date_vienna': date.astimezone(ZoneInfo('Europe/Vienna')).strftime('%d.%m.%Y %H:%M %Z'),
            'text': text, 'parser_version': PARSER_VERSION, 'locator': bodies[0]['locator'],
            'projection': {'normalization': 'CRCRLF/CRLF/CR to LF', 'start': 0, 'end': end,
                           'rule': 'primary-text-v1; stop before quoted-message delimiter'},
            'attachments': [p.get_filename() or '(inline)' for p in BytesParser(policy=policy.default)
                            .parsebytes(raw).walk() if p.get_filename() or p.get_content_disposition() == 'attachment']}


def publish_set(store, archive, inventory, selections, ab_reference):
    """Publish a manually curated set, fail closed on attribution/boundary ambiguity."""
    require(0 < len(selections) <= 12, 'selection_size')
    ids = [s[k] for s in selections for k in ('inquiry', 'reply')]
    require(len(ids) == len(set(ids)), 'selection_duplicate')
    ab_raw = store.path(ab_reference).read_bytes()
    require(ab_reference == f'90_manual_review/response_calibration/{digest(ab_raw)}/ab-result.json',
            'feedback_hash_mismatch')
    ab = json.loads(ab_raw)
    require(ab['kind'] == 'attributed_blinded_review_result', 'feedback_kind')
    for kind in ('original', 'owner_feedback'):
        require(digest(store.path(ab[f'{kind}_reference']).read_bytes()) == ab[f'{kind}_sha256'],
                'feedback_source_integrity')
    rows = inventory['messages']
    by_id = {r['message_id']: r for r in rows}
    require(len(by_id) == len(rows), 'inventory_duplicate')
    examples = []
    for selected in selections:
        incoming, outgoing = (by_id[selected[k]] for k in ('inquiry', 'reply'))
        require('SENT' not in incoming['labels'] and 'SENT' in outgoing['labels'], 'direction_invalid')
        require(incoming['thread_id'] == outgoing['thread_id'] and
                incoming['internal_ms'] < outgoing['internal_ms'], 'pair_chronology')
        thread = [r for r in rows if r['thread_id'] == incoming['thread_id']]
        require(not any(r['internal_ms'] < outgoing['internal_ms'] and 'SENT' in r['labels']
                        for r in thread), 'not_first_outbound')
        require(not any(r['internal_ms'] < incoming['internal_ms'] for r in thread), 'not_first_inquiry')
        require(not any(incoming['internal_ms'] < r['internal_ms'] < outgoing['internal_ms']
                        for r in thread), 'intervening_message')
        inquiry, reply = (message_view(archive, r) for r in (incoming, outgoing))
        ih, rh = inquiry['headers'], reply['headers']
        require(bool(ih.get('message-id')) and rh.get('in-reply-to') == ih['message-id'], 'not_direct_reply')
        customer = addresses(ih, 'from')
        require(len(customer) == 1 and 'office@elektrovienna.at' not in customer and
                customer & addresses(rh, 'to') and
                addresses(rh, 'from') == {'office@elektrovienna.at'}, 'not_customer_reply')
        require(not any(r['internal_ms'] < incoming['internal_ms'] and customer &
                        addresses(r['headers'], 'from', 'to', 'cc') for r in rows), 'earlier_customer_contact')
        # Conservative admission: full-name signature in the unquoted primary message,
        # no other named operator / representation signature. Source inspection still required.
        require(bool(re.search(r'(?m)^Giovanni Superti-Furga\s*$', reply['text'])) and
                not re.search(r'Josef|Schenk|Sascha|Stanojevic|i\.V\.', reply['text'], re.I),
                'authorship_uncertain')
        require(selected['selection_reason'].strip(), 'selection_reason_missing')
        examples.append({'id': incoming['message_id'], 'title': selected['title'],
                         'selection_reason': selected['selection_reason'],
                         'inquiry': inquiry, 'reply': reply,
                         'author': 'Giovanni Superti-Furga',
                         'attribution_basis': 'Full-name signature in primary unquoted reply; shared office mailbox.',
                         'boundary_basis': 'Direct RFC reply; first archived thread outbound; no earlier exact-address contact.'})
    record = {'kind': 'first_response_calibration_set', 'format_version': 1,
              'effect': 'historical_style_process_evidence_only; policy_unchanged',
              'ab_result_reference': ab_reference, 'ab_result_sha256': digest(ab_raw),
              'inventory_sha256': digest(canonical(inventory)), 'state_sha256': inventory['state_sha256'],
              'processor_sha256': digest(Path(__file__).read_bytes()),
              'script_sha256': digest(Path(__file__).with_name('response_calibration.js').read_bytes()),
              'inventory_message_count': len(rows), 'examples': examples,
              'selection_method': 'Agent-curated diverse first replies; no later case outcomes used; human calibration pending.',
              'limitations': ['Nonrandom sample, not a performance evaluation.',
                             'Signature-based attribution, not proof of keyboard operator.',
                             'Archive coverage only; calls, aliases and unarchived correspondence cannot be excluded.',
                             'Attachments listed but not rendered or technically interpreted.',
                             'No historical prices or technical statements become current policy.']}
    run = digest(canonical(record))
    html = render_calibration(record, run)
    store.publish(f'30_cases/response_calibration/{run}/calibration.html', html.encode('utf-8'))
    store.publish(f'30_cases/response_calibration/{run}/set.json', canonical(record))
    return run, record


def render_calibration(record, run):
    script = Path(__file__).with_name('response_calibration.js').read_text('utf-8')
    script_hash = base64.b64encode(sha256(script.encode('utf-8')).digest()).decode()
    cards = []
    for index, example in enumerate(record['examples'], 1):
        parts = []
        for kind, heading in (('inquiry', 'KUNDENANFRAGE'), ('reply', 'TATSÄCHLICHE ERSTE ANTWORT')):
            message = example[kind]
            attachments = ', '.join(message['attachments']) or 'keine benannten Anhänge'
            parts.append(f'<section><h3>{heading}</h3><p class="meta">{escape(message["date_vienna"])}</p>'
                         f'<pre>{escape(message["text"])}</pre><p class="meta">Anhänge (nicht dargestellt): '
                         f'{escape(attachments)}</p></section>')
        cards.append(f'<article data-id="{escape(example["id"], quote=True)}">'
                     f'<h2>{index}. {escape(example["title"])}</h2>'
                     '<p class="meta">Signiert: Giovanni Superti-Furga · persönlicher Namenszug in der '
                     'neu geschriebenen Antwort aus dem gemeinsamen Office-Postfach.</p>'
                     f'<p class="meta">Betreff: {escape(example["inquiry"]["headers"].get("subject", ""))}</p>'
                     f'<div class="pair">{"".join(parts)}</div>'
                     f'<fieldset><legend>Wie würdest du heute antworten?</legend>'
                     f'<label><input type="radio" name="choice-{index}" value="same"> '
                     'Würde ich heute ungefähr so machen</label>'
                     f'<label><input type="radio" name="choice-{index}" value="different"> '
                     'Würde ich heute anders machen</label>'
                     f'<label for="comment-{index}">Optionaler kurzer Kommentar</label>'
                     f'<textarea id="comment-{index}" rows="2"></textarea></fieldset></article>')
    return f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Giovanni · Erste Antworten</title>
<style>body{{font:17px/1.55 system-ui,sans-serif;background:#f5f4ef;color:#202b32;margin:auto;padding:24px;max-width:1300px}}
h1,h2,h3{{line-height:1.2}}h3{{font-size:14px;letter-spacing:.05em}}article{{background:white;border:1px solid #d5dbd8;border-radius:12px;padding:24px;margin:26px 0}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}section{{min-width:0}}pre{{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}}.meta{{font-size:14px;color:#53656b}}
fieldset{{border:0;border-top:1px solid #ddd;padding:16px 0 0;margin-top:18px}}label{{display:block;margin:10px 0}}textarea{{box-sizing:border-box;width:100%;font:inherit;padding:10px;border:1px solid #889b99;border-radius:6px}}
button{{background:#175a50;color:white;border:0;border-radius:8px;font:inherit;padding:12px 20px;cursor:pointer}}input{{accent-color:#175a50}}
footer{{position:sticky;bottom:0;background:#f5f4ef;padding:14px 0;border-top:1px solid #ccc}}@media(max-width:850px){{.pair{{grid-template-columns:1fr}}body{{padding:12px}}article{{padding:16px}}}}</style></head>
<body data-set="{run}"><h1>Wie würdest du heute zuerst antworten?</h1>
<p>{len(record['examples'])} echte Kundenanfragen und deine damaligen ersten Antworten. Wähle pro Beispiel eine Antwort;
ein kurzer Kommentar ist freiwillig. Du musst keine E-Mail neu schreiben.</p>
<p class="meta">Historische E-Mails sind Belege für damaligen Stil und Ablauf, keine aktuelle Vorgabe.
Preise und technische Aussagen werden unverändert gezeigt. Zitate früherer Nachrichten sind ausgeblendet;
die damalige Signatur bleibt sichtbar. Späterer Fallverlauf ist nicht enthalten.
Fotos und Dokumente werden nur als Anhänge benannt, nicht dargestellt.</p>
<p><strong>Zum Sichern unten „Bewertung herunterladen“ wählen.</strong> Auch ein Zwischenstand ist möglich.
Die Seite speichert nicht automatisch und überträgt nichts.</p>
{''.join(cards)}
<footer><span id="progress" aria-live="polite"></span> <button id="download" type="button">Bewertung herunterladen</button>
<span id="saved" role="status"></span></footer><script>{script}</script></body></html>'''
