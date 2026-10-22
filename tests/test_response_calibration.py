import copy
import json
import socket
from email.message import EmailMessage

import pytest

from test_blind_eval import experiment
from test_policy_experiment import ready
from elektro_vienna import policy_experiment as p, response_calibration as c
from elektro_vienna.pilot_sources import canonical, digest
from elektro_vienna.archive_store import SourceArchive, source_reference
from elektro_vienna.models import ProviderError


def feedback(experiment):
    store, run, answers = ready(experiment)
    p.freeze_policy_only(store, run, answers)
    p.freeze(store, run, answers)
    p.comparison(store, run)
    raw = json.dumps({'kind': 'blinded_response_preference', 'comparison': run,
                      'recorded_at': '2026-10-14T12:19:29Z', 'cases': [
                          {'case_id': cid, 'preference': 'equal', 'comment': 'Exact\n comment  '}
                          for cid in answers]}, indent=4).encode()
    owner = {'kind': 'owner_feedback', 'owner': 'Giovanni Superti-Furga',
             'verbatim': 'Synthetic feedback, not a new tariff.', 'effect': 'evidence_only; policy_unchanged'}
    return c.CalibrationStore(store.root), raw, owner


def test_original_feedback_preserved_before_mapping_and_idempotent(experiment, monkeypatch):
    store, raw, owner = feedback(experiment)
    before = {f: f.read_bytes() for f in store.root.rglob('*') if f.is_file()}
    original_load = c.load
    def guarded(path):
        if path.name == 'mapping.json':
            assert store.path(f'90_manual_review/response_calibration/{digest(raw)}/ab-original.json').read_bytes() == raw
        return original_load(path)
    monkeypatch.setattr(c, 'load', guarded)
    reference, result = c.ingest_ab(store, raw, owner)
    assert c.ingest_ab(store, raw, owner) == (reference, result)
    assert all(r['comment_verbatim'] == 'Exact\n comment  ' for r in result['results'])
    assert all(r['policy_only'] != r['policy_history'] for r in result['results'])
    assert all(f.read_bytes() == data for f, data in before.items())


@pytest.mark.parametrize('bad', ['duplicate', 'choice', 'timestamp', 'owner'])
def test_bad_feedback_rejected(experiment, bad):
    store, raw, owner = feedback(experiment)
    review = json.loads(raw)
    if bad == 'duplicate': review['cases'][1] = copy.deepcopy(review['cases'][0])
    if bad == 'choice': review['cases'][0]['preference'] = None
    if bad == 'timestamp': review['recorded_at'] = '2026-10-14T12:00:00'
    if bad == 'owner': owner['effect'] = 'automatically_set_current_prices'
    with pytest.raises(ValueError): c.ingest_ab(store, canonical(review), owner)


def pair(experiment, body='We can help. <script>unsafe()</script>\nGiovanni Superti-Furga\n_____%\nQUOTED_SECRET'):
    store, raw, owner = feedback(experiment)
    ab_ref, _ = c.ingest_ab(store, raw, owner)
    archive = SourceArchive(store.root)
    rows = []
    for i in range(3):
        mid = f'pair{i}'
        msg = EmailMessage()
        msg['From'] = 'customer@example.invalid' if i == 0 else 'office@elektrovienna.at'
        msg['To'] = 'office@elektrovienna.at' if i == 0 else 'customer@example.invalid'
        msg['Date'] = f'Wed, 14 Oct 2026 12:0{i}:00 +0000'
        msg['Subject'] = 'Socket <script>unsafe()</script>' if i < 2 else 'FUTURE_SECRET'
        msg['Message-ID'] = f'<{mid}@example.invalid>'
        if i: msg['In-Reply-To'] = '<pair0@example.invalid>'
        msg.set_content('Please install a socket.' if i == 0 else body if i == 1 else 'FUTURE_SECRET')
        data = msg.as_bytes()
        published = archive.publish(source_reference('office@elektrovienna.at', mid), data)
        rows.append({'message_id': mid, 'thread_id': 'pair', 'internal_ms': 1000 + i * 1000,
                     'labels': [] if i == 0 else ['SENT'],
                     'headers': {k.lower(): v for k, v in c.parse_email(data)['headers']},
                     'source': {'message_id': mid, 'reference': published.reference,
                                'sha256': published.sha256, 'byte_length': published.byte_length}})
    inventory = {'state_sha256': 'a' * 64, 'messages': rows}
    selection = [{'title': 'Synthetic socket', 'inquiry': 'pair0', 'reply': 'pair1',
                  'selection_reason': 'Direct estimate with a full-name signature.'}]
    return store, archive, inventory, selection, ab_ref


