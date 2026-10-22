import copy
import json
import socket
from datetime import datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import Mock

import pytest

from elektro_vienna import archive_store, shadow_input as si, shadow_cards as sc, shadow_store as ss, gmail
from elektro_vienna.models import Message, OriginalMessage, Page, HistoryPage, ProviderError
from elektro_vienna.models import Attachment
from elektro_vienna.pilot_sources import canonical, digest

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'archive'; private = tmp_path / 'private'
    monkeypatch.setattr(archive_store, 'AUTHORIZED_ROOT', root)
    monkeypatch.setattr(archive_store, 'local_app_data', lambda: private)
    monkeypatch.setattr(si, 'local_app_data', lambda: private)
    monkeypatch.setattr(si, 'validate_path', lambda path, _: path)
    monkeypatch.setattr(socket, 'create_connection', Mock(side_effect=AssertionError('network forbidden')))
    store = ss.ShadowStore()
    validation = store.document('result', {'kind': 'owner_final_validation', 'approved': 3}, review=True)
    reader = Mock(spec=['mailbox', 'history_checkpoint', 'list_messages', 'list_history', 'get_message', 'get_original', 'get_attachment'])
    reader.mailbox.return_value = si.MAILBOX
    reader.history_checkpoint.return_value = '100'
    reader.list_messages.return_value = Page(('a',), None)
    reader.list_history.return_value = HistoryPage((), None, '110')
    mail = EmailMessage()
    mail['From'] = 'Ada Test <ada@example.invalid>'
    mail['To'] = si.MAILBOX
    mail['Subject'] = 'Anfrage Lampenmontage <script>bad()</script>'
    mail.set_content('Bitte eine Lampe montieren.\n')
    reader.get_message.side_effect = lambda mid: Message(mid, 'thread-' + mid, MS, (), (), ())
    reader.get_original.side_effect = lambda mid: OriginalMessage(mid, 'thread-' + mid, MS, mail.as_bytes())
    return store, reader, private / 'checkpoint.json', tmp_path / 'absent.sqlite', si.guidance(REPO), validation


def poll(env, **kwargs):
    store, reader, checkpoint, historic, contexts, validation = env
    return si.check(reader, store, checkpoint, historic, contexts, now=NOW, validation_reference=validation, **kwargs)


def proposal(ref, item):
    customer = copy.deepcopy(item['customer'])
    return {'input_reference': ref, 'classification': 'new_customer_inquiry',
            'classification_reason': 'Explicit installation request.', 'customer': customer,
            'need': 'Lampe montieren.', 'known': ['Eine Lampe.'], 'blocking_customer_information': [],
            'internal_checks': ['Befestigung prüfen.'],
            'commercial': {'kind': 'current_standard_price', 'standard_items': ['single_first_hour_travel'],
                           'estimate_lines': [], 'material_assumption': 'Noch nicht geklärt.', 'travel': 'In erster Stunde enthalten.',
                           'vat': 'Zuzüglich anwendbarer Umsatzsteuer; Behandlung prüfen.',
                           'pricing_decision': 'Scope prüfen.', 'explanation': 'Aktuelle Standardrate; keine Festpreisgarantie.'},
            'history': {'reference': None, 'why_useful': '', 'lesson': 'No useful reviewed comparison.'},
            'next_action': 'Adresse und Verfügbarkeit erfragen.',
            'draft': 'Guten Tag,\n\ndie erste Stunde inklusive Anfahrt kostet EUR 150 netto.\n\n' + sc.SIGNOFF,
            'crm_proposal': {'kind': 'proposal_only', 'customer_account': customer, 'project': 'Lampenmontage',
                             'address': None, 'service_type': 'Installation', 'request_summary': 'Eine Lampe',
                             'status': 'inquiry', 'missing_information': ['Adresse'],
                             'commercial_indication': 'Aktuelle Standardrate, Umfang unbestätigt.',
                             'next_action': 'Anfrage prüfen.', 'matches': []},
            'case_update': None, 'basis': [item['sources'][0]['sha256'], 'price_book', 'playbook']}


def prepared(env):
    state, _ = poll(env)
    ref = state['done']['a']; item = env[0].read(ref)
    return state, proposal(ref, item)


