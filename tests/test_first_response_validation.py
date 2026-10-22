import copy
import json
import re
import socket
from email.message import EmailMessage
from pathlib import Path

import pytest

from test_blind_eval import experiment
from test_response_calibration import pair
from elektro_vienna import first_response_validation as v, response_calibration as c
from elektro_vienna.archive_store import source_reference
from elektro_vienna.pilot_sources import canonical, digest


def completed(experiment):
    args = pair(experiment)
    store = args[0]
    run, record = c.publish_set(*args)
    review = {'kind': 'first_response_calibration_review', 'format_version': 1,
              'set': run, 'complete': True, 'reviewer': 'Giovanni Superti-Furga',
              'recorded_at': '2026-10-14T14:51:29Z',
              'cases': [{'example_id': 'pair0', 'choice': 'different', 'comment': 'Exact typo \n'}]}
    raw = json.dumps(review, indent=4).encode()
    return store, raw, review


def test_preserve_completed_calibration_and_owner_request(experiment):
    store, raw, _ = completed(experiment)
    request = b'Current style confirmed; historical prices not confirmed.\r\n'
    ref, record = c.ingest_calibration(store, raw, request)
    assert c.ingest_calibration(store, raw, request) == (ref, record)
    assert store.path(record['original_reference']).read_bytes() == raw
    assert store.path(record['owner_request_reference']).read_bytes() == request
    assert record['cases'][0]['comment'] == 'Exact typo \n'
    assert record['counts'] == {'same': 0, 'different': 1}


@pytest.mark.parametrize('bad', ['incomplete', 'missing', 'duplicate', 'choice', 'set'])
def test_bad_calibration_completion(experiment, bad):
    store, _, review = completed(experiment)
    if bad == 'incomplete': review['complete'] = False
    if bad == 'missing': review['cases'] = []
    if bad == 'duplicate': review['cases'] *= 2
    if bad == 'choice': review['cases'][0]['choice'] = None
    if bad == 'set': store.path(f'30_cases/response_calibration/{review["set"]}/set.json').write_bytes(b'{}')
    with pytest.raises(ValueError): c.ingest_calibration(store, canonical(review), b'Owner request')


@pytest.fixture
def validation(experiment, tmp_path, monkeypatch):
    store, raw, _ = completed(experiment)
    feedback, _ = c.ingest_calibration(store, raw, b'Owner request')
    archive = v.SourceArchive(store.root)
    messages, files, selections = [], [], []
    for i in range(3):
        for future in (False, True):
            mid = f'new{i}-{future}'
            mail = EmailMessage()
            mail['From'] = f'fresh{i}@example.invalid' if not future else 'office@elektrovienna.at'
            mail['To'] = 'undisclosed-recipients:;' if not future and i == 0 else 'office@elektrovienna.at'
            if i == 0: mail['Delivered-To'] = 'office@elektrovienna.at'
            mail['Date'] = 'Wed, 14 Oct 2026 10:00:00 +0000'
            mail['Message-ID'] = f'<{mid}@example.invalid>'
            mail['Subject'] = 'Initial inquiry <img src=x>' if not future else 'LATE_SECRET'
            mail.set_content('Please fit a lamp.' if not future else 'LATE_SECRET_PRICE_777')
            data = mail.as_bytes()
            headers = v.parse_email(data)['headers']
            published = archive.publish(source_reference('office@elektrovienna.at', mid), data)
            files.append({'message_id': mid, 'kind': 'message', 'reference': published.reference,
                          'sha256': published.sha256, 'byte_length': published.byte_length})
            messages.append({'message_id': mid, 'thread_id': f'newthread{i}',
                             'internal_ms': 1000 + 1000*future, 'headers': json.dumps(headers),
                             'labels': json.dumps(['SENT'] if future else []), 'missing_attachment_parts': []})
        selections.append({'message_id': f'new{i}-False', 'title': f'Case {i}', 'selection_reason': 'Clean initial inquiry'})
    state = tmp_path / 'state'; state.write_bytes(b'unchanged read-only state')
    monkeypatch.setattr(v, 'archived_inputs', lambda *_: (messages, files))
    original_read = v.read_verified
    def guard(archive, row):
        assert not row['message_id'].endswith('True'), 'Future body must never be read'
        return original_read(archive, row)
    monkeypatch.setattr(v, 'read_verified', guard)
    def denied(*args, **kwargs): raise AssertionError('Network prohibited')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    contexts = {key: Path(path) for key, path in {
        'operating_policy': 'src/elektro_vienna/operating_policy.json', 'playbook': 'docs/FIRST_RESPONSE_PLAYBOOK.md',
        'price_book': 'docs/CURRENT_PRICE_BOOK.md', 'north_star': 'docs/NORTH_STAR.md'}.items()}
    prior = next((store.root / '30_cases/blind_eval').glob('*/packet.json')).relative_to(store.root).as_posix()
    args = (v.ValidationStore(store.root), state, selections, feedback, prior, [], contexts)
    return args, messages, files


