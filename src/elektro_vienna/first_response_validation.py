"""Three offline T0 drafts, frozen for human review; no retrospective or provider API."""

import base64
import json
import re
from collections import Counter
from datetime import date
from email import policy
from email.parser import BytesParser
from html import escape
from pathlib import Path

from .archive_store import SourceArchive
from .blind_eval import require
from .operating_policy import load_policy
from .pilot_parse import parse_email
from .pilot_sources import archived_inputs, canonical, digest, read_verified
from .response_calibration import CalibrationStore, addresses, message_view


class ValidationStore(SourceArchive):
    def path(self, reference):
        require(bool(re.fullmatch(r'30_cases/first_response_validation/[0-9a-f]{64}/'
                                  r'(?:packet.json|answers.json|review.html)', reference)), 'validation_path_invalid')
        target = self.root.joinpath(*reference.split('/'))
        self._guard(target)
        return target


def prepare(store, state, selections, feedback_reference, prior_blind_reference, excluded_threads, contexts):
    """Read selected original bytes only; other messages contribute metadata only."""
    require(len(selections) == len({s['message_id'] for s in selections}) == 3, 'three_inquiries_required')
    feedback_raw = CalibrationStore(store.root).path(feedback_reference).read_bytes()
    require(digest(feedback_raw) == feedback_reference.split('/')[-2], 'feedback_integrity')
    feedback = json.loads(feedback_raw)
    require(feedback['kind'] == 'completed_first_response_calibration', 'completed_calibration_required')
    prior_path = store.root / prior_blind_reference
    require(bool(re.fullmatch(r'30_cases/blind_eval/[0-9a-f]{64}/packet.json', prior_blind_reference)), 'prior_path')
    store._guard(prior_path)
    prior_raw = prior_path.read_bytes()
    require(digest(prior_raw) == prior_blind_reference.split('/')[-2], 'prior_packet_integrity')
    forbidden = {c['example_id'] for c in feedback['cases']} | {
        c['message_id'] for c in json.loads(prior_raw)['inquiries']}
    before = digest(state.read_bytes())
    messages, files = archived_inputs(state, 'office@elektrovienna.at')
    by_id = {m['message_id']: m for m in messages}
    forbidden_threads = set(excluded_threads) | {by_id[m]['thread_id'] for m in forbidden if m in by_id}
    archive = SourceArchive(store.root)
    inquiries = []
    for selected in selections:
        mid = selected['message_id']; meta = by_id[mid]
        require(mid not in forbidden and meta['thread_id'] not in forbidden_threads, 'previously_exposed_case')
        require('SENT' not in json.loads(meta['labels']), 'not_inbound')
        require(not meta['missing_attachment_parts'], 'initial_attachments_missing')
        require(not any(m['thread_id'] == meta['thread_id'] and
                        (m['internal_ms'], m['message_id']) < (meta['internal_ms'], mid)
                        for m in messages), 'not_first_thread_message')
        sources = [f for f in files if f['message_id'] == mid]
        originals = [f for f in sources if f['kind'] == 'message']
        require(len(originals) == 1, 'original_missing')
        raw = read_verified(archive, originals[0])
        headers = {k.lower(): v for k, v in parse_email(raw)['headers']}
        require(not headers.get('in-reply-to') and not headers.get('references') and
                not re.match(r'(?i)\s*(re|aw|wg|fw|fwd)\s*:', headers.get('subject', '')), 'not_clean_initial')
        customer = addresses(headers, 'from')
        require(len(customer) == 1 and 'office@elektrovienna.at' not in customer and
                'office@elektrovienna.at' in addresses(headers, 'to', 'cc', 'delivered-to'), 'initial_sender_invalid')
        require(not any(m['internal_ms'] < meta['internal_ms'] and customer & addresses(
            {k.lower(): v for k, v in json.loads(m['headers'])}, 'from', 'to', 'cc') for m in messages),
            'earlier_customer_contact')
        mime = BytesParser(policy=policy.default).parsebytes(raw)
        attached = Counter((p.get_filename(), digest(p.get_payload(decode=True) or b''))
                           for p in mime.walk() if p.get_filename())
        require(attached == Counter((f['filename'], f['sha256']) for f in sources if f['kind'] == 'attachment'),
                'attachment_not_in_t0')
        for source in sources:
            read_verified(archive, source)
        view = message_view(archive, {**meta, 'source': originals[0], 'headers': headers})
        inquiries.append({'id': mid, 'title': selected['title'], 'selection_reason': selected['selection_reason'],
                          'inquiry': view, 'sources': sources, 'cutoff_internal_ms': meta['internal_ms']})
    require(digest(state.read_bytes()) == before, 'state_changed')
    require(set(contexts) == {'operating_policy', 'playbook', 'price_book', 'north_star'}, 'context_required')
    current_policy = load_policy(contexts['operating_policy'])
    require(date.today() >= date.fromisoformat(current_policy['effective_from']), 'policy_not_effective')
    packet = {'kind': 'first_response_validation_packet', 'format_version': 1,
              'prepared_as_of': date.today().isoformat(), 'inquiries': inquiries,
              'contexts': {key: {'text': path.read_text('utf-8'), 'sha256': digest(path.read_bytes())}
                           for key, path in contexts.items()},
              'calibration_result_reference': feedback_reference, 'calibration_result_sha256': digest(feedback_raw),
              'prior_blind_reference': prior_blind_reference, 'prior_blind_sha256': digest(prior_raw),
              'excluded_message_ids': sorted(forbidden), 'excluded_thread_ids': sorted(forbidden_threads),
              'state_sha256': before, 'processor_sha256': digest(Path(__file__).read_bytes()),
              'history_used': [], 'history_rationale': 'T0 plus current guidance suffices; no additional case history needed.',
              'boundary': 'Current guidance applied today to historical T0. No outbound bodies, later attachments, CRM state or retrospective.'}
    run = digest(canonical(packet))
    store.publish(f'30_cases/first_response_validation/{run}/packet.json', canonical(packet))
    return run, packet