def test_calibration_preserves_provenance_and_excludes_future(experiment, monkeypatch):
    args = pair(experiment)
    store, archive, inventory, *_ = args
    before = {f: f.read_bytes() for f in store.root.rglob('*') if f.is_file()}
    def denied(*args, **kwargs):
        pytest.fail('Network is forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    run, record = c.publish_set(*args)
    assert c.publish_set(*args) == (run, record)
    assert all(f.read_bytes() == data for f, data in before.items())
    rendered = store.path(f'30_cases/response_calibration/{run}/calibration.html').read_text('utf-8')
    assert 'FUTURE_SECRET' not in rendered and 'QUOTED_SECRET' not in rendered
    assert 'FUTURE_SECRET' not in json.dumps(record)
    assert '<script>unsafe()' not in rendered and '&lt;script&gt;unsafe()' in rendered
    assert rendered.count('type="radio"') == 2 and rendered.count('<textarea ') == 1
    assert '14.10.2026 14:01 CEST' in rendered
    example = record['examples'][0]
    assert example['reply']['source'] == inventory['messages'][1]['source']
    assert example['reply']['projection']['end'] < len(c.parse_email(
        archive.path(example['reply']['source']['reference']).read_bytes())['sections'][-1]['text'])


@pytest.mark.parametrize('body', [
    'We can help.\nJosef Schenk\n_____\nGiovanni Superti-Furga',
    'We can help.\n_____\nGiovanni Superti-Furga',
    'We can help.\nGiovanni Superti-Furga\ni.V. Sascha Stanojevic',
    'We can help.\nOffice Elektro Vienna'])
def test_unclear_or_other_authorship_excluded(experiment, body):
    with pytest.raises(ValueError, match='authorship_uncertain'):
        c.publish_set(*pair(experiment, body))


@pytest.mark.parametrize('mutation, error', [
    ('later', 'not_first_outbound'), ('internal', 'not_customer_reply'),
    ('earlier_contact', 'earlier_customer_contact'), ('duplicate', 'selection_duplicate'),
    ('intervening', 'intervening_message'), ('headers', 'inventory_headers_changed'),
    ('integrity', 'archive_integrity_mismatch'), ('feedback', 'feedback_hash_mismatch')])
def test_invalid_calibration_boundaries_fail_closed(experiment, mutation, error):
    args = pair(experiment)
    store, archive, inventory, selection, ab_ref = args
    rows = inventory['messages']
    if mutation == 'later': selection[0]['reply'] = 'pair2'
    if mutation == 'internal':
        # Keep archive/header identity coherent to reach the recipient validation.
        source = rows[1]['source']; path = archive.path(source['reference'])
        data = path.read_bytes().replace(b'To: customer@example.invalid', b'To: internal@example.invalid')
        path.write_bytes(data); source.update(sha256=digest(data), byte_length=len(data))
        rows[1]['headers']['to'] = 'internal@example.invalid'
    if mutation in {'earlier_contact', 'intervening'}:
        other = copy.deepcopy(rows[0]); other['message_id'] = 'other'
        other['thread_id'] = 'other' if mutation == 'earlier_contact' else 'pair'
        other['internal_ms'] = 500 if mutation == 'earlier_contact' else 1500
        rows.append(other)
    if mutation == 'duplicate': selection.append(selection[0])
    if mutation == 'headers': rows[0]['headers']['subject'] = 'Changed'
    if mutation == 'integrity': archive.path(rows[1]['source']['reference']).write_bytes(b'corrupt')
    if mutation == 'feedback': store.path(ab_ref).write_bytes(b'{}')
    with pytest.raises((ValueError, ProviderError), match=error): c.publish_set(*args)
    assert not (store.root / '30_cases/response_calibration').exists()
