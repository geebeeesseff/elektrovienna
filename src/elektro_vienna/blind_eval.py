"""Small offline inquiry experiment: prepare -> freeze all answers -> reveal.

This is an artifact boundary, not an OS sandbox or a production agent. Only the
prepare/reveal side sees pilot metadata; the answer writer receives packet.json.
Source text is untrusted data. No provider, model, matching or delivery calls.
"""

import argparse
import base64
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .archive_store import SourceArchive
from .pilot_parse import parse_email
from .pilot_sources import canonical, digest, read_verified

REVIEWED = {'rec1UQBiAv31HAonb', 'rec46pEibvvvQhcWZ', 'recHWvM5L6FmYUh0A',
            'recpXIAjYKuNPj938', 'rec38U9hrvfcVgvVl'}
SECTIONS = ('need', 'known', 'missing', 'questions', 'history', 'commercial',
            'technical', 'action', 'draft')
LABELS = ('Kundenbedarf', 'Bekannte Fakten am T0', 'Fehlende Informationen – priorisiert',
          'Konkrete Rückfragen', 'Relevante geprüfte Erfahrung', 'Kaufmännische Einordnung',
          'Technische Überlegungen', 'Nächster Schritt', 'Kundenentwurf – NICHT VERSENDET')
DIMENSIONS = ('need', 'missing', 'questions', 'history', 'commercial', 'technical',
              'uncertainty', 'action', 'draft')
RETRO = ('trajectory', 'anticipated', 'helpful_questions', 'missed', 'overreach',
         'history_value', 'harm')
RETRO_LABELS = ('Späterer belegter Verlauf', 'Richtig erkannte Lücken', 'Hilfreiche Fragen',
                'Übersehenes', 'Überzogene Aussagen', 'Nutzen der Erfahrung', 'Mögliche Schäden')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


class EvalStore(SourceArchive):
    def path(self, reference):
        require(bool(re.fullmatch(r'30_cases/blind_eval/[0-9a-f]{64}/(?:packet|frozen|answer_key|evaluation)\.json|'
                                  r'30_cases/blind_eval/[0-9a-f]{64}/overview\.html', reference)),
                'evaluation_path_invalid')
        path = self.root.joinpath(*reference.split('/'))
        self._guard(path)
        return path


