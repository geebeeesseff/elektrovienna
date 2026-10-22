import copy
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from elektro_vienna import pending_replies as pr
from elektro_vienna.operational_replies import OperationalEvidence, STATES
from elektro_vienna.pilot_sources import canonical
from test_pending_replies import env, configure, message, suggestion, NOW


def snapshot(status='Anfrage', tickets=1):
    fields = {'tickets': ['Name', 'Customer', 'Craftsman', 'Service type', 'Gewerk', 'Status',
                         'Customer request', 'Notes', 'Customer Request Date', 'Ticket volume (net)', 'Closed Date'],
              'customers': ['Name', 'Email', 'Adress'], 'craftsmen': ['Name', 'Email']}
    names = {'tickets': 'Tickets (main)', 'customers': 'Customers', 'craftsmen': 'Craftsmen'}
    data = {'format_version': 1, 'base_id': 'appeNai0w4Umr4Tm8', 'captured_at': NOW.isoformat(),
            'capture_method': 'synthetic read-only test',
            'schema': {'tables': [{'id': k, 'name': names[k], 'fields': [{'id': f, 'name': f} for f in v]}
                                  for k, v in fields.items()]},
            'query_scope': {'complete': True, 'candidate_thread_ids': ['t1']},
            'customers': [{'id': 'rec00000000000001', 'cellValuesByFieldId': {'Name': 'Customer', 'Email': 'customer@example.invalid'}}],
            'craftsmen': [], 'tickets': []}
    for i in range(tickets):
        data['tickets'].append({'id': f'rec{i+2:014}', 'cellValuesByFieldId': {'Name': 'Customer - Herdanschluss',
            'Customer': [{'id': 'rec00000000000001'}], 'Customer request': 'Herdanschluss in Neubau', 'Status': {'name': status}}})
    return data


def setup(env, status='Anfrage', tickets=1, data=None):
    store, reader, state = env
    configure(env, message('a', text='Was kostet der Herdanschluss im Neubau?'))
    path = state.parent / 'airtable.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(data or snapshot(status, tickets)))
    op = OperationalEvidence(store, state.with_name('operational.json'), snapshot_path=path, now=NOW)
    raw = reader.get_thread.return_value
    messages = pr.conversation(raw)
    _, _, decision = pr.queue_decision(messages)
    decision['thread_id'] = 't1'
    packet = {'thread_id': 't1', 'messages': messages, 'queue_decision': decision,
              'source_reference': store.document('input', {'gmail_thread': raw})}
    return op, packet


def owner(op, packet, state, note='Owner fact'):
    classification = op.classify(packet)
    item = {'thread_id': 't1', 'state': state, 'note': note, 'source': 'Giovanni', 'recorded_at': NOW.isoformat(),
            'ticket_id': classification['ticket_id'], 'evidence_key': classification['evidence_key']}
    op.preserve_owner_items([item], canonical({'synthetic_owner_request': item}))


def run(env, op, model=None):
    store, reader, state = env
    return pr.reconcile(reader, store, state, {}, now=NOW, suggest=model or suggestion, operational=op)


def test_closed_airtable_no_generation_no_original_fetch(env):
    op, packet = setup(env, 'Abgeschlossen')
    model = Mock(side_effect=AssertionError('must not generate'))
    result = run(env, op, model)
    assert result['operational_counts']['CLOSED_OR_LOST'] == 1
    model.assert_not_called()
    env[1].get_original.assert_not_called()
    assert 'Copy response' not in Path(result['review_path']).read_text('utf-8')