def publish(store, run, answers):
    packet_raw = store.path(f'30_cases/first_response_validation/{run}/packet.json').read_bytes()
    require(digest(packet_raw) == run, 'packet_integrity')
    packet = json.loads(packet_raw)
    require(set(answers) == {i['id'] for i in packet['inquiries']}, 'all_three_drafts_required')
    for inquiry in packet['inquiries']:
        answer = answers[inquiry['id']]
        require(set(answer) == {'draft', 'internal_checks', 'basis', 'price_items'}, 'answer_contract')
        require(answer['draft'].strip().endswith('Viele Grüße\nGiovanni Superti-Furga'), 'body_and_signoff_required')
        allowed = set(packet['contexts']) | {s['sha256'] for s in inquiry['sources']}
        require(answer['basis'] and set(answer['basis']) <= allowed, 'citation_outside_t0')
        prices = json.loads(packet['contexts']['operating_policy']['text'])['prices']
        require(set(answer['price_items']) <= set(prices), 'historical_price_forbidden')
    frozen = {'kind': 'first_response_validation_answers', 'packet_sha256': run,
              'answers': answers, 'answers_sha256': digest(canonical(answers)),
              'generation': 'Interactive Codex authored drafts; human acceptance pending; not sent.'}
    # No-replace freeze precedes review rendering; changed prose cannot replace this run.
    store.publish(f'30_cases/first_response_validation/{run}/answers.json', canonical(frozen))
    page = render(store, packet, run, frozen)
    store.publish(f'30_cases/first_response_validation/{run}/review.html', page.encode('utf-8'))
    return frozen