def now():
    return datetime.now(timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def binding(path):
    return {'path': str(path), 'sha256': digest(Path(path).read_bytes())}


def checked_file(root, reference):
    path = root / reference
    require(path.resolve().is_relative_to(root.resolve()), 'input_path_outside_root')
    return path


def source_tokens(value):
    """Walk provenance IDs, including source refs nested in review items."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in {'source_id', 'message_id', 'thread_id', 'sha256'} and isinstance(v, str):
                yield v
            yield from source_tokens(v)
    elif isinstance(value, list):
        for v in value:
            yield from source_tokens(v)


def knowledge_cards(root, references, forbidden):
    cards = []
    for ref in references:
        path = checked_file(root, ref)
        revision = load(path)
        require(revision['case_id'] in REVIEWED, 'unreviewed_knowledge_forbidden')
        require(revision['kind'] == 'reviewed_case_revision', 'wrong_revision_kind')
        require(revision['completed_review']['status'] == 'completed', 'review_not_completed')
        facts, excluded = [], 0
        for group in ('state', 'technical', 'commercial', 'open_questions'):
            for fact in revision['facts'][group]:
                if set(source_tokens(fact)) & forbidden:
                    excluded += 1
                    continue
                # Do not include baseline, matches, CRM, raw conversations or basis payloads.
                facts.append({k: fact[k] for k in ('id', 'title', 'text', 'certainty')})
        cards.append({'case_id': revision['case_id'], 'reference': ref,
                      'revision_sha256': digest(path.read_bytes()), 'facts': facts,
                      'excluded_overlap_facts': excluded})
    require(len(cards) == 5 and {c['case_id'] for c in cards} == REVIEWED, 'exactly_five_reviewed_cases_required')
    return cards


def prepare(root, plan, guidance_path):
    """Project original initial messages; never emit full case/CRM records."""
    pilot_path = checked_file(root, plan['pilot_reference'])
    pilot = load(pilot_path)
    extraction_path = checked_file(root, pilot['extraction_reference'])
    extraction = load(extraction_path)
    require(digest(extraction_path.read_bytes()) == extraction_path.stem, 'extraction_integrity')
    cases = {c['id']: c for c in pilot['cases']}
    inventory = {m['message_id']: m for m in extraction['inventory_scope']}
    selections = plan['selection']
    selected_ids = {s['case_id'] for s in selections}
    require(len(selections) == len(selected_ids) == 3 and not selected_ids & REVIEWED, 'three_heldout_cases_required')
    heldout_mids = {m['message_id'] for cid in selected_ids for m in cases[cid]['matches']}
    heldout_sources = [s for s in extraction['sources'] if s['message_id'] in heldout_mids]
    forbidden = (selected_ids | heldout_mids | {inventory[m]['thread_id'] for m in heldout_mids}
                 | {s[k] for s in heldout_sources for k in ('id', 'sha256')})
    cards = knowledge_cards(root, plan['reviewed_references'], forbidden)
    inquiries = []
    archive = SourceArchive(root)
    for selected in selections:
        cid, mid = selected['case_id'], selected['message_id']
        candidate_ids = {m['message_id'] for m in cases[cid]['matches']}
        require(mid in candidate_ids, 'initial_message_not_in_pilot')
        require(mid == min(candidate_ids, key=lambda x: (inventory[x]['internal_ms'], x)),
                'not_earliest_pilot_candidate')
        meta = inventory[mid]
        require(not meta['missing_attachment_parts'], 'initial_attachments_incomplete')
        require(meta['internal_ms'] == selected['internal_ms'], 'cutoff_mismatch')
        rows = [s for s in extraction['sources'] if s['message_id'] == mid]
        originals = [s for s in rows if s['kind'] == 'message']
        require(len(originals) == 1, 'initial_original_missing')
        original = read_verified(archive, originals[0])
        parsed = parse_email(original)
        headers = {k.lower(): v for k, v in parsed['headers']}
        require(not headers.get('in-reply-to') and not headers.get('references'), 'reply_not_initial')
        require(selected['customer_email'].lower() in
                (headers.get('from', '') + ' ' + headers.get('reply-to', '')).lower(),
                'initial_sender_not_customer')
        require('office@elektrovienna.at' in headers.get('to', '').lower(), 'not_inbound_inquiry')
        require(not re.match(r'\s*(re|aw|wg|fw|fwd)\s*:', headers.get('subject', ''), re.I), 'forward_or_reply_not_initial')
        require(not parsed['issues'], 'initial_parser_issue_requires_review')
        mime = BytesParser(policy=policy.default).parsebytes(original)
        attached = Counter((part.get_filename(), digest(part.get_payload(decode=True) or b''))
                           for part in mime.walk() if part.get_filename())
        archived = Counter((row['filename'], row['sha256']) for row in rows if row['kind'] == 'attachment')
        require(attached == archived, 'attachment_not_in_initial_mime')
        # Verify every initial attachment, never fetch URLs in the message.
        for row in rows:
            read_verified(archive, row)
        inquiries.append({'case_id': cid, 'name': selected['name'], 'rationale': selected['rationale'],
                          'message_id': mid, 'internal_ms': meta['internal_ms'],
                          't0_utc': datetime.fromtimestamp(meta['internal_ms']/1000, timezone.utc).isoformat(),
                          'sections': parsed['sections'], 'sources': rows,
                          'airtable': [], 'attachment_interpretation': 'Initial bytes only; linked web images are not archived attachments.'})
    guidance = Path(guidance_path).read_text('utf-8')
    packet = {'format_version': 1, 'kind': 'blind_inquiry_packet',
              'protocol': plan['protocol'], 'selection_log': plan['selection_log'],
              'inquiries': inquiries, 'knowledge': cards,
              'guidance': {'text': guidance, **binding(guidance_path)},
              'inputs': {'pilot': binding(pilot_path), 'extraction': binding(extraction_path)},
              'processor_sha256': digest(Path(__file__).read_bytes())}
    run_id = digest(canonical(packet))
    EvalStore(root).publish(f'30_cases/blind_eval/{run_id}/packet.json', canonical(packet))
    return run_id


def packet_at(store, run_id):
    packet = load(store.path(f'30_cases/blind_eval/{run_id}/packet.json'))
    require(digest(canonical(packet)) == run_id, 'packet_changed')
    return packet


def allowed_citations(packet, inquiry):
    return ({f"T0:{s['locator']}" for s in inquiry['sections']}
            | {f"T0:attachment:{s['id']}" for s in inquiry['sources'] if s['kind'] == 'attachment'}
            | {f"{c['case_id']}:{f['id']}" for c in packet['knowledge'] for f in c['facts']}
            | {'guidance:NORTH_STAR'})


def freeze(store, run_id, answers):
    packet = packet_at(store, run_id)
    require(set(answers) == {c['case_id'] for c in packet['inquiries']}, 'all_three_answers_required')
    for inquiry in packet['inquiries']:
        answer = answers[inquiry['case_id']]
        require(set(answer) == {'sections', 'citations'}, 'answer_schema')
        require(set(answer['sections']) == set(SECTIONS), 'nine_sections_required')
        require(all(isinstance(t, str) and t.strip() for t in answer['sections'].values()), 'empty_answer')
        require(set(answer['citations']) == set(SECTIONS), 'citations_per_section_required')
        for refs in answer['citations'].values():
            require(isinstance(refs, list) and bool(refs) and set(refs) <= allowed_citations(packet, inquiry),
                    'citation_outside_blind_packet')
    reference = f'30_cases/blind_eval/{run_id}/frozen.json'
    if store.path(reference).exists():
        prior = load(store.path(reference))
        require(prior['answers'] == answers and prior['packet_sha256'] == run_id, 'frozen_answers_cannot_change')
        return prior
    record = {'kind': 'frozen_blind_answers', 'packet_sha256': run_id, 'frozen_at': now(),
              'answers': answers, 'answers_sha256': digest(canonical(answers))}
    store.publish(reference, canonical(record))
    return record


def frozen_at(store, run_id):
    packet = packet_at(store, run_id)
    path = store.path(f'30_cases/blind_eval/{run_id}/frozen.json')
    require(path.is_file(), 'answer_key_locked_until_all_answers_frozen')
    frozen = load(path)
    require(frozen['packet_sha256'] == run_id and digest(canonical(frozen['answers'])) == frozen['answers_sha256'],
            'frozen_integrity')
    require(set(frozen['answers']) == {c['case_id'] for c in packet['inquiries']}, 'incomplete_freeze')
    return packet, frozen


def reveal(store, run_id):
    # The gate runs before opening ANY answer-key input.
    packet, frozen = frozen_at(store, run_id)
    for entry in packet['inputs'].values():
        require(digest(Path(entry['path']).read_bytes()) == entry['sha256'], 'answer_key_input_changed')
    pilot = load(packet['inputs']['pilot']['path'])
    extraction = load(packet['inputs']['extraction']['path'])
    inventory = {m['message_id']: m for m in extraction['inventory_scope']}
    keys = []
    for inquiry in packet['inquiries']:
        case = next(c for c in pilot['cases'] if c['id'] == inquiry['case_id'])
        mids = {m['message_id'] for m in case['matches'] if inventory[m['message_id']]['internal_ms'] > inquiry['internal_ms']}
        sources = [s for s in extraction['sources'] if s['message_id'] in mids]
        for row in sources:
            read_verified(SourceArchive(store.root), row)
        keys.append({'case_id': case['id'], 'candidate_history_not_human_ground_truth': True,
                     'case': case, 'sources': sources,
                     'messages': [{'inventory': inventory[mid], **extraction['messages'][mid]}
                                  for mid in sorted(mids, key=lambda x: (inventory[x]['internal_ms'], x))],
                     'documents': {s['id']: extraction['documents'][s['id']] for s in sources if s['id'] in extraction['documents']}})
    result = {'kind': 'retrospective_answer_key', 'frozen_sha256': digest(canonical(frozen)),
              'frozen_at': frozen['frozen_at'], 'cases': keys}
    store.publish(f'30_cases/blind_eval/{run_id}/answer_key.json', canonical(result))
    return result


def paragraphs(value):
    return ''.join('<p>' + html.escape(p).replace('\n', '<br>') + '</p>' for p in value.split('\n\n'))


def evaluate(store, run_id, evaluation):
    packet, frozen = frozen_at(store, run_id)
    key = load(store.path(f'30_cases/blind_eval/{run_id}/answer_key.json'))
    require(key['frozen_sha256'] == digest(canonical(frozen)), 'answer_key_freeze_mismatch')
    require(set(evaluation['cases']) == set(frozen['answers']), 'evaluation_cases_mismatch')
    for result in evaluation['cases'].values():
        require(set(result['scores']) == set(DIMENSIONS), 'nine_scores_required')
        require(all(type(n) is int and 0 <= n <= 2 for n in result['scores'].values()), 'score_out_of_range')
        require(set(result['retrospective']) == set(RETRO), 'retrospective_sections_required')
        require(set(result['score_reasons']) == set(DIMENSIONS), 'score_reasons_required')
        require(isinstance(result['critical_failures'], list), 'critical_failures_required')
    record = {'kind': 'blind_retrospective_evaluation', 'packet_sha256': run_id,
              'frozen_sha256': digest(canonical(frozen)), 'answer_key_sha256': digest(canonical(key)),
              'evaluation': evaluation}
    store.publish(f'30_cases/blind_eval/{run_id}/evaluation.json', canonical(record))
    esc = html.escape
    page = ['<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
            '<title>Erster blinder Anfragetest – Elektro Vienna</title>',
            '<style>body{font:17px/1.6 system-ui;max-width:1050px;margin:40px auto;padding:0 24px;color:#20323c}h1,h2,h3{line-height:1.25}article{border-top:3px solid #25796e;margin-top:55px}details{background:#f0f5f4;padding:12px;margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 monospace}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #cbd9d5;text-align:left}a{color:#006956}.badge{background:#edf4f2;padding:10px}</style>',
            '<h1>Erster blinder Anfragetest</h1><p class="badge">SHADOW MODE · Keine Nachricht versendet · Keine CRM-Änderung</p>',
            paragraphs(evaluation['method']), '<h2>Ergebnis</h2><table><tr><th>Fall</th><th>T0 UTC</th><th>Punkte / 18</th></tr>']
    for item in packet['inquiries']:
        total = sum(evaluation['cases'][item['case_id']]['scores'].values())
        page.append(f'<tr><td><a href="#{esc(item["case_id"])}">{esc(item["name"])}</a></td><td>{esc(item["t0_utc"])}</td><td>{total}</td></tr>')
    page += ['</table><h2>Wichtigste Erkenntnisse</h2>', paragraphs(evaluation['lessons']),
             '<details><summary>Auswahl und Ausschlüsse</summary>', paragraphs(packet['selection_log']), '</details>',
             '<details><summary>Methodik und Grenzen vor dem Blindtest</summary>', paragraphs(packet['protocol']),
             f'<p>Freeze: {esc(frozen["frozen_at"])}<br>SHA-256 der Antworten: {frozen["answers_sha256"]}</p></details>']
    for item in packet['inquiries']:
        cid = item['case_id']; result = evaluation['cases'][cid]
        page += [f'<article id="{esc(cid)}"><h2>{esc(item["name"])}</h2><p>{esc(cid)} · {esc(item["rationale"])}</p>',
                 f'<p>T0: {esc(item["t0_utc"])} · Gmail {esc(item["message_id"])}<br>Nur diese Nachricht und ihre {sum(s["kind"] == "attachment" for s in item["sources"])} Anhänge; keine Airtable-Felder.</p>',
                 '<details open><summary>Originalanfrage am T0</summary><pre>',
                 esc('\n\n'.join(s['text'] for s in item['sections'])), '</pre></details>',
                 '<h3>Unveränderlich gespeicherte Blindantwort</h3>']
        for section, label in zip(SECTIONS, LABELS):
            page += [f'<h3>{label}</h3>', paragraphs(frozen['answers'][cid]['sections'][section])]
        page += ['<details><summary>Belege der Blindantwort und Originalanhänge</summary>']
        for row in item['sources']:
            link = (store.root / row['reference']).as_uri()
            page.append(f'<p><a href="{esc(link, quote=True)}">{esc(row.get("filename") or "Original-E-Mail")}</a> · {esc(row["id"])}</p>')
        for section, label in zip(SECTIONS, LABELS):
            page.append('<p><b>' + label + '</b>: ' + esc('; '.join(frozen['answers'][cid]['citations'][section])) + '</p>')
        for row in item['sources']:
            if row['kind'] == 'attachment' and row['mime_type'] in {'image/jpeg', 'image/png'}:
                data = base64.b64encode(read_verified(SourceArchive(store.root), row)).decode('ascii')
                page.append(f'<p>{esc(row["filename"])}</p><img alt="T0-Anhang" style="max-width:100%;max-height:650px" src="data:{row["mime_type"]};base64,{data}">')
        page.append('</details>')
        page += ['<h3>Retrospektive – erst nach dem Freeze gelesen</h3>']
        for section, label in zip(RETRO, RETRO_LABELS):
            page += [f'<h3>{label}</h3>', paragraphs(result['retrospective'][section])]
        page += ['<table><tr><th>Kriterium</th><th>0–2</th><th>Begründung</th></tr>']
        for dimension in DIMENSIONS:
            page.append(f'<tr><td>{esc(dimension)}</td><td>{result["scores"][dimension]}</td><td>{esc(result["score_reasons"][dimension])}</td></tr>')
        page += ['</table><p>Kritische Fehler: ' + esc('; '.join(result['critical_failures']) or 'Keine festgestellt.') + '</p>',
                 '<details><summary>Spätere Quellen – Originalnachrichten und extrahierte Dokumente</summary>']
        case_key = next(k for k in key['cases'] if k['case_id'] == cid)
        for message in case_key['messages']:
            mid = message['inventory']['message_id']
            row = next(s for s in case_key['sources'] if s['message_id'] == mid and s['kind'] == 'message')
            page.append(f'<h4>Gmail {esc(mid)}</h4><a href="{esc((store.root / row["reference"]).as_uri(), quote=True)}">Originalnachricht</a><pre>' + esc('\n\n'.join(s['text'] for s in message['sections'])) + '</pre>')
        for sid, doc in case_key['documents'].items():
            row = next(s for s in case_key['sources'] if s['id'] == sid)
            if doc['sections']:
                page.append(f'<h4>{esc(row["filename"] or sid)}</h4><a href="{esc((store.root / row["reference"]).as_uri(), quote=True)}">Originaldokument</a><pre>' + esc('\n\n'.join(s['text'] for s in doc['sections'])) + '</pre>')
        page += ['</details></article>']
    page += ['<h2>Die fünf zugelassenen Erfahrungsfälle</h2>']
    for card in packet['knowledge']:
        comparison = (store.root / card['reference']).with_name('comparison.html').as_uri()
        page += [f'<details><summary>{esc(card["case_id"])}</summary><a href="{esc(comparison, quote=True)}">Geprüfte Fallrevision öffnen</a>']
        for fact in card['facts']:
            page += [f'<p><b>{esc(fact["title"])}</b> [{esc(fact["id"])}; {esc(fact["certainty"])}]<br>{esc(fact["text"])}</p>']
        page += ['</details>']
    page += ['</html>']
    ref = f'30_cases/blind_eval/{run_id}/overview.html'
    store.publish(ref, ''.join(page).encode('utf-8'))
    return str(store.path(ref))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('prepare', 'freeze', 'reveal', 'evaluate'))
    parser.add_argument('--input', type=Path)
    parser.add_argument('--run')
    parser.add_argument('--guidance', type=Path, default=Path('docs/NORTH_STAR.md'))
    args = parser.parse_args()
    store = EvalStore()
    if args.stage == 'prepare':
        print(prepare(store.root, load(args.input), args.guidance))
    elif args.stage == 'freeze':
        print(freeze(store, args.run, load(args.input))['answers_sha256'])
    elif args.stage == 'reveal':
        result = reveal(store, args.run)
        print('Answer key released for', len(result['cases']), 'cases; frozen at', result['frozen_at'])
    else:
        print(evaluate(store, args.run, load(args.input)))


if __name__ == '__main__':
    main()
