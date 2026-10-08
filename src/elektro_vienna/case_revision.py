"""Bounded, evidence-linked case revisions after explicit human completion.

No matching, CRM adapter, mutable current pointer or knowledge promotion. The plan
is an agent-authored interpretation; it is not itself a second human approval.
"""

import copy
import html
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path

from .archive_store import SourceArchive, source_reference
from .models import ProviderError
from .pilot_matching import source_identity
from .pilot_parse import PARSER_VERSION, parse_document, parse_email
from .pilot_sources import canonical, digest, read_verified
from .review_local import ReviewSession
from .review_ui import ReviewStore, STAGES, STAGE_LABELS, checked_json

STAGE_KEYS = {'inquiry', 'consultation', 'quote', 'execution', 'invoice_payment', 'remaining_work'}
ROLES = {'estimate', 'customer_quote', 'final_invoice', 'payment', 'labor_time',
         'material', 'travel_site_visit', 'internal_technician_cost'}
GROUPS = ('identity', 'state', 'technical', 'commercial', 'actors', 'open_questions')
CERTAINTIES = {'reviewed_source', 'human_reported', 'source_reported', 'inferred', 'unknown'}
LABELS = {'inquiry': 'Anfrage', 'consultation': 'Beratung / Besichtigung', 'quote': 'Angebot',
          'execution': 'Ausführung', 'invoice_payment': 'Rechnung / Zahlung', 'remaining_work': 'Restarbeiten',
          'estimate': 'Schätzung', 'customer_quote': 'Kundenangebot', 'final_invoice': 'Tatsächliche Rechnung',
          'payment': 'Zahlung', 'labor_time': 'Arbeit / Zeit', 'material': 'Material',
          'travel_site_visit': 'Anfahrt / Besichtigung', 'internal_technician_cost': 'Interne / Technikerkosten',
          'reviewed_source': 'Geprüfter Quellenstand', 'human_reported': 'Menschlich berichtet',
          'source_reported': 'Laut Originalbeleg', 'inferred': 'Abgeleitete Einordnung', 'unknown': 'Unbekannt'}