def drafts(packet):
    return {item['id']: {'draft': 'Vielen Dank. <script>bad()</script>\n\nViele Grüße\nGiovanni Superti-Furga',
                         'internal_checks': 'Meister checks fixture handling.',
                         'basis': ['playbook', item['sources'][0]['sha256']], 'price_items': []}
            for item in packet['inquiries']}


def test_t0_only_immutable_drafts_and_static_review(validation):
    args, *_ = validation
    store = args[0]
    before = {f: f.read_bytes() for f in store.root.rglob('*') if f.is_file()}
    run, packet = v.prepare(*args)
    assert v.prepare(*args) == (run, packet)
    assert 'LATE_SECRET' not in json.dumps(packet)
    answers = drafts(packet)
    frozen = v.publish(store, run, answers)
    assert v.publish(store, run, answers) == frozen
    page = store.path(f'30_cases/first_response_validation/{run}/review.html').read_text('utf-8')
    assert 'LATE_SECRET' not in page and '<script>bad()' not in page
    assert page.count('type="radio"') == 9 and page.count('<textarea ') == 3
    assert '&lt;img src=x&gt;' in page
    assert frozen['answers_sha256'] in page and 'data-mode="validation"' in page
    assert all(f.read_bytes() == data for f, data in before.items())
    answers[next(iter(answers))]['draft'] = 'Changed\nViele Grüße\nGiovanni Superti-Furga'
    with pytest.raises(Exception, match='archive_integrity_mismatch'): v.publish(store, run, answers)


@pytest.mark.parametrize('bad,error', [('exposed', 'previously_exposed'), ('later', 'not_inbound'),
    ('earlier', 'not_first_thread'), ('missing_attachment', 'initial_attachments_missing'),
    ('partial', 'all_three_drafts'), ('citation', 'citation_outside_t0'), ('price', 'historical_price_forbidden')])
def test_validation_boundaries(validation, bad, error):
    args, messages, files = validation
    if bad == 'exposed': args[5].append('newthread0')
    if bad == 'later': args[2][0]['message_id'] = 'new0-True'
    if bad == 'earlier': messages.append({**messages[0], 'message_id': 'earlier', 'internal_ms': 500})
    if bad == 'missing_attachment': messages[0]['missing_attachment_parts'] = ['1']
    with pytest.raises(ValueError, match=error):
        run, packet = v.prepare(*args)
        answer = drafts(packet)
        if bad == 'partial': answer.pop(next(iter(answer)))
        if bad == 'citation': answer[next(iter(answer))]['basis'] = ['later-history']
        if bad == 'price': answer[next(iter(answer))]['price_items'] = ['historical_fan_fixed_price']
        v.publish(args[0], run, answer)


def test_price_book_is_exact_owner_seed_and_playbook_is_compact():
    book = Path('docs/CURRENT_PRICE_BOOK.md').read_text('utf-8')
    seeded = dict(re.findall(r'^\| ([a-z_]+) \| ([0-9]+\.[0-9]{2}) \|', book, re.M))
    policy = v.load_policy()
    assert seeded == {key: item['amount_net_eur'] for key, item in policy['prices'].items()}
    rules = re.findall(r'^\d+\. ', Path('docs/FIRST_RESPONSE_PLAYBOOK.md').read_text('utf-8'), re.M)
    assert len(rules) == 18


def test_missing_optional_headers_do_not_hide_real_recipients():
    assert c.addresses({'to': 'office@elektrovienna.at'}, 'to', 'cc', 'delivered-to') == {'office@elektrovienna.at'}
    assert c.addresses({'from': 'customer@example.invalid', 'to': 'office@elektrovienna.at'},
                       'from', 'to', 'cc') == {'customer@example.invalid', 'office@elektrovienna.at'}
