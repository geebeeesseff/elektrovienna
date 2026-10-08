"""Offline review contracts using synthetic sources only."""

import copy
import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_pilot import setup, MAILBOX, TICKET
from elektro_vienna import cli, pilot
from elektro_vienna.models import ProviderError
from elektro_vienna.pilot_sources import canonical, digest
from elektro_vienna.review_ui import (ReviewStore, SECTIONS, build_view, email_blocks, fold_history,
                                      import_review, publish_view, validate_review)


@pytest.fixture
def review_setup(setup):
    path, state, root = setup
    run = pilot.reconstruct(path, state, MAILBOX)
    extraction = json.loads(Path(run['extraction']).read_bytes())
    message = extraction['messages']['a']
    section = next(s for s in message['sections'] if ':decoded-text' in s['locator'])
    ref = dict(source_id=message['source_id'], locator=section['locator'], start=0,
               end=len(section['text']), quote=section['text'])
    presentation = dict(case_id=TICKET, baseline_id=run['processing_run'],
        human_feedback=dict(reviewer='Synthetic reviewer', received_on='2026-10-06', verbatim='Visit completed; full project uncertain.'),
        sections={k: [dict(id=k, title=k, text='A cited interpretation.', certainty='Uncertain',
                          source_refs=[ref], **({'status':'unknown'} if k=='outcome' else {}))] for k in SECTIONS})
    return run, presentation, state, root


def test_replay_preserves_sources_state_and_prior_outputs(review_setup):
    run, presentation, state, root = review_setup
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    state_bytes = state.read_bytes()
    store = ReviewStore()
    first = publish_view(store, run['processing_run'], TICKET, presentation)
    assert first == publish_view(store, run['processing_run'], TICKET, presentation)
    view = json.loads(Path(first['html']).with_name('view.json').read_bytes())
    assert view['sections']['outcome'][0]['status'] == 'unknown'
    assert view['identity']['status']['value'] == 'Abgeschlossen'
    assert view['baseline_sha256'] == digest(Path(run['cases']).read_bytes())
    assert all(p.read_bytes() == data for p, data in before.items())
    assert state.read_bytes() == state_bytes
    assert not (root/'40_knowledge').exists()
    presentation['sections']['summary'][0]['text'] = 'A revised interpretation.'
    assert publish_view(store, run['processing_run'], TICKET, presentation)['view_id'] != first['view_id']
    assert Path(first['html']).exists()


def test_unsafe_source_html_escaped_and_no_external_resources(review_setup):
    run, presentation, _, _ = review_setup
    presentation['sections']['summary'][0]['text'] = '</script><img src="https://evil.invalid/x" onerror="alert(1)">'
    published = publish_view(ReviewStore(), run['processing_run'], TICKET, presentation)
    page = Path(published['html']).read_text(encoding='utf-8')
    assert '<img' not in page
    assert '&lt;img' in page
    assert 'connect-src &#x27;none&#x27;' in page
    assert "'unsafe-inline'" not in page
    assert not re.search(r'<(?:script|link|iframe)[^>]+(?:src|href)=', page)
    assert page.count('<script>') == 1


@pytest.mark.parametrize('mutation', ['case', 'citation', 'foreign_source', 'duplicate', 'stage'])
def test_bad_curation_fails_before_publication(review_setup, mutation):
    run, p, _, root = review_setup
    if mutation == 'case': p['case_id'] = 'foreign'
    if mutation == 'citation': p['sections']['summary'][0]['source_refs'][0]['quote'] = 'invented'
    if mutation == 'foreign_source': p['sections']['summary'][0]['source_refs'][0]['source_id'] = 'foreign'
    if mutation == 'duplicate': p['sections']['technical'][0]['id'] = 'summary'
    if mutation == 'stage': p['sections']['outcome'][0]['status'] = 'whole_project_complete'
    with pytest.raises(ProviderError): publish_view(ReviewStore(), run['processing_run'], TICKET, p)
    assert not (root/'30_cases/review_ux').exists()


def test_extraction_tamper_rejected(review_setup):
    run, p, _, _ = review_setup
    Path(run['extraction']).write_bytes(b'{}')
    with pytest.raises(ProviderError, match='integrity'):
        publish_view(ReviewStore(), run['processing_run'], TICKET, p)


def export_for(view, view_id):
    return dict(format_version=1, view_id=view_id, case_id=view['case_id'], baseline_id=view['baseline_id'],
                reviewer='Synthetic Reviewer', reviewed_at='2026-10-06T11:00:00Z', decisions=[])


