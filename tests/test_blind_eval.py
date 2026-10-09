"""Synthetic future canaries test the inquiry boundary; no customer fixtures."""

import copy
import json
from email.message import EmailMessage

import pytest

from elektro_vienna import archive_store, blind_eval as b
from elektro_vienna.archive_store import SourceArchive, source_reference
from elektro_vienna.pilot_sources import canonical, digest


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    root = tmp_path / 'kb'
    monkeypatch.setattr(archive_store, 'AUTHORIZED_ROOT', root)
    monkeypatch.setattr(archive_store, 'local_app_data', lambda: tmp_path / 'private')
    archive = SourceArchive()
    extraction = {'sources': [], 'messages': {}, 'inventory_scope': [], 'documents': {}}
    cases, selection = [], []
    for i in range(3):
        cid = 'rec' + str(i) * 14
        matches = []
        for future in (False, True):
            mid = f'{i}-{future}'
            message = EmailMessage()
            message['From'] = f'customer{i}@example.invalid'
            message['To'] = 'office@elektrovienna.at'
            message['Subject'] = 'Initial inquiry' if not future else 'FUTURE_INVOICE'
            message.set_content('Please install a socket.' if not future else 'SECRET_FUTURE_PAYMENT_777')
            published = archive.publish(source_reference('office@elektrovienna.at', mid), message.as_bytes())
            row = dict(id=mid, message_id=mid, kind='message', filename=None, mime_type=None,
                       reference=published.reference, sha256=published.sha256, byte_length=published.byte_length)
            extraction['sources'].append(row)
            extraction['inventory_scope'].append(dict(message_id=mid, thread_id=f'thread{i}',
                internal_ms=1000 + 1000*future, missing_attachment_parts=[]))
            extraction['messages'][mid] = {'source_id': mid, **b.parse_email(message.as_bytes())}
            matches.append({'message_id': mid})
        cases.append(dict(id=cid, matches=matches, outcome='SECRET_CRM_EXECUTED', notes='SECRET_LATE_CLARIFICATION'))
        selection.append(dict(case_id=cid, message_id=f'{i}-False', internal_ms=1000,
                              customer_email=f'customer{i}@example.invalid', name=f'Customer {i}', rationale='Synthetic coverage'))
    refs = []
    for cid in sorted(b.REVIEWED):
        ref = f'reviewed/{cid}.json'
        path = root / ref; path.parent.mkdir(exist_ok=True)
        revision = dict(kind='reviewed_case_revision', case_id=cid, completed_review={'status': 'completed'},
            before_review='SECRET_OTHER_RAW_BASELINE', crm_observations='SECRET_CRM',
            facts={g: [] for g in ('state', 'technical', 'commercial', 'open_questions')})
        revision['facts']['technical'] = [dict(id='known', title='Reviewed fact', text='Existing work matters.', certainty='reviewed_source', basis=[]),
            dict(id='leak', title='Cross-case leakage', text='SECRET_OVERLAP', certainty='reviewed_source', basis=[{'source_id': '0-True'}])]
        path.write_bytes(canonical(revision)); refs.append(ref)
    plan = dict(pilot_reference='pilot.json', selection=selection, reviewed_references=refs,
                protocol='Synthetic protocol', selection_log='Synthetic selection')
    guidance = tmp_path / 'guidance.md'; guidance.write_text('Human checks drafts.')

    def save():
        ext_ref = digest(canonical(extraction)) + '.json'
        (root / ext_ref).write_bytes(canonical(extraction))
        (root / 'pilot.json').write_bytes(canonical({'cases': cases, 'extraction_reference': ext_ref}))
    save()
    return root, plan, guidance, extraction, cases, save


def ready(experiment):
    root, plan, guidance, *_ = experiment
    run = b.prepare(root, plan, guidance)
    store = b.EvalStore()
    answers = {i['case_id']: {'sections': {s: '<script>Not sent</script>' for s in b.SECTIONS},
                              'citations': {s: ['guidance:NORTH_STAR'] for s in b.SECTIONS}}
               for i in plan['selection']}
    return store, run, answers


def test_future_crm_and_other_case_provenance_cannot_enter_packet(experiment):
    store, run, _ = ready(experiment)
    text = store.path(f'30_cases/blind_eval/{run}/packet.json').read_text('utf-8')
    assert 'SECRET_' not in text and 'FUTURE_INVOICE' not in text
    packet = b.packet_at(store, run)
    assert all(not i['airtable'] and len(i['sources']) == 1 for i in packet['inquiries'])
    assert all(c['excluded_overlap_facts'] == 1 for c in packet['knowledge'])


def test_key_gate_precedes_any_answer_key_file_access(experiment):
    store, run, _ = ready(experiment)
    (store.root / 'pilot.json').unlink()
    with pytest.raises(ValueError, match='locked_until'):
        b.reveal(store, run)


