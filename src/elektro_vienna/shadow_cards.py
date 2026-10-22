"""Validate Codex-authored shadow proposals and render one offline operator page."""

import base64
import json
import re
from datetime import datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive_store import SourceArchive
from .blind_eval import REVIEWED, require
from .models import ArchivedFile
from .pilot_sources import canonical, digest
from .shadow_input import KINDS, guidance

SIGNOFF = 'Viele Grüße\nGiovanni Superti-Furga'
STAGES = {'inquiry', 'consultation', 'quote', 'execution', 'invoice_payment', 'remaining_work'}
CHOICES = {'sendable', 'needs_edit', 'wrong_direction', 'not_customer'}


def verify_sources(store, item):
    archive = SourceArchive(store.root)
    for source in item['sources']:
        archive.verify(ArchivedFile(**{k: source[k] for k in ('reference', 'sha256', 'byte_length', 'archived_at')}))


def fact_valid(fact, item):
    require(set(fact) == {'value', 'locator', 'quote'}, 'customer_fact_contract')
    if fact['value'] is None:
        require(fact['locator'] is None and fact['quote'] is None, 'missing_field_must_remain_missing')
        return
    require(isinstance(fact['value'], str) and fact['value'] and isinstance(fact['quote'], str)
            and fact['value'] in fact['quote'], 'customer_value_not_in_quote')
    if fact['locator'].startswith('header:'):
        evidence = item['headers'].get(fact['locator'].split(':')[-1], '')
    else:
        evidence = '\n'.join(s['text'] for s in item['parsed_sections'] if s['locator'] == fact['locator'])
    require(fact['quote'] in evidence, 'customer_quote_not_in_source')