def render(store, packet, run, frozen):
    script = Path(__file__).with_name('response_calibration.js').read_text('utf-8')
    script_hash = base64.b64encode(bytes.fromhex(digest(script.encode('utf-8')))).decode()
    cards = []
    for n, item in enumerate(packet['inquiries'], 1):
        inquiry = item['inquiry']; attachments = []
        for source in item['sources']:
            if source['kind'] != 'attachment': continue
            raw = read_verified(SourceArchive(store.root), source)
            mime = source['mime_type']; name = escape(source['filename'], quote=True)
            if mime in {'image/jpeg', 'image/png'}:
                content = f'<img alt="{name}" src="data:{mime};base64,{base64.b64encode(raw).decode()}">'
            elif mime == 'application/pdf':
                content = f'<a download="{name}" href="data:application/pdf;base64,{base64.b64encode(raw).decode()}">Original-PDF herunterladen: {name}</a>'
            else: content = name + ' (nicht dargestellt)'
            attachments.append(f'<details><summary>T0-Anhang: {name}</summary>{content}</details>')
        options = [('send', 'Würde ich so schicken'), ('minor_change', 'Kleine Änderung'), ('reject', 'So nicht')]
        controls = ''.join(f'<label><input type="radio" name="choice-{n}" value="{value}"> {label}</label>'
                           for value, label in options)
        cards.append(f'<article data-id="{item["id"]}"><h2>{n}. {escape(item["title"])}</h2>'
                     f'<h3>KUNDENANFRAGE</h3><p class="meta">{escape(inquiry["date_vienna"])}</p>'
                     f'<p class="meta">Betreff: {escape(inquiry["headers"].get("subject", ""))}</p>'
                     f'<pre>{escape(inquiry["text"])}</pre>{"".join(attachments)}'
                     '<h3>→ VORGESCHLAGENE ANTWORT</h3>'
                     f'<pre>{escape(frozen["answers"][item["id"]]["draft"])}</pre>'
                     f'<fieldset><legend>Deine Einschätzung</legend>{controls}'
                     f'<label for="comment-{n}">Optionaler Kommentar</label><textarea id="comment-{n}" rows="2"></textarea>'
                     '</fieldset></article>')
    return f'''<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Erste Antworten · Drei Entwürfe</title><style>
body{{font:17px/1.55 system-ui,sans-serif;max-width:950px;margin:auto;padding:24px;color:#20312e;background:#f5f4ef}}
article{{background:white;border:1px solid #d8ded9;border-radius:12px;padding:25px;margin:24px 0}}h3{{font-size:14px;color:#175a50;margin-top:28px}}
pre{{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}}.meta{{font-size:14px;color:#53656b}}img{{max-width:100%;height:auto}}
label{{display:block;margin:10px 0}}textarea{{width:100%;box-sizing:border-box;font:inherit;padding:10px}}fieldset{{border:0;border-top:1px solid #ddd;margin-top:24px}}
button{{font:inherit;background:#175a50;color:white;border:0;border-radius:8px;padding:12px 20px;cursor:pointer}}footer{{position:sticky;bottom:0;background:#f5f4ef;padding:12px 0}}
</style></head><body data-mode="validation" data-set="{run}" data-answers="{frozen['answers_sha256']}">
<h1>Drei erste Antworten zur Freigabe</h1><p>Historische Anfragen, heute beantwortet mit aktuellem Playbook und Preisbuch.
Bitte pro Entwurf eine Auswahl treffen; ein kurzer Kommentar ist freiwillig. Nichts wurde versendet.</p>
<p class="meta">Nur die ursprüngliche Anfrage und ihre Anhänge; kein späterer Verlauf. Preise in Kundenanhängen sind historische Quellen, keine heutigen Tarife.
Termine und aktuelle Verfügbarkeit werden nicht aus der damaligen Korrespondenz übernommen.</p>
<p><strong>Zum Sichern „Bewertung herunterladen“ wählen.</strong> Kein automatisches Speichern oder Übertragen.</p>
{''.join(cards)}<footer><span id="progress" aria-live="polite"></span> <button id="download" type="button">Bewertung herunterladen</button>
<span id="saved" role="status"></span></footer><script>{script}</script></body></html>'''