@pytest.mark.parametrize('name,state,note', [
    ('Nelida Székely', 'CLOSED_OR_LOST', 'Completed; technician took over directly.'),
    ('Lisa Haas', 'CLOSED_OR_LOST', 'Customer cancelled by phone; WhatsApp communication.'),
    ('Daniel Zafirakis', 'HANDLED_ELSEWHERE', 'Bilateral communication with Mario Kristo.'),
    ('Carina Meltsch / DI Czerny / Alsegger Straße', 'WAITING_INTERNAL', 'Waiting for Sollinger.'),
    ('Gudrun Sturmann', 'HANDLED_ELSEWHERE', 'Mario Kristo handled this.'),
])
def test_owner_acceptance_examples_are_data_not_case_logic(env, name, state, note):
    op, packet = setup(env, 'Abgeschlossen')
    packet['queue_decision']['customer'] = name
    owner(op, packet, state, note)
    # Changing display name must not change the outcome; binding is evidence + thread.
    packet['queue_decision']['customer'] = 'Another display name'
    classified = op.classify(packet)
    assert classified['state'] == state and classified['owner_applied']
    if state == 'WAITING_INTERNAL':
        assert classified['conflicts'] and 'Sollinger' in classified['waiting_on']
    result = run(env, op, Mock(side_effect=AssertionError('must not generate')))
    assert result['operational_counts'][state] == 1
    assert 'Copy response' not in Path(result['review_path']).read_text('utf-8')


def test_open_matching_unresolved_question_gets_suggestion(env):
    op, _ = setup(env)
    result = run(env, op)
    assert result['suggestions_generated'] == 1
    assert result['operational_counts']['NEEDS_YOUR_REPLY'] == 1
    assert 'Copy response' in Path(result['review_path']).read_text('utf-8')


@pytest.mark.parametrize('tickets', [0, 2])
def test_no_forced_or_multiple_project_match(env, tickets):
    op, packet = setup(env, tickets=tickets)
    result = op.classify(packet)
    assert result['state'] == 'UNCERTAIN' and result['ticket_id'] is None
    assert len(result['matches']) == tickets
    assert run(env, op)['suggestion_calls'] == 0


def test_customer_email_alone_does_not_establish_project(env):
    data = snapshot()
    data['tickets'][0]['cellValuesByFieldId'].update({'Name': 'Different project', 'Customer request': 'Wallbox garage'})
    op, packet = setup(env, data=data)
    assert op.classify(packet)['state'] == 'UNCERTAIN'


def test_feedback_export_import_persists_and_bound_to_batch(env):
    op, _ = setup(env)
    result = run(env, op)
    data = {'kind': 'operational_feedback', 'source': 'Giovanni', 'recorded_at': NOW.isoformat(),
            'batch_reference': result['batch_reference'],
            'items': [{'thread_id': 't1', 'state': 'HANDLED_ELSEWHERE', 'note': 'Technician handled this.'}]}
    ref = op.import_feedback(canonical(data))
    assert op.import_feedback(canonical(data)) == ref
    restored = OperationalEvidence(env[0], op.path, now=NOW)
    result = run(env, restored)
    assert result['operational_counts']['HANDLED_ELSEWHERE'] == 1
    assert result['suggestion_calls'] == 0
    assert len(restored.state['owner_reviews']) == 1
    assert env[0].read(result['batch_reference'])['rows'][0]['decision']['suggested_response'] == ''
    assert len(json.loads(env[2].read_bytes())['cache']) == 1  # cached original preserved


@pytest.mark.parametrize('change', ['incoming', 'outgoing', 'airtable'])
def test_new_evidence_invalidates_owner_without_erasing_it(env, change):
    op, packet = setup(env, 'Abgeschlossen')
    owner(op, packet, 'HANDLED_ELSEWHERE')
    if change == 'airtable':
        op.source.data['tickets'][0]['cellValuesByFieldId']['Status'] = {'name': 'Anfrage'}
    else:
        raw = copy.deepcopy(env[1].get_thread.return_value)
        raw['messages'].append(message('new', offset=1000, sender=pr.MAILBOX if change == 'outgoing' else 'customer@example.invalid')[0])
        packet['source_reference'] = env[0].document('input', {'gmail_thread': raw})
    result = op.classify(packet)
    assert result['state'] == 'UNCERTAIN' and result['owner_evidence'] and not result['owner_applied']