def test_incremental_today_then_history_and_no_unread_dependency(env):
    state, result = poll(env)
    assert result['processed'] == 1 and state['phase'] == 'history'
    assert env[1].list_messages.call_args.args[0] == int(datetime(2026, 10, 14, 22, tzinfo=timezone.utc).timestamp()*1000)
    env[1].list_history.return_value = HistoryPage(('a', 'b'), None, '130')
    state, result = poll(env)
    assert result['processed'] == 1 and set(state['done']) == {'a', 'b'} and state['history_id'] == '130'
    assert env[1].list_messages.call_count == 1
    assert env[1].list_history.call_args.args[0] == '100'


def test_durable_page_failure_resume_and_no_checkpoint_loss(env):
    reader = env[1]; reader.list_messages.return_value = Page(('a', 'b'), 'page2')
    original = reader.get_original.side_effect
    reader.get_original.side_effect = lambda mid: (_ for _ in ()).throw(ProviderError('temporary')) if mid == 'b' else original(mid)
    with pytest.raises(ProviderError): poll(env)
    state = json.loads(env[2].read_bytes())
    assert list(state['done']) == ['a'] and state['page']['pending'] == ['b'] and state['history_id'] == '100'
    reader.get_original.side_effect = original
    poll(env)
    assert reader.list_messages.call_count == 1
    state = json.loads(env[2].read_bytes()); assert state['page_token'] == 'page2'


def test_limit_pagination_and_history_advance_only_after_durable_processing(env):
    poll(env)
    env[1].list_history.return_value = HistoryPage(('b', 'c'), None, '150')
    state, _ = poll(env, limit=1)
    assert state['history_id'] == '100' and state['page']['pending'] == ['c']
    state, _ = poll(env, limit=1)
    assert state['history_id'] == '150'


@pytest.mark.parametrize('label', ['SPAM', 'TRASH', 'DRAFT'])
def test_excluded_labels_never_archive(env, label):
    env[1].get_message.return_value = None
    env[1].get_message.side_effect = lambda mid: Message(mid, 'thread-a', MS, (), (label,), ())
    state, _ = poll(env)
    assert state['done']['a'] is None
    env[1].get_original.assert_not_called()


def test_bootstrap_exact_timestamp_and_sent_separation(env):
    env[1].list_messages.return_value = Page(('old', 'sent'), None)
    env[1].get_message.side_effect = lambda mid: Message(mid, 'thread-' + mid, MS-86400000 if mid == 'old' else MS, (), ('SENT',), ())
    state, _ = poll(env)
    assert state['done']['old'] is None
    item = env[0].read(state['done']['sent'])
    assert item['direction'] == 'sent' and item['classification'] == 'non_customer'


def test_history_expiry_stops_without_full_resync(env):
    poll(env); before = env[2].read_bytes()
    env[1].list_history.side_effect = ProviderError('shadow_history_expired_no_automatic_resync')
    with pytest.raises(ProviderError): poll(env)
    assert env[2].read_bytes() == before and env[1].list_messages.call_count == 1


def test_wix_labelled_customer_identity_and_missing_values():
    headers = {'from': 'Elektro Vienna <automations@wix.com>'}
    customer, basis = si.identity(headers, 'Name: Ada\nE-Mail: ada@example.invalid\nTelefon: 123\n', 'mime:0:decoded-text')
    assert basis == 'wix_form_source_claim' and customer['email']['value'] == 'ada@example.invalid'
    assert customer['address']['value'] is None and customer['company']['value'] is None
    unresolved, basis = si.identity(headers, 'Mail sent by automations@wix.com', 'mime:0:decoded-text')
    assert basis == 'wix_identity_unresolved' and unresolved['email']['value'] is None


def test_uncertain_is_not_forced_into_customer_case():
    c = {'email': si.field('x@example.invalid', 'header:from', 'x@example.invalid')}
    assert si.classify({}, 'Hello.', 'incoming', c, 'envelope_source_claim', [])[0] == 'uncertain'
    assert si.classify({'list-id': 'list'}, 'Angebot', 'incoming', c, 'envelope_source_claim', [])[0] == 'non_customer'