def publish_card(store, plan, repo):
    require(set(plan) == {'input_reference', 'classification', 'classification_reason', 'customer',
                          'need', 'known', 'blocking_customer_information', 'internal_checks', 'commercial',
                          'history', 'next_action', 'draft', 'crm_proposal', 'case_update', 'basis'}, 'shadow_card_contract')
    item = store.read(plan['input_reference'])
    require(item['kind'] == 'live_shadow_input' and plan['classification'] in KINDS, 'shadow_classification')
    require(item['direction'] != 'sent' or plan['classification'] == 'non_customer', 'sent_not_new_inquiry')
    require({k: v['sha256'] for k, v in item['contexts'].items()} ==
            {k: v['sha256'] for k, v in guidance(repo).items()}, 'guidance_changed_refresh_input_before_drafting')
    verify_sources(store, item)
    require(set(plan['customer']) == {'name', 'email', 'phone', 'company', 'address'}, 'customer_fields')
    for fact in plan['customer'].values(): fact_valid(fact, item)
    if item['identity_basis'].startswith('wix'):
        require(plan['customer']['email']['value'] is None or
                not plan['customer']['email']['locator'].endswith(':from'), 'wix_envelope_not_customer')
    require(isinstance(plan['next_action'], str) and bool(plan['next_action'].strip()), 'next_action_required')
    for key in ('known', 'blocking_customer_information', 'internal_checks'):
        require(isinstance(plan[key], list) and all(isinstance(x, str) for x in plan[key]), 'card_list_contract')
    allowed = set(item['contexts']) | {s['sha256'] for s in item['sources']}
    require(plan['basis'] and set(plan['basis']) <= allowed, 'card_source_basis')
    history = plan['history']
    require(set(history) == {'reference', 'why_useful', 'lesson'}, 'history_contract')
    history_binding = None
    if history['reference']:
        reference = history['reference']
        require(bool(re.fullmatch(r'30_cases/reviewed/rec[A-Za-z0-9]{14}/[0-9a-f]{64}/case.json', reference)), 'history_path')
        path = store.root / reference; store._guard(path)
        raw = path.read_bytes(); revision = json.loads(raw)
        require(digest(raw) == reference.split('/')[-2], 'reviewed_history_integrity')
        require(revision['kind'] == 'reviewed_case_revision' and revision['case_id'] in REVIEWED
                and revision['completed_review']['status'] == 'completed' and bool(history['why_useful']),
                'unreviewed_history_forbidden')
        history_binding = digest(raw)
    else:
        require(history['lesson'] == 'No useful reviewed comparison.', 'no_forced_history')
    commercial = plan['commercial']
    require(set(commercial) == {'kind', 'standard_items', 'estimate_lines', 'material_assumption',
                                'travel', 'vat', 'pricing_decision', 'explanation'}, 'commercial_contract')
    require(commercial['kind'] in {'current_standard_price', 'estimate', 'rough_indication', 'internal_decision_required', 'not_applicable'},
            'unsupported_fixed_price_requires_owner_confirmation')
    prices = json.loads(item['contexts']['operating_policy']['text'])['prices']
    require(set(commercial['standard_items']) <= set(prices), 'historical_price_cannot_override_current')
    amounts = {Decimal(prices[k]['amount_net_eur']) for k in commercial['standard_items']}
    estimate = Decimal(0)
    for line in commercial['estimate_lines']:
        require(set(line) == {'price_item', 'quantity', 'assumption'} and line['price_item'] in commercial['standard_items']
                and bool(line['assumption']), 'estimate_requires_current_basis')
        quantity = Decimal(str(line['quantity']))
        require(quantity.is_finite() and 0 < quantity <= 100, 'estimate_quantity_invalid')
        estimate += quantity * Decimal(prices[line['price_item']]['amount_net_eur'])
    if commercial['estimate_lines']:
        require(commercial['kind'] in {'estimate', 'rough_indication'}, 'estimate_not_fixed_price')
        amounts.add(estimate)
        ids = {line['price_item'] for line in commercial['estimate_lines']}
        require(not {'travel', 'single_first_hour_travel'} <= ids, 'double_travel_forbidden')
    draft = plan['draft']
    if draft is not None:
        require(isinstance(draft, str) and draft.strip().endswith(SIGNOFF), 'body_and_signoff_only')
        require(not re.search(r'(?i)(www\.elektrovienna|office@elektrovienna|firmenbuch|UID\s*[:.-]|ATU\d{8})', draft),
                'company_footer_forbidden')
        # Free-form currency amounts must be supported by this card's current price basis.
        money = re.findall(r'(?i)(?:EUR|€)\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*(?:EUR|€|Euro|netto|brutto)', draft)
        require(all(Decimal((a or b).replace(',', '.')) in amounts for a, b in money), 'unsupported_draft_price')
        require(not re.search(r'(?i)\b(brutto|gross)\b', draft), 'gross_requires_explicit_tax_confirmation_not_in_price_book')
        if money: require(re.search(r'(?i)\b(net|netto)\b', draft) is not None, 'net_price_label_required')
    require(plan['classification'] != 'new_customer_inquiry' or bool(draft), 'inquiry_draft_required')
    crm = plan['crm_proposal']
    require(set(crm) == {'kind', 'customer_account', 'project', 'address', 'service_type', 'request_summary',
                         'status', 'missing_information', 'commercial_indication', 'next_action', 'matches'}, 'crm_contract')
    require(crm['kind'] == 'proposal_only' and crm['customer_account'] == plan['customer']
            and crm['address'] == plan['customer']['address']['value'], 'crm_is_proposal_not_mutation')
    require(all(m.get('status') == 'proposal' and m.get('basis') for m in crm['matches']), 'match_proposal_not_merge')
    if plan['classification'] == 'non_customer':
        require(draft is None and crm['status'] == 'not_applicable' and crm['project'] is None
                and commercial['kind'] == 'not_applicable', 'noncustomer_must_not_create_job_or_reply')
    update = plan['case_update']
    if plan['classification'] == 'case_update':
        require(isinstance(update, dict) and set(update) == {'probable_existing_case', 'event', 'what_changed',
                'proposed_stages', 'possible_crm_changes', 'response_required'}, 'case_update_required')
        require(set(update['proposed_stages']) == STAGES and all(
            isinstance(s, dict) and set(s) == {'state', 'basis'} and s['state'] in {'unknown', 'source_reported', 'proposed'}
            for s in update['proposed_stages'].values()), 'separate_case_stages_required')
        require(set(update['event']) == {'type', 'occurred_at', 'time_basis', 'actor', 'summary', 'source_reference', 'association'},
                'case_event_contract')
        require(update['event']['occurred_at'] is None or
                datetime.fromisoformat(update['event']['occurred_at'].replace('Z', '+00:00')).utcoffset() is not None,
                'case_event_timestamp')
        require(update['event']['source_reference'] == plan['input_reference'] and
                update['event']['association'] == 'proposal', 'case_event_source_required')
        require(isinstance(update['response_required'], bool) and (not update['response_required'] or bool(draft)), 'response_decision')
    else: require(update is None, 'unexpected_case_update')
    card = {'kind': 'live_shadow_card', 'format_version': 1, 'proposal': plan,
            'input_sha256': plan['input_reference'].split('/')[-2], 'history_sha256': history_binding,
            'standard_prices': {k: prices[k] for k in commercial['standard_items']},
            'estimate_net_eur': str(estimate) if commercial['estimate_lines'] else None,
            'author': 'Interactive Codex; pending Giovanni review', 'external_actions': [],
            'processor_sha256': digest(Path(__file__).read_bytes())}
    return store.document('card', card)