def test_read_state_does_not_invalidate_owner(env):
    op, packet = setup(env)
    owner(op, packet, 'CLOSED_OR_LOST')
    raw = copy.deepcopy(env[1].get_thread.return_value)
    raw['messages'][0]['labelIds'] = ['UNREAD', 'INBOX']
    packet['source_reference'] = env[0].document('input', {'gmail_thread': raw})
    assert op.classify(packet)['owner_applied']


def test_new_customer_after_closed_recommendation_reopens_review(env):
    op, packet = setup(env, 'Abgeschlossen')
    op.remember([{'thread_id': 't1', 'operational': op.classify(packet)}])
    raw = copy.deepcopy(env[1].get_thread.return_value)
    raw['messages'].append(message('new', offset=1000)[0])
    packet['source_reference'] = env[0].document('input', {'gmail_thread': raw})
    assert op.classify(packet)['state'] == 'UNCERTAIN'


def test_message_after_actual_closed_date_never_silently_suppressed(env):
    data = snapshot('Abgeschlossen')
    data['tickets'][0]['cellValuesByFieldId']['Closed Date'] = '2026-10-19'
    op, packet = setup(env, data=data)
    assert op.classify(packet)['state'] == 'UNCERTAIN'


def test_stale_or_out_of_scope_snapshot_never_infers(env):
    op, packet = setup(env)
    op.now = NOW + timedelta(days=2)
    stale = OperationalEvidence(env[0], op.path, now=op.now)
    assert stale.classify(packet)['state'] == 'UNCERTAIN'
    op.source.data['query_scope']['candidate_thread_ids'] = []
    assert op.classify(packet)['state'] == 'UNCERTAIN'


def test_current_candidates_mode_does_not_enumerate_mailbox(env):
    op, _ = setup(env)
    run(env, op)
    env[1].list_threads.reset_mock()
    pr.reconcile(env[1], env[0], env[2], {}, now=NOW, suggest=suggestion, operational=op, current_candidates_only=True)
    env[1].list_threads.assert_not_called()


def test_only_needs_reply_state_reaches_inference(env):
    for state in STATES:
        op, packet = setup(env)
        owner(op, packet, state)
        model = Mock(side_effect=suggestion)
        result = run(env, op, model)
        assert model.call_count == (1 if state == 'NEEDS_YOUR_REPLY' else 0)
        assert result['operational_counts'][state] == 1


@pytest.mark.parametrize('state', ['WAITING_CUSTOMER', 'NO_ACTION_REQUIRED'])
def test_inactive_owner_state_persists_without_draft(env, state):
    op, packet = setup(env)
    owner(op, packet, state, 'Owner confirms no current action.')
    restored = OperationalEvidence(env[0], op.path, now=NOW)
    model = Mock(side_effect=AssertionError('No reply should be generated'))
    result = run(env, restored, model)
    assert result['operational_counts'][state] == 1
    model.assert_not_called()
    page = Path(result['review_path']).read_text('utf-8')
    assert 'Copy response' not in page
    if state == 'NO_ACTION_REQUIRED':
        assert '<details><summary>NO ACTION REQUIRED · 1' in page
    else:
        assert '<h2>WAITING CUSTOMER · 1</h2>' in page


@pytest.mark.parametrize('state', ['WAITING_CUSTOMER', 'NO_ACTION_REQUIRED'])
def test_substantive_customer_message_reopens_inactive_owner_state(env, state):
    op, packet = setup(env)
    owner(op, packet, state)
    original = op.owners['t1']['reference']
    configure(env, message('a', text='Was kostet der Herdanschluss im Neubau?'),
              message('new-question', text='Wann können Sie den Herdanschluss durchführen?', offset=1000))
    result = run(env, op)
    row = env[0].read(result['batch_reference'])['rows'][0]
    assert row['operational']['state'] == 'UNCERTAIN'
    assert not row['operational']['owner_applied']
    assert row['operational']['owner_evidence']['reference'] == original
    assert row['operational']['owner_evidence']['state'] == state