def test_publish_local_card_idempotent_escaped_page_and_feedback(env):
    state, plan = prepared(env); store = env[0]
    card = sc.publish_card(store, plan, REPO)
    assert sc.publish_card(store, plan, REPO) == card
    state['cards'][plan['input_reference']] = card
    result = sc.render_batch(store, state)
    page = Path(result['review_path']).read_text('utf-8')
    assert '&lt;script&gt;bad()&lt;/script&gt;' in page and '<script>bad()' not in page
    assert 'connect-src \'none\'' in page and page.count('type="radio"') == 4
    review = {'kind': 'live_shadow_feedback', 'format_version': 1, 'batch': result['batch_reference'].split('/')[-2],
              'reviewer': 'Giovanni Superti-Furga', 'recorded_at': NOW.isoformat(),
              'items': [{'input_reference': plan['input_reference'], 'card_reference': card,
                         'choice': 'sendable', 'comment': 'Exact typo\n'}]}
    raw = json.dumps(review, indent=4).encode()
    ref = sc.import_feedback(store, raw)
    assert sc.import_feedback(store, raw) == ref
    assert store.path(store.read(ref)['original_reference']).read_bytes() == raw
    assert store.read(card)['external_actions'] == []


@pytest.mark.parametrize('bad', ['historical_price', 'draft_price', 'footer', 'missing', 'merge', 'crm_write', 'fixed', 'wrong_source'])
def test_prohibited_semantics_fail_closed(env, bad):
    _, plan = prepared(env)
    if bad == 'historical_price': plan['commercial']['standard_items'] = ['old_lamp_price']
    if bad == 'draft_price': plan['draft'] = plan['draft'].replace('150', '80')
    if bad == 'footer': plan['draft'] += '\nwww.elektrovienna.at'
    if bad == 'missing': plan['customer']['address'] = si.field('Invented address')
    if bad == 'merge': plan['crm_proposal']['matches'] = [{'status': 'confirmed', 'basis': 'same email'}]
    if bad == 'crm_write': plan['crm_proposal']['kind'] = 'update_record'
    if bad == 'fixed': plan['commercial']['kind'] = 'fixed_price'
    if bad == 'wrong_source': plan['basis'] = ['not-a-current-source']
    with pytest.raises((ValueError, TypeError)): sc.publish_card(env[0], plan, REPO)


def test_same_customer_is_only_match_proposal(env):
    store, reader, *_ = env
    state, _ = poll(env)
    reader.list_history.return_value = HistoryPage(('b',), None, '120')
    state, _ = poll(env)
    item = store.read(state['done']['b'])
    assert item['classification'] == 'case_update'
    assert all(m['status'] == 'proposal' and m['case_id'] is None for m in item['match_proposals'])


def test_case_update_requires_sourced_event_and_separate_stages(env):
    _, plan = prepared(env); plan['classification'] = 'case_update'
    with pytest.raises(ValueError, match='case_update_required'): sc.publish_card(env[0], plan, REPO)
    plan['case_update'] = {'probable_existing_case': 'Unconfirmed thread match',
                          'event': {'type': 'email', 'occurred_at': None, 'time_basis': 'Underlying event time unknown.',
                                    'actor': 'Customer according to source', 'source_reference': plan['input_reference'],
                                    'association': 'proposal', 'summary': 'New information'},
                          'what_changed': 'Source reports installation scope.',
                          'proposed_stages': {stage: {'state': 'unknown', 'basis': []} for stage in sc.STAGES},
                          'possible_crm_changes': [], 'response_required': True}
    card = sc.publish_card(env[0], plan, REPO)
    assert all(v['state'] == 'unknown' for v in env[0].read(card)['proposal']['case_update']['proposed_stages'].values())


def test_adapter_get_only_history_and_expired_response():
    session = Mock(spec=['get'])
    response = Mock(status_code=200)
    response.json.return_value = {'historyId': '110', 'history': [{'messagesAdded': [{'message': {'id': 'a'}}]}]}
    session.get.return_value = response
    reader = gmail.GmailReader(session, sleep=lambda _: None)
    assert reader.list_history('100', None).message_ids == ('a',)
    assert session.get.call_args.kwargs['params']['historyTypes'] == 'messageAdded'
    response.status_code = 404
    with pytest.raises(ProviderError, match='shadow_history_expired'): reader.list_history('100', None)