def render_batch(store, state):
    inputs = [ref for ref in state['done'].values() if ref]
    batch = {'kind': 'live_shadow_batch', 'format_version': 1, 'inputs': inputs,
             'cards': state['cards'], 'start_ms': state['start_ms'], 'history_id': state['history_id'],
             'unavailable_drafts': state.get('unavailable_drafts', {}),
             'unavailable_messages': state.get('unavailable_messages', {}),
             'availability_resolutions': state.get('availability_resolutions', {}),
             'pending_page': bool(state['page'] or state['page_token']),
             'renderer_sha256': digest(Path(__file__).read_bytes()),
             'script_sha256': digest(Path(__file__).with_name('shadow_review.js').read_bytes())}
    reference = store.document('batch', batch)
    run = reference.split('/')[-2]
    articles = []
    for ref in inputs:
        item = store.read(ref); verify_sources(store, item)
        card_ref = state['cards'].get(ref)
        card = store.read(card_ref) if card_ref else None
        plan = card['proposal'] if card else None
        draft = plan['draft'] if plan else None
        fields = []
        if plan:
            for key, title in [('customer', 'CUSTOMER'), ('need', 'CUSTOMER NEED'), ('known', 'ALREADY KNOWN'),
                               ('blocking_customer_information', 'BLOCKING CUSTOMER INFORMATION'), ('internal_checks', 'INTERNAL CHECKS'),
                               ('commercial', 'COMMERCIAL'), ('history', 'HISTORICAL EXPERIENCE'), ('next_action', 'RECOMMENDED NEXT ACTION')]:
                value = plan[key]
                if key == 'customer': value = {k: v['value'] for k, v in value.items()}
                if key == 'commercial':
                    value = {**value, 'current_standard_prices_EUR_net': {k: f"{p['amount_net_eur']} / {p['unit']}"
                                                                        for k, p in card['standard_prices'].items()},
                             'estimate_total_EUR_net': card['estimate_net_eur']}
                fields.append(f'<h3>{title}</h3><pre>{escape(pretty(value))}</pre>')
            if plan['case_update']:
                fields.append(f'<h3>CASE UPDATE · PROPOSAL</h3><pre>{escape(pretty(plan["case_update"]))}</pre>')
        else:
            fields.append('<p>Sent message retained as context; no new customer inquiry.</p>' if item['direction'] == 'sent' else
                          '<p class="pending">Codex reasoning pending. This is an input, not a completed shadow card.</p>')
        attachments = []
        for source in item['sources']:
            if source['kind'] != 'attachment' or not source.get('filename'): continue
            mime = source['mime_type']; name = escape(source['filename'], quote=True)
            # Bounded safe inline originals; other types stay downloadable through a local file link.
            path = SourceArchive(store.root).path(source['reference'])
            if mime in {'image/jpeg', 'image/png', 'application/pdf'} and source['byte_length'] <= 10 * 1024 * 1024:
                uri = f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()
                preview = f'<img alt="{name}" src="{uri}">' if mime.startswith('image/') else ''
                attachments.append(f'<details><summary>Anhang: {name}</summary>{preview}<a download="{name}" href="{uri}">Original herunterladen</a></details>')
            else:
                attachments.append(f'<p>Anhang: {name} · Originalpfad: {escape(str(path))}</p>')
        options = [('sendable', 'SENDABLE AS WRITTEN'), ('needs_edit', 'NEEDS EDIT'), ('wrong_direction', 'WRONG DIRECTION'), ('not_customer', 'NOT A CUSTOMER INQUIRY')]
        controls = ''.join(f'<label><input type="radio" name="choice-{item["message_id"]}" value="{value}"'
                           f'{" disabled" if value == "sendable" and not draft else ""}> {title}</label>' for value, title in options)
        classification = plan['classification'] if plan else item['classification']
        sent = item['direction'] == 'sent'
        content = (f'<h2>{escape(item["headers"].get("subject", "(ohne Betreff)"))}</h2>'
                   f'<p>{escape(item["direction"])} · {escape(classification)} · {datetime.fromtimestamp(item["internal_ms"]/1000, tz=ZoneInfo("Europe/Vienna")).isoformat()}</p>'
                   f'<h3>SOURCE</h3><p>{escape(item["headers"].get("from", ""))}</p><pre>{escape(item["text"])}</pre>'
                   f'<details><summary>Provenienz und Anhänge</summary><p>{escape(ref)}</p><pre>{escape(pretty(item["sources"]))}</pre>{"".join(attachments)}</details>'
                   f'<h3>SHADOW CARD</h3>{"".join(fields)}<h3>CUSTOMER RESPONSE DRAFT · NOT SENT</h3>'
                   f'<pre class="draft">{escape(draft or "No draft prepared / no response proposed.")}</pre>'
                   f'{"<button type=button class=copy>Antwort kopieren</button>" if draft else ""}'
                   f'<h3>CRM PROPOSAL · NO WRITE</h3><pre>{escape(pretty(plan["crm_proposal"]) if plan else "Pending Codex review; no mutation.")}</pre>'
                   f'<fieldset><legend>Local feedback only</legend>{controls}<label>Optionaler Kommentar<textarea rows="2"></textarea></label></fieldset>')
        if sent: content = '<details><summary>Sent context · ' + escape(item['headers'].get('subject', '')) + '</summary>' + content + '</details>'
        articles.append(f'<article data-input="{ref}" data-card="{card_ref or ""}">{content}</article>')
    script = Path(__file__).with_name('shadow_review.js').read_text('utf-8')
    unavailable = [store.read(ref) for ref in batch['unavailable_drafts'].values()]
    audit = ('<details><summary>Excluded unavailable drafts: ' + str(len(unavailable)) + '</summary><pre>'
             + escape(pretty(unavailable)) + '</pre></details>') if unavailable else ''
    gaps = [store.read(ref) for ref in batch['unavailable_messages'].values()]
    if gaps:
        audit += ('<p class="pending">Unresolved source gaps: ' + str(len(gaps)) +
                  '. Gmail returned 404. These IDs remain queued for bounded retry; their content is unknown.</p>'
                  '<details><summary>Source gap evidence</summary><pre>' + escape(pretty(gaps)) + '</pre></details>')
    sh = base64.b64encode(bytes.fromhex(digest(script.encode()))).decode()
    page = f'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{sh}'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Elektro Vienna · Live shadow pilot</title><style>