@pytest.mark.parametrize('mutation,error', [('late','not_earliest'), ('time','cutoff_mismatch'),
                                         ('reviewed','heldout'), ('sender','sender_not_customer')])
def test_selection_rejects_wrong_boundary(experiment, mutation, error):
    root, plan, guidance, *_ = experiment
    if mutation == 'late': plan['selection'][0]['message_id'] = '0-True'
    if mutation == 'time': plan['selection'][0]['internal_ms'] = 999999
    if mutation == 'reviewed': plan['selection'][0]['case_id'] = next(iter(b.REVIEWED))
    if mutation == 'sender': plan['selection'][0]['customer_email'] = 'wrong@example.invalid'
    with pytest.raises(ValueError, match=error):
        b.prepare(root, plan, guidance)


def test_future_attachment_cannot_be_laundered_into_initial_message(experiment):
    root, plan, guidance, extraction, _, save = experiment
    # Even a forged row claiming the initial parent must correspond to MIME bytes.
    row = copy.deepcopy(extraction['sources'][0])
    row.update(id='forged', kind='attachment', filename='future.pdf', mime_type='application/pdf')
    extraction['sources'].append(row); save()
    with pytest.raises(ValueError, match='attachment_not_in_initial_mime'):
        b.prepare(root, plan, guidance)


def test_unreviewed_knowledge_and_draft_rejected(experiment):
    root, plan, guidance, *_ = experiment
    path = root / plan['reviewed_references'][0]; rev = b.load(path)
    rev['completed_review']['status'] = 'draft'; path.write_bytes(canonical(rev))
    with pytest.raises(ValueError, match='not_completed'): b.prepare(root, plan, guidance)
    rev['completed_review']['status'] = 'completed'; rev['case_id'] = 'recUNREVIEWED00000'; path.write_bytes(canonical(rev))
    with pytest.raises(ValueError, match='unreviewed_knowledge'): b.prepare(root, plan, guidance)


def test_freeze_requires_all_cases_and_only_blind_citations(experiment):
    store, run, answers = ready(experiment)
    partial = copy.deepcopy(answers); partial.pop(next(iter(partial)))
    with pytest.raises(ValueError, match='all_three'): b.freeze(store, run, partial)
    answers[next(iter(answers))]['citations']['known'] = ['future:invoice']
    with pytest.raises(ValueError, match='outside_blind'): b.freeze(store, run, answers)


def test_immutable_freeze_replay_and_post_freeze_gate(experiment):
    store, run, answers = ready(experiment)
    first = b.freeze(store, run, answers)
    assert b.freeze(store, run, answers) == first
    key = b.reveal(store, run)
    assert all('SECRET_FUTURE_PAYMENT_777' in str(c) for c in key['cases'])
    assert all(all(m['inventory']['internal_ms'] > 1000 for m in c['messages']) for c in key['cases'])
    assert b.reveal(store, run) == key
    changed = copy.deepcopy(answers); changed[next(iter(answers))]['sections']['draft'] = 'Rewritten after future'
    with pytest.raises(ValueError, match='cannot_change'): b.freeze(store, run, changed)


def test_packet_and_key_inputs_are_hash_bound(experiment):
    store, run, answers = ready(experiment); b.freeze(store, run, answers)
    with (store.root / 'pilot.json').open('a') as f: f.write(' ')
    with pytest.raises(ValueError, match='input_changed'): b.reveal(store, run)
    path = store.path(f'30_cases/blind_eval/{run}/packet.json')
    packet = b.load(path); packet['guidance']['text'] = 'tampered'; path.write_bytes(canonical(packet))
    with pytest.raises(ValueError, match='packet_changed'): b.frozen_at(store, run)


def test_report_preserves_freeze_escapes_source_and_scores_without_side_effects(experiment, monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('Network forbidden'))
    root, *_ = experiment
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    store, run, answers = ready(experiment); b.freeze(store, run, answers); b.reveal(store, run)
    result = dict(method='Synthetic retrospective', lessons='Synthetic lessons', cases={cid: dict(
        scores={d: 1 for d in b.DIMENSIONS}, score_reasons={d: 'Reason' for d in b.DIMENSIONS},
        retrospective={d: 'Evidence' for d in b.RETRO}, critical_failures=[]) for cid in answers})
    output = b.evaluate(store, run, result)
    assert b.evaluate(store, run, result) == output
    page = b.Path(output).read_text('utf-8')
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert b.frozen_at(store, run)[1]['answers'] == answers
    result['cases'][next(iter(answers))]['scores']['need'] = 3
    with pytest.raises(ValueError, match='score_out_of_range'): b.evaluate(store, run, result)


@pytest.mark.parametrize('ref', ['00_raw/gmail/x.eml', '../overview.html', '30_cases/blind_eval/x/packet.json'])
def test_evaluation_store_cannot_overwrite_sources(experiment, ref):
    with pytest.raises(ValueError, match='path_invalid'): b.EvalStore().path(ref)