def test_no_external_write_or_runtime_clients_in_shadow_code():
    import ast
    for name in ('shadow_input.py', 'shadow_store.py', 'shadow_cards.py', 'shadow_commands.py'):
        tree = ast.parse((REPO / 'src/elektro_vienna' / name).read_text('utf-8'))
        imports = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(any(word in module for word in ('airtable', 'calendar', 'openai', 'anthropic', 'whatsapp')) for module in imports)
        calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        assert not set(calls) & {'post', 'put', 'patch', 'delete', 'send', 'create_draft', 'serve_forever'}


def test_validation_export_preserved_bound_to_frozen_answers(env):
    from elektro_vienna.first_response_validation import ValidationStore
    store = env[0]; validation = ValidationStore(store.root)
    packet = {'inquiries': [{'id': str(i)} for i in range(3)]}
    run = digest(canonical(packet))
    validation.publish(f'30_cases/first_response_validation/{run}/packet.json', canonical(packet))
    answers = {str(i): {'draft': 'Frozen ' + str(i)} for i in range(3)}
    frozen = {'packet_sha256': run, 'answers': answers, 'answers_sha256': digest(canonical(answers))}
    freeze_ref = f'30_cases/first_response_validation/{run}/answers.json'
    validation.publish(freeze_ref, canonical(frozen))
    review = {'kind': 'first_response_validation_review', 'format_version': 1, 'complete': True,
              'reviewer': 'Giovanni Superti-Furga', 'recorded_at': NOW.isoformat(), 'set': run,
              'answers_sha256': frozen['answers_sha256'],
              'cases': [{'example_id': str(i), 'choice': 'send', 'comment': ''} for i in range(3)]}
    raw = json.dumps(review, indent=3).encode(); owner = b'Owner: 3 of 3 accepted.\r\n'
    reference = ss.preserve_validation(store, raw, owner)
    assert ss.preserve_validation(store, raw, owner) == reference
    result = store.read(reference)
    assert store.path(result['original_reference']).read_bytes() == raw
    assert store.path(result['owner_request_reference']).read_bytes() == owner
    assert validation.path(freeze_ref).read_bytes() == canonical(frozen)
    review['answers_sha256'] = 'wrong'
    with pytest.raises(ValueError, match='answers_integrity'):
        ss.preserve_validation(store, canonical(review), owner)


def test_generic_project_subject_does_not_force_vendor_pitch_into_case():
    customer = {'email': si.field('vendor@example.invalid', 'header:from', 'vendor@example.invalid')}
    kind, _ = si.classify({'subject': 'Projektanfrage'}, 'Wir helfen euch, neue Kunden zu gewinnen.',
                          'incoming', customer, 'envelope_source_claim', [])
    assert kind == 'uncertain'


def test_pending_source_cannot_be_marked_sendable(env):
    state, _ = poll(env); store = env[0]
    result = sc.render_batch(store, state)
    assert 'value="sendable" disabled' in Path(result['review_path']).read_text('utf-8')
    review = {'kind': 'live_shadow_feedback', 'format_version': 1, 'batch': result['batch_reference'].split('/')[-2],
              'reviewer': 'Giovanni Superti-Furga', 'recorded_at': NOW.isoformat(),
              'items': [{'input_reference': state['done']['a'], 'card_reference': None, 'choice': 'sendable', 'comment': ''}]}
    with pytest.raises(ValueError, match='no_draft'): sc.import_feedback(store, canonical(review))


def test_corrupt_source_blocks_card_publication(env):
    _, plan = prepared(env); item = env[0].read(plan['input_reference'])
    source = item['sources'][0]
    archive_store.SourceArchive(env[0].root).path(source['reference']).write_bytes(b'corrupt')
    with pytest.raises(ProviderError, match='integrity'): sc.publish_card(env[0], plan, REPO)


def test_standard_price_and_qualified_estimate_are_policy_derived(env):
    _, plan = prepared(env)
    plan['commercial'].update(kind='estimate', standard_items=['single_hour', 'travel'],
                              estimate_lines=[{'price_item': 'single_hour', 'quantity': '2', 'assumption': 'Two hours, provisional'},
                                              {'price_item': 'travel', 'quantity': '1', 'assumption': 'One visit'}])
    plan['draft'] = 'Bei angenommenen zwei Stunden ergibt sich ein Richtwert von EUR 250 netto inklusive Anfahrt, zuzüglich Umsatzsteuer und Material.\n\n' + sc.SIGNOFF
    ref = sc.publish_card(env[0], plan, REPO); card = env[0].read(ref)
    assert Decimal(card['estimate_net_eur']) == Decimal('250')
    assert card['standard_prices']['single_hour']['amount_net_eur'] == '100.00'