class RevisionStore(ReviewStore):
    def path(self, reference):
        if re.fullmatch(r'30_cases/reviewed/rec[A-Za-z0-9]{14}/[0-9a-f]{64}/(?:case.json|comparison.html)', reference):
            path = self.root.joinpath(*reference.split('/'))
            self._guard(path)
            return path
        return super().path(reference)


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ProviderError('case_revision_pointer_invalid')
    for part in path[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def latest_completed(store, case_id, baseline):
    """Select across all views, never from mutable drafts or legacy autosaves."""
    directory = store.root / '90_manual_review/completed'
    store._guard(directory)
    choices = []
    if directory.exists():
        for folder in sorted(directory.iterdir()):
            if not folder.is_dir() or not re.fullmatch('[0-9a-f]{64}', folder.name):
                continue
            session = ReviewSession(store, folder.name)
            if session.view['case_id'] != case_id or session.view['baseline_id'] != baseline:
                continue
            for review in session.history('completed'):
                instant = datetime.fromisoformat(review['completed_at'])
                if instant.utcoffset() is None or not review['reviewer'].strip():
                    raise ProviderError('case_revision_review_invalid')
                choices.append((instant, review['view_id'], review['revision'], review, session.view))
    if not choices:
        raise ProviderError('case_revision_completed_review_missing')
    return max(choices, key=lambda x: x[:3])[3:]


def checked_supplement(store, supplied):
    """Reparse only explicitly named, already archived originals with existing code."""
    if not supplied:
        return None
    sources = supplied['sources']
    mids = {s['message_id'] for s in sources}
    if not 1 <= len(mids) <= 10 or len(sources) > 50:
        raise ProviderError('case_revision_supplement_scope')
    result = {'parser_version': PARSER_VERSION,
              'parser_sha256': digest(Path(__file__).with_name('pilot_parse.py').read_bytes()),
              'pypdf_version': version('pypdf'), 'sources': [], 'messages': {}, 'documents': {}}
    seen = set()
    for row in sources:
        expected = source_reference(row['mailbox'], row['message_id'],
                                    None if row['kind'] == 'message' else row['part_id'])
        if row['kind'] not in {'message', 'attachment'} or row['reference'] != expected:
            raise ProviderError('case_revision_supplement_identity')
        sid = source_identity(row)
        if row['id'] != sid or sid in seen:
            raise ProviderError('case_revision_supplement_identity')
        seen.add(sid)
        raw = read_verified(SourceArchive(store.root), row)
        result['sources'].append(row)
        if row['kind'] == 'message':
            result['messages'][row['message_id']] = {'source_id': sid, **parse_email(raw)}
        else:
            result['documents'][sid] = {'source_id': sid, **parse_document(raw, row['mime_type'], row['filename'])}
    if set(result['messages']) != mids:
        raise ProviderError('case_revision_supplement_parent_missing')
    return result


def _build(store, plan):
    case_id, baseline = plan['case_id'], plan['baseline_id']
    if not re.fullmatch('rec[A-Za-z0-9]{14}', case_id) or not re.fullmatch('[0-9a-f]{64}', baseline):
        raise ProviderError('case_revision_identity_invalid')
    review, view = latest_completed(store, case_id, baseline)
    if plan['completed_review_reference'] != review['reference']:
        raise ProviderError('case_revision_review_not_latest')
    if review['whole_case_acknowledged'] is not True or review['status'] != 'completed':
        raise ProviderError('case_revision_review_not_completed')
    base, base_raw = checked_json(store, view['baseline_reference'], view['baseline_sha256'])
    case = next(c for c in base['cases'] if c['id'] == case_id)
    extraction, _ = checked_json(store, base['extraction_reference'], Path(base['extraction_reference']).stem)
    checked_json(store, base['snapshot_reference'], extraction['snapshot_sha256'])
    checked_json(store, view['feedback_reference'], view['feedback_sha256'])
    supplement = checked_supplement(store, plan.get('supplement'))
    sources = copy.deepcopy(view['sources'])
    sections = {(o['source_id'], s['locator']): s['text']
                for o in [*extraction['messages'].values(), *extraction['documents'].values()]
                for s in o['sections']}
    if view.get('document_collection_reference'):
        collection, _ = checked_json(store, view['document_collection_reference'],
                                     view['document_collection_reference'].split('/')[-2])
        for doc in collection['documents']:
            if doc.get('case_id') != case_id:
                continue
            parsed, _ = checked_json(store, doc['extraction_reference'], doc['extraction_sha256'])
            sections.update({(doc['source']['id'], s['locator']): s['text'] for s in parsed['sections']})
    if supplement:
        sources.update({s['id']: s for s in supplement['sources']})
        sections.update({(o['source_id'], s['locator']): s['text']
                         for o in [*supplement['messages'].values(), *supplement['documents'].values()]
                         for s in o['sections']})
    for row in sources.values():
        owner = store if row['reference'].startswith('00_raw/manual_documents/') else SourceArchive(store.root)
        if digest(owner.path(row['reference']).read_bytes()) != row['sha256']:
            raise ProviderError('case_revision_source_integrity')
    items = {i['id']: i for rows in view['sections'].values() for i in rows}
    decisions = {(d['kind'], d['id']): d for d in review['decisions']}
    comments = {(f['kind'], f['id']): f['comment'] for f in review['review']['fields'] if f['comment'].strip()}

    def basis(ref):
        kind = ref['kind']
        if kind == 'review_item':
            item = items[ref['id']]
            return ref | {'text': item['text'], 'source_refs': item['source_refs'],
                          'source_certainty': item['certainty'],
                          'review_state': decisions[('item', ref['id'])]['state'],
                          'view_id': review['view_id'], 'review_reference': review['reference']}
        if kind == 'comment':
            text = comments[(ref['target_kind'], ref['id'])]
            return ref | {'quote': text, 'reviewer': review['reviewer'], 'reported_at': review['completed_at'],
                          'review_reference': review['reference'], 'review_sha256': review['sha256']}
        if kind == 'baseline':
            return ref | {'value': pointer(case, ref['pointer']), 'reference': view['baseline_reference'],
                          'sha256': digest(base_raw), 'case_id': case_id}
        if kind == 'source_span':
            sid = ref['source_id']
            content = sections[(sid, ref['locator'])]
            start, end = ref['start'], ref['end']
            if (sid not in sources or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(content) or content[start:end] != ref['quote']):
                raise ProviderError('case_revision_citation_invalid')
            return ref | {'original_reference': sources[sid]['reference'], 'sha256': sources[sid]['sha256'],
                          'association': 'agent_located_after_review' if sid not in view['sources'] else 'existing_review_source'}
        raise ProviderError('case_revision_basis_invalid')

    seen = set()

    def fact(f):
        f = copy.deepcopy(f)
        if (not re.fullmatch('[a-z][a-z0-9_-]{0,79}', f['id']) or f['id'] in seen
                or not f['text'].strip() or f['certainty'] not in CERTAINTIES or not f['basis']):
            raise ProviderError('case_revision_fact_invalid')
        seen.add(f['id'])
        f['basis'] = [basis(r) for r in f['basis']]
        f['interpretation_status'] = 'agent_consolidation_of_completed_review; not_new_human_approval'
        return f

    facts = {group: [fact(f) for f in plan['facts'][group]] for group in GROUPS}
    stages = facts['state']
    if len(stages) != len(STAGE_KEYS) or {s['stage'] for s in stages} != STAGE_KEYS or any(s['status'] not in STAGES for s in stages):
        raise ProviderError('case_revision_stages_invalid')
    if not {'customer_account', 'project', 'address', 'service_type'} <= {f['field'] for f in facts['identity']}:
        raise ProviderError('case_revision_identity_incomplete')
    if {f['role'] for f in facts['commercial']} != ROLES:
        raise ProviderError('case_revision_commercial_role_invalid')
    for commercial in facts['commercial']:
        amount = commercial.get('amount')
        if amount is not None:
            try:
                number = Decimal(amount)
                if not isinstance(amount, str) or not number.is_finite() or number < 0 or not commercial.get('currency'):
                    raise ValueError
            except (InvalidOperation, ValueError, TypeError):
                raise ProviderError('case_revision_amount_invalid') from None
    events = []
    for item in plan['events']:
        event = fact(item)
        occurred = event['occurred_at']
        if event['time_precision'] not in {'date', 'datetime', 'unknown'}:
            raise ProviderError('case_revision_event_time_invalid')
        if (occurred is None) != (event['time_precision'] == 'unknown'):
            raise ProviderError('case_revision_event_time_invalid')
        if occurred is not None:
            instant = datetime.fromisoformat(occurred)
            if (event['time_precision'] == 'date' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', occurred)
                    or event['time_precision'] == 'datetime' and instant.utcoffset() is None):
                raise ProviderError('case_revision_event_time_invalid')
        if event['channel'] == 'human_review':
            event['reported_at'] = review['completed_at']
            event['reported_by'] = review['reviewer']
        event['case_id'] = case_id
        events.append(event)
    events.sort(key=lambda e: (e['occurred_at'] is None, e['occurred_at'] or '', e['id']))
    observations = []
    for proposal in plan['crm_observations']:
        observation = fact(proposal)
        observation['before'] = pointer(case, proposal['baseline_pointer'])
        observation['snapshot_reference'] = base['snapshot_reference']
        observation['effect'] = 'proposal_only; no_crm_write'
        observations.append(observation)
    resolutions = plan['comment_resolutions']
    if (len(resolutions) != len(comments) or {(r['kind'], r['id']) for r in resolutions} != set(comments)
            or any(not r['targets'] or not set(r['targets']) <= seen or not r['note'].strip() for r in resolutions)):
        raise ProviderError('case_revision_comment_unrepresented')
    # Preserve original proposal membership and baseline events, even for mixed accounts.
    # Scoped events below never rewrite their historical association decisions.
    scopes = plan['project_scopes']
    scope_ids = {s['id'] for s in scopes}
    assigned = []
    allowed_mids = {m['message_id'] for m in case['matches']}
    if supplement:
        allowed_mids.update(supplement['messages'])
    for scope in scopes:
        if not scope['id'] or not scope['title'] or not scope['association_note']:
            raise ProviderError('case_revision_project_scope_invalid')
        assigned.extend(scope['message_ids'])
    if (len(scope_ids) != len(scopes) or len(assigned) != len(set(assigned)) or set(assigned) != allowed_mids
            or any(f.get('project_scope') not in scope_ids for g in facts.values() for f in g if f.get('project_scope'))
            or any(e.get('project_scope') not in scope_ids for e in events if e.get('project_scope'))):
        raise ProviderError('case_revision_project_scope_invalid')
    previous = plan.get('previous_revision')
    if previous:
        old, _ = checked_json(store, previous, previous.split('/')[-2])
        if old['case_id'] != case_id or old['baseline_id'] != baseline:
            raise ProviderError('case_revision_parent_invalid')
    return {'format_version': 1, 'kind': 'reviewed_case_revision', 'case_id': case_id, 'baseline_id': baseline,
            'lineage': {'baseline_reference': view['baseline_reference'], 'baseline_sha256': digest(base_raw),
                        'completed_review_reference': review['reference'], 'completed_review_sha256': review['sha256'],
                        'view_reference': f'30_cases/review_ux/{review["view_id"]}/view.json',
                        'previous_revision': previous, 'prior_review': view.get('prior_review'),
                        'feedback_reference': view['feedback_reference'], 'feedback_sha256': view['feedback_sha256']},
            'processing': {'processor': 'reviewed-case-v1', 'code_sha256': digest(Path(__file__).read_bytes()),
                           'curator': plan['curator'], 'reviewed_at': review['completed_at'],
                           'trust': 'case_scoped_consolidation; no_global_knowledge; new_wording_not_human_approved'},
            'facts': facts, 'events': events, 'baseline_events': case['timeline'], 'baseline_matches': case['matches'],
            'before_review': {'identity': case['identity'], 'outcome': case['outcome'],
                              'requested_work': case['requested_work'], 'notes': case['notes'],
                              'review_view_sections': view['sections']},
            'completed_review': review, 'comment_resolutions': resolutions,
            'crm_observations': observations, 'project_scopes': scopes,
            'sources': sources, 'extraction_reference': base['extraction_reference'],
            'supplement': supplement, 'document_search': plan['document_search'],
            'effect': 'inspection_only; no_airtable_source_archive_or_knowledge_mutation'}


def build_revision(store, plan):
    try:
        return _build(store, plan)
    except (KeyError, TypeError, AttributeError, ValueError, StopIteration, IndexError):
        raise ProviderError('case_revision_plan_invalid') from None


def render_comparison(revision):
    esc = lambda x: html.escape(str(x), quote=True)
    name = revision['before_review']['identity']['name']['value']
    parts = [f'<h1>{esc(name)}</h1><p>Versionierter Fallstand aus abgeschlossener Prüfung. '
             'Neue Formulierungen sind eine Agenten-Konsolidierung, keine erneute menschliche Freigabe. '
             'CRM-Vergleich zum gespeicherten Snapshot, kein Live-CRM-Abruf und keine Schreibaktion.</p>']

    def refs(f):
        body = []
        for r in f['basis']:
            kind = r['kind']
            label = {'comment': 'Menschliche Rückmeldung', 'review_item': 'Geprüfter Falltext',
                     'source_span': 'Originalbeleg', 'baseline': 'Pilot / CRM-Snapshot'}[kind]
            value = r.get('quote', r.get('text', r.get('value', '')))
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, indent=2)
            link = ''
            if kind == 'source_span':
                link = f'<a href="../../../../{esc(r["original_reference"])}">Original öffnen</a>'
            elif kind in {'comment', 'review_item'}:
                link = f'<a href="../../../../{esc(revision["lineage"]["view_reference"].replace("view.json", "review.html"))}">Geprüfte Ansicht</a>'
            body.append(f'<p><strong>{label}</strong> {link}</p><pre>{esc(value)}</pre>')
        return '<details><summary>Herkunft</summary>' + ''.join(body) + '</details>'

    def facts(rows):
        return ''.join(f'<article><h3>{esc(LABELS.get(f.get("title"), f.get("title", f["id"])))}</h3><p>{esc(f["text"])}</p>'
                       f'<small>{esc(LABELS[f["certainty"]])}' + (f' · {esc(STAGE_LABELS[f["status"]])}' if 'status' in f else '')
                       + '</small>' + refs(f) + '</article>' for f in rows)

    parts.append('<h2>Vor der Prüfung</h2>')
    before = revision['before_review']
    parts.append(f'<p>CRM: {esc(before["identity"]["status"]["value"])} · '
                 f'Techniker: {esc(before["identity"]["craftsmen"]["value"])}</p>')
    for item in before['review_view_sections']['outcome']:
        parts.append(f'<p><strong>{esc(item["title"])}:</strong> {esc(item["text"])}</p>')
    parts.append('<h2>→ Menschliche Korrekturen</h2>')
    review = revision['completed_review']
    parts.append(f'<p>{esc(review["reviewer"])} · Abschluss {esc(review["completed_at"])} · '
                 f'Quellen gesondert bestätigt: {esc(review["sources_acknowledged"])}</p>')
    comments = [f for f in review['review']['fields'] if f['comment'].strip()]
    if not comments:
        parts.append('<p>Keine neuen Korrekturkommentare. Die bereits eingearbeiteten früheren Rückmeldungen bleiben über die geprüfte Ansicht erhalten.</p>')
    for f in comments:
        parts.append(f'<blockquote><strong>{esc(f["id"])}</strong><pre>{esc(f["comment"])}</pre></blockquote>')
    parts.append('<h2>→ Konsolidierter Fallstand</h2>')
    for group, title in [('identity', 'Identität'), ('state', 'Leistungsstufen'), ('actors', 'Beteiligte'),
                         ('technical', 'Technische Fakten'), ('commercial', 'Kaufmännische Fakten'),
                         ('open_questions', 'Offen / unbekannt')]:
        rows = revision['facts'][group]
        unknown_roles = [f for f in rows if group == 'commercial' and f['id'].startswith('unknown-')]
        parts.append(f'<h2>{title}</h2>' + facts([f for f in rows if f not in unknown_roles]))
        if unknown_roles:
            parts.append('<p>Kein gesondert belastbarer Wert belegt: ' + esc(', '.join(LABELS[f['role']] for f in unknown_roles)) + '. Beträge und Stunden bleiben unbekannt.</p>')
    parts.append('<h2>Projektabgrenzung</h2>')
    for scope in revision['project_scopes']:
        parts.append(f'<p><strong>{esc(scope["title"])}:</strong> {esc(scope["association_note"])}</p>')
    parts.append('<h2>Chronologie</h2>')
    for event in revision['events']:
        parts.append(f'<p><strong>{esc(event["occurred_at"] or "Zeitpunkt unbekannt")}</strong> · '
                     f'{esc(event["channel"])} · {esc(event["text"])}</p>' + refs(event))
    parts.append('<h2>→ Mögliche CRM-Updates</h2><p>Nur Vorschläge zum gespeicherten Snapshot.</p>' + facts(revision['crm_observations']))
    search = revision['document_search']
    parts.append('<h2>Belegsuche</h2><p>' + esc(search['scope']) + '</p>')
    for key, title in [('found', 'Gefunden'), ('unresolved', 'Offen')]:
        parts.append(f'<p><strong>{title}:</strong> ' + esc(' · '.join(search.get(key, [])) or 'Keine weiteren Angaben.') + '</p>')
    parts.append('<details><summary>Versionierung und vollständige Provenienz</summary><p><a href="case.json">Fallrevision</a></p>'
                 + '<pre>' + esc(json.dumps(revision['lineage'], ensure_ascii=False, indent=2)) + '</pre></details>')
    parts.append('<details><summary>Ursprüngliche Quellenchronologie (unverändert)</summary>' + ''.join(
        f'<p>{esc(e["timestamp"]["value"])} · {esc(e["channel"])} · {esc(e["summary"]["value"])}</p>'
        for e in revision['baseline_events']) + '</details>')
    style = 'body{font:17px/1.55 system-ui;margin:40px auto;max-width:1000px;padding:0 24px;color:#17333c;background:#f7f8f6}h1{font-size:30px}h2{margin-top:36px;border-bottom:1px solid #bfd0cc}article{background:white;padding:16px 22px;margin:14px 0;border:1px solid #d8e1dd;border-radius:8px}h3{margin:0}small{color:#53645e}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 system-ui}blockquote{border-left:4px solid #43806e;padding:0 18px}a{color:#146b59}summary{cursor:pointer}'
    return '<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>' + esc(name) + ' · Fallrevision</title><style>' + style + '</style><body>' + ''.join(parts) + '</body></html>'


def publish_revisions(store, plans, *, dry_run=False):
    if not 1 <= len(plans) <= 5 or len({p['case_id'] for p in plans}) != len(plans):
        raise ProviderError('case_revision_batch_scope')
    # Validate the entire bounded batch before any publication.
    revisions = [build_revision(store, p) for p in plans]
    outputs = []
    for revision in revisions:
        raw = canonical(revision)
        identity = digest(raw)
        prefix = f'30_cases/reviewed/{revision["case_id"]}/{identity}'
        if not dry_run:
            store.publish(prefix + '/comparison.html', render_comparison(revision).encode())
            store.publish(prefix + '/case.json', raw)  # completion marker last, immutable
        outputs.append({'case_id': revision['case_id'], 'revision_id': identity,
                        'reference': prefix + '/case.json', 'comparison': prefix + '/comparison.html'})
    return outputs