body{{font:16px/1.55 system-ui;max-width:1050px;margin:auto;padding:24px;background:#f5f4ef;color:#213b35}}
article{{background:white;padding:24px;margin:24px 0;border:1px solid #d3ddd6;border-radius:12px}}h3{{font-size:14px;margin-top:28px}}
pre{{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}}label{{display:block;margin:10px 0}}textarea{{display:block;width:100%;font:inherit;box-sizing:border-box}}
button{{font:inherit;padding:10px 18px;background:#175a50;color:white;border:0;border-radius:6px;cursor:pointer}}img{{max-width:100%}}
footer{{position:sticky;bottom:0;background:#f5f4ef;padding:12px}}.pending{{background:#fff1c9;padding:12px}}
</style></head><body data-batch="{run}"><h1>Live shadow pilot</h1>
<p>Local drafts and CRM proposals only. Nothing is sent or written externally. Read source and draft before copying. A later email may make an older draft stale; check the conversation before use.</p>
<p>{len(inputs)} sources · {len(state['cards'])} reviewed-by-Codex proposals · Feedback is downloaded, then preserved with shadow-feedback. The browser does not autosave.</p>
{audit}
{''.join(articles) or '<p>No new messages in the live window. Run the manual check again when real mail arrives.</p>'}
<footer><button id="download" type="button">Feedback als JSON herunterladen</button><p id="status" role="status"></p></footer>
<script>{script}</script></body></html>'''
    html_ref = f'30_cases/live_shadow/{run}/review.html'
    store.publish(html_ref, page.encode())
    return {'batch_reference': reference, 'review_path': str(store.path(html_ref)), 'input_count': len(inputs), 'card_count': len(state['cards'])}


def pretty(value):
    if isinstance(value, str): return value
    if value is None: return 'Not supplied / unknown'
    if isinstance(value, list): return '\n'.join('• ' + pretty(v) for v in value) or 'None identified.'
    if isinstance(value, dict): return '\n'.join(f'{k.replace("_", " ")}: {pretty(v)}' for k, v in value.items())
    return str(value)


def import_feedback(store, raw):
    review = json.loads(raw)
    require(review['kind'] == 'live_shadow_feedback' and review['format_version'] == 1
            and review['reviewer'] == 'Giovanni Superti-Furga', 'shadow_feedback_invalid')
    require(datetime.fromisoformat(review['recorded_at'].replace('Z', '+00:00')).utcoffset() is not None, 'feedback_timestamp')
    batch = store.read(f'30_cases/live_shadow/{review["batch"]}/batch.json')
    require(len(review['items']) == len({x['input_reference'] for x in review['items']}) and
            {x['input_reference'] for x in review['items']} == set(batch['inputs']), 'feedback_items')
    for decision in review['items']:
        require(decision['choice'] in CHOICES | {None} and isinstance(decision['comment'], str), 'feedback_choice')
        card = batch['cards'].get(decision['input_reference'])
        require(decision['card_reference'] == card, 'feedback_card_binding')
        if decision['choice'] == 'sendable': require(card and store.read(card)['proposal']['draft'], 'no_draft_to_approve')
    original = f'90_manual_review/live_shadow/{digest(raw)}/original.json'
    store.publish(original, raw)
    result = {'kind': 'preserved_live_shadow_feedback', 'original_reference': original,
              'original_sha256': digest(raw), 'batch': review['batch'], 'reviewer': review['reviewer'],
              'recorded_at': review['recorded_at'], 'items': review['items'],
              'effect': 'Local human feedback only; no send, CRM mutation, or automatic knowledge promotion.'}
    return store.document('result', result, review=True)