@pytest.mark.parametrize('payload', [{'historyId': 'bad'}, {'historyId': '99'},
                                      {'historyId': '101', 'history': [{'messagesAdded': [{'message': {'id': '../bad'}}]}]}])
def test_malformed_history_pages_fail_closed(payload):
    session = Mock(spec=['get']); response = Mock(status_code=200); response.json.return_value = payload
    session.get.return_value = response
    with pytest.raises(ProviderError, match='invalid_history_page'):
        gmail.GmailReader(session).list_history('100', None)


def test_legacy_pending_deleted_draft_is_audited_without_reset_or_losing_later_mail(env):
    store, reader, checkpoint, *_ = env
    state, _ = poll(env)
    state['page'] = {'pending': ['gone', 'b', 'sent'], 'next': None, 'end': '150'}
    si.save_checkpoint(checkpoint, state)
    reader.list_history.return_value = HistoryPage(('gone', 'b', 'sent'), None, '150',
                                                  (('gone', ('DRAFT',)), ('b', ('INBOX',)), ('sent', ('SENT',))))
    reader.get_message.side_effect = lambda mid: (_ for _ in ()).throw(ProviderError('gmail_http_404')) if mid == 'gone' else Message(
        mid, 'thread-' + mid, MS, (), ('SENT',) if mid == 'sent' else ('INBOX',), ())
    state, result = poll(env)
    assert result['processed'] == 3 and len(result['new_inputs']) == 2
    assert result['unavailable_drafts_excluded'] == ['gone']
    assert state['history_id'] == '150' and state['done']['gone'] is None
    assert store.read(state['unavailable_drafts']['gone'])['message_added_labels'] == ['DRAFT']
    assert store.read(state['done']['b'])['direction'] == 'incoming'
    assert store.read(state['done']['sent'])['direction'] == 'sent'
    reader.history_checkpoint.assert_called_once()  # Initial bootstrap only; no reset.
    assert reader.list_messages.call_count == 1  # No fallback import.
    assert reader.list_history.call_args.args == ('100', None, 20)
    page = sc.render_batch(store, state)
    assert 'Excluded unavailable drafts: 1' in Path(page['review_path']).read_text('utf-8')
    before = len(list(store.root.rglob('input.json')))
    reader.list_history.return_value = HistoryPage((), None, '160')
    _, result = poll(env)
    assert result['new_inputs'] == [] and len(list(store.root.rglob('input.json'))) == before


@pytest.mark.parametrize('labels', [('INBOX',), ()])
def test_unproven_message_404_is_never_silently_skipped(env, labels):
    poll(env); reader = env[1]
    reader.list_history.return_value = HistoryPage(('gone',), None, '150', (('gone', labels),))
    reader.get_message.side_effect = ProviderError('gmail_http_404')
    state, result = poll(env)
    assert result['unresolved_source_count'] == 1 and result['new_inputs'] == []
    state = json.loads(env[2].read_bytes())
    assert state['history_id'] == '150' and state['page'] is None
    audit = env[0].read(state['unavailable_messages']['gone'])
    assert audit['kind'] == 'live_shadow_unavailable_message' and not state.get('unavailable_drafts')
    assert 'Unresolved source gaps: 1' in Path(sc.render_batch(env[0], state)['review_path']).read_text('utf-8')
    # An unavailable ID remains retryable even when no new history mentions it.
    reader.list_history.return_value = HistoryPage((), None, '160')
    reader.get_message.side_effect = lambda mid: Message(mid, 'thread-' + mid, MS, (), ('INBOX',), ())
    state, result = poll(env)
    assert len(result['new_inputs']) == 1 and result['unresolved_source_count'] == 0
    resolution = env[0].read(state['availability_resolutions']['gone'])
    assert env[0].read(resolution['previous_reference']) == audit
    assert resolution['source_reference'] == state['done']['gone']


@pytest.mark.parametrize('failure_at,code', [('get_message', 'gmail_http_403'),
                                           ('get_message', 'gmail_http_500'), ('get_original', 'gmail_http_404'),
                                           ('get_attachment', 'gmail_http_404')])