def test_mixed_thread_requires_ack_and_message_exception_overrides(review_setup):
    run, p, _, _ = review_setup
    view, *_ = build_view(ReviewStore(), run['processing_run'], TICKET, p)
    thread = view['conversations'][0]
    second = copy.deepcopy(thread['messages'][0]); second['id'] = 'mixed-topic'
    thread['messages'].append(second)
    review = export_for(view, 'synthetic')
    decision = dict(kind='thread', id=thread['id'], status='belongs', comment='Checked')
    review['decisions'] = [decision]
    with pytest.raises(ProviderError, match='acknowledgement'): validate_review(view, 'synthetic', review)
    decision['all_shown_messages_acknowledged'] = True
    review['decisions'].append(dict(kind='message', id='mixed-topic', status='does_not_belong', comment='Other job'))
    assert [d['status'] for d in validate_review(view, 'synthetic', review)] == ['belongs', 'does_not_belong']


@pytest.mark.parametrize('mutation', ['stale', 'foreign', 'duplicate', 'status', 'timestamp', 'empty'])
def test_bad_export_rejected(review_setup, mutation):
    run, p, _, _ = review_setup
    view, *_ = build_view(ReviewStore(), run['processing_run'], TICKET, p)
    r = export_for(view, 'synthetic')
    r['decisions'] = [dict(kind='item', id='summary', status='partially_correct', comment='Clarify scope')]
    if mutation == 'stale': r['view_id'] = 'stale'
    if mutation == 'foreign': r['decisions'][0]['id'] = 'foreign'
    if mutation == 'duplicate': r['decisions'] *= 2
    if mutation == 'status': r['decisions'][0]['status'] = 'accepted'
    if mutation == 'timestamp': r['reviewed_at'] = '2026-10-06T12:00:00'
    if mutation == 'empty': r['decisions'] = []
    with pytest.raises(ProviderError): validate_review(view, 'synthetic', r)


def test_import_is_immutable_attributable_and_evaluation_only(review_setup):
    run, p, state, root = review_setup
    store = ReviewStore(); published = publish_view(store, run['processing_run'], TICKET, p)
    view = json.loads(Path(published['html']).with_name('view.json').read_bytes())
    review = export_for(view, published['view_id'])
    review['decisions'] = [dict(kind='item', id='summary', status='important_info_missing', comment='Something missing')]
    raw = canonical(review)
    before = {x:x.read_bytes() for x in [state, Path(run['cases']), Path(run['extraction']), Path(published['html'])]}
    first = import_review(store, published['view_id'], raw)
    assert first == import_review(store, published['view_id'], raw)
    assert Path(first['export']).read_bytes() == raw
    review['decisions'][0]['comment'] = 'Correction 2'
    assert import_review(store, published['view_id'], canonical(review))['review'] != first['review']
    assert all(x.read_bytes() == b for x,b in before.items())


def test_quotes_preserved_with_inline_answers_and_forwarded_only_visible():
    text = 'New answer\r\r\n> old question\nInline new answer\n>> older answer\n'
    blocks = email_blocks(text)
    assert ''.join(b['text'] for b in blocks if not b['quoted']) == 'New answer\nInline new answer\n'
    assert 'old question' in ''.join(b['text'] for b in blocks if b['quoted'])
    forwarded = 'Forwarded message\nFrom: Supplier\nSubject: Offer\n\nThe only offer is EUR 321.'
    assert not any(b['quoted'] for b in fold_history(forwarded, []))
    old = 'This is the prior long message. ' * 6
    reply = 'Thanks\nFrom: Sender\nSubject: Previous\n\n' + old
    assert fold_history(reply, [old])[-1]['quoted']
    assert 'New inline correction' in ''.join(b['text'] for b in fold_history(reply + '\nNew inline correction', [old]) if not b['quoted'])
    assert ''.join(b['text'] for b in fold_history(reply, [old])) == reply


@pytest.mark.parametrize('malformed', [None, [], {'decisions': None}, {'decisions': [None]}])
def test_malformed_export_is_a_safe_error(review_setup, malformed):
    run, p, _, _ = review_setup
    view, *_ = build_view(ReviewStore(), run['processing_run'], TICKET, p)
    with pytest.raises(ProviderError):
        validate_review(view, 'synthetic', malformed)


def test_cli_render_and_import_do_not_load_config_or_auth(review_setup, monkeypatch, tmp_path, capsys):
    run, p, _, _ = review_setup
    presentation = tmp_path/'presentation.json'; presentation.write_bytes(canonical(p))
    monkeypatch.setattr(cli, 'local_app_data', lambda: tmp_path)
    monkeypatch.setattr(cli.Config, 'from_environment', Mock(side_effect=AssertionError('config forbidden')))
    assert cli.main(['review-render', '--baseline', run['processing_run'], '--case-id', TICKET, '--presentation', str(presentation)]) == 0
    result = json.loads(capsys.readouterr().out)
    view = json.loads(Path(result['html']).with_name('view.json').read_bytes())
    review = export_for(view, result['view_id']); review['decisions'] = [dict(kind='item', id='summary', status='correct', comment='')]
    export = tmp_path/'export.json'; export.write_bytes(canonical(review))
    assert cli.main(['review-import', '--view-id', result['view_id'], '--decisions', str(export)]) == 0
    cli.authenticate.assert_not_called()