def test_other_failures_preserve_pending_source(env, failure_at, code):
    poll(env); reader = env[1]
    reader.list_history.return_value = HistoryPage(('b',), None, '150', (('b', ('DRAFT',)),))
    if failure_at == 'get_attachment':
        reader.get_message.side_effect = lambda mid: Message(mid, 'thread-' + mid, MS, (), ('INBOX',),
                                                            (Attachment('1', 'opaque', 'test.txt', 'text/plain', 1),))
    getattr(reader, failure_at).side_effect = ProviderError(code)
    with pytest.raises(ProviderError, match=code): poll(env)
    state = json.loads(env[2].read_bytes())
    assert state['history_id'] == '100' and state['page']['pending'] == ['b']
    assert 'b' not in state['done'] and not state.get('unavailable_drafts')


def test_draft_creation_labels_do_not_override_fresh_incoming_metadata(env):
    poll(env); reader = env[1]
    reader.list_history.return_value = HistoryPage(('b',), None, '150', (('b', ('DRAFT',)),))
    state, result = poll(env)
    assert len(result['new_inputs']) == 1 and result['unavailable_drafts_excluded'] == []
    assert env[0].read(state['done']['b'])['direction'] == 'incoming'


def test_audit_publish_failure_keeps_draft_pending(env, monkeypatch):
    poll(env); store, reader, checkpoint, *_ = env
    reader.list_history.return_value = HistoryPage(('gone',), None, '150', (('gone', ('DRAFT',)),))
    reader.get_message.side_effect = ProviderError('gmail_http_404')
    original = store.document
    monkeypatch.setattr(store, 'document', Mock(side_effect=OSError('disk unavailable')))
    with pytest.raises(OSError): poll(env)
    state = json.loads(checkpoint.read_bytes())
    assert state['history_id'] == '100' and state['page']['pending'] == ['gone'] and 'gone' not in state['done']
    monkeypatch.setattr(store, 'document', original)
    state, result = poll(env)
    assert result['unavailable_drafts_excluded'] == ['gone'] and state['history_id'] == '150'


def test_history_creation_labels_validated_and_conflicting_draft_not_assumed():
    session = Mock(spec=['get']); response = Mock(status_code=200)
    response.json.return_value = {'historyId': '101', 'history': [{'messagesAdded': [
        {'message': {'id': 'a', 'labelIds': ['DRAFT']}}, {'message': {'id': 'a', 'labelIds': ['SENT']}}]}]}
    session.get.return_value = response
    reader = gmail.GmailReader(session, sleep=lambda _: None)
    assert dict(reader.list_history('100', None).message_added_labels) == {'a': ()}
    response.json.return_value['history'][0]['messagesAdded'][0]['message']['labelIds'] = 'DRAFT'
    with pytest.raises(ProviderError, match='invalid_history_page'): reader.list_history('100', None)


def test_cli_identifies_shadow_failure(monkeypatch, capsys):
    from elektro_vienna import cli, shadow_commands
    monkeypatch.setattr(shadow_commands, 'run', Mock(side_effect=ProviderError('synthetic_failure')))
    assert cli.main(['shadow-check']) == 1
    assert 'Shadow stopped: synthetic_failure' in capsys.readouterr().err


def test_missing_retries_are_bounded_rotate_and_do_not_starve_new_evidence(env):
    store, reader, checkpoint, *_ = env
    poll(env)
    reader.list_history.return_value = HistoryPage(tuple('bcdef'), None, '150')
    reader.get_message.side_effect = ProviderError('gmail_http_404')
    state, result = poll(env)
    assert result['unresolved_source_count'] == 5
    original = lambda mid: Message(mid, 'thread-' + mid, MS, (), ('INBOX',), ())
    reader.get_message.side_effect = lambda mid: original(mid) if mid == 'new' else (_ for _ in ()).throw(ProviderError('gmail_http_404'))
    reader.list_history.return_value = HistoryPage(('new',), None, '160')
    reader.get_message.reset_mock()
    state, result = poll(env)
    assert len(result['new_inputs']) == 1 and result['processed'] == 4
    assert [c.args[0] for c in reader.get_message.call_args_list] == ['b', 'c', 'd', 'new']
    reader.list_history.return_value = HistoryPage((), None, '170')
    reader.get_message.reset_mock()
    poll(env)
    assert [c.args[0] for c in reader.get_message.call_args_list] == ['e', 'f', 'b']
