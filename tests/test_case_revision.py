"""Synthetic completed reviews; never customer runtime data or provider calls."""

import copy
import json
from pathlib import Path

import pytest

from test_pilot import setup, TICKET
from test_review_ui import review_setup
from elektro_vienna import cli
from elektro_vienna import review_local
from elektro_vienna.case_revision import (GROUPS, ROLES, STAGE_KEYS, RevisionStore,
                                         build_revision, checked_supplement, publish_revisions, render_comparison)
from elektro_vienna.models import ProviderError
from elektro_vienna.pilot_sources import canonical, digest
from elektro_vienna.review_local import ReviewSession
from elektro_vienna.review_ui import publish_view


@pytest.fixture
def revision_setup(review_setup, tmp_path, monkeypatch):
    monkeypatch.setattr(review_local, 'local_app_data', lambda: tmp_path / 'private')
    run, presentation, state, root = review_setup
    store = RevisionStore()
    view = publish_view(store, run['processing_run'], TICKET, presentation)
    session = ReviewSession(store, view['view_id'])
    draft = session.latest()['review']
    draft['reviewer'] = 'Synthetic reviewer'
    next(f for f in draft['fields'] if f['kind'] == 'item' and f['id'] == 'summary')['comment'] = 'Visit performed by Example Electrician; installation remains unknown.'
    saved = session.save(0, draft)
    completed = session.complete(saved['revision'], True, False)
    ref = {'kind': 'review_item', 'id': 'summary'}

    def fact(identity, **extra):
        return dict(id=identity, title=identity, text='Synthetic evidence-linked interpretation.',
                    certainty='unknown', basis=[ref], **extra)

    p = dict(case_id=TICKET, baseline_id=run['processing_run'],
             completed_review_reference=completed['reference'], curator='Synthetic test',
             facts={g: [] for g in GROUPS}, events=[], crm_observations=[],
             document_search={'scope': 'Synthetic only', 'found': [], 'unresolved': ['Invoice missing']},
             comment_resolutions=[dict(kind='item', id='summary', targets=['stage-consultation'], note='Visit retained.')],
             project_scopes=[dict(id='project', title='Synthetic project', message_ids=list(session.thread_for),
                                  association_note='Original candidate decisions retained.')])
    p['facts']['identity'] = [fact('identity-' + k, field=k) for k in ['customer_account', 'project', 'address', 'service_type']]
    p['facts']['state'] = [fact('stage-' + k, stage=k, status='unknown') for k in sorted(STAGE_KEYS)]
    p['facts']['commercial'] = [fact('cost-' + r, role=r, amount=None) for r in sorted(ROLES)]
    consultation = next(s for s in p['facts']['state'] if s['stage'] == 'consultation')
    consultation.update(status='completed', certainty='human_reported', basis=[dict(kind='comment', target_kind='item', id='summary')])
    p['events'] = [fact('event-visit', occurred_at=None, time_precision='unknown', channel='human_review',
                        event_type='site_visit', project_scope='project')]
    p['events'][0]['basis'] = consultation['basis']
    p['crm_observations'] = [fact('crm-proposal', baseline_pointer='/identity/status')]
    return store, p, session, state, root


def test_versioned_replay_preserves_all_inputs_and_no_provider_writes(revision_setup, monkeypatch):
    store, p, session, state, root = revision_setup
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    state_before = state.read_bytes()
    monkeypatch.setattr(cli, 'authenticate', lambda *_: pytest.fail('Provider access prohibited'))
    first = publish_revisions(store, [p])
    assert first == publish_revisions(store, [p])
    revision = json.loads(store.path(first[0]['reference']).read_bytes())
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert state.read_bytes() == state_before
    assert revision['completed_review']['sources_acknowledged'] is False
    assert all(d['state'] == 'unreviewed' for d in revision['completed_review']['decisions'] if d['kind'] != 'item')
    assert revision['events'][0]['occurred_at'] is None
    assert revision['events'][0]['reported_by'] == 'Synthetic reviewer'
    assert revision['lineage']['completed_review_sha256'] == session.history('completed')[-1]['sha256']
    assert not (root / '40_knowledge').exists()
    p['facts']['identity'][0]['text'] = 'A revised interpretation, not a source overwrite.'
    p['previous_revision'] = first[0]['reference']
    second = publish_revisions(store, [p])
    assert first != second
    assert store.path(first[0]['reference']).exists()


def test_draft_does_not_replace_completed_review(revision_setup):
    store, p, session, *_ = revision_setup
    original = build_revision(store, p)
    current = session.latest()
    current['review']['fields'][0]['comment'] = 'UNFINISHED correction, not approved.'
    session.save(current['revision'], current['review'])
    assert build_revision(store, p) == original
    session.complete(session.latest()['revision'], True, False)
    with pytest.raises(ProviderError, match='not_latest'):
        build_revision(store, p)


def test_no_completed_review_is_not_accepted(review_setup):
    run, p, _, _ = review_setup
    store = RevisionStore()
    published = publish_view(store, run['processing_run'], TICKET, p)
    with pytest.raises(ProviderError, match='completed_review_missing'):
        build_revision(store, dict(case_id=TICKET, baseline_id=run['processing_run']))
    assert ReviewSession(store, published['view_id']).latest()['revision'] == 0


@pytest.mark.parametrize('bad', ['missing_stage', 'invalid_status', 'duplicate_fact', 'comment_dropped',
                                 'comment_wrong_target', 'foreign_item', 'missing_role', 'negative_amount',
                                 'wrong_quote', 'unsafe_pointer', 'unknown_time', 'naive_time', 'mixed_projects'])
def test_bad_plans_fail_before_publication(revision_setup, bad):
    store, p, session, _, root = revision_setup
    if bad == 'missing_stage': p['facts']['state'].pop()
    if bad == 'invalid_status': p['facts']['state'][0]['status'] = 'crm_proves_everything_done'
    if bad == 'duplicate_fact': p['facts']['identity'][1]['id'] = p['facts']['identity'][0]['id']
    if bad == 'comment_dropped': p['comment_resolutions'] = []
    if bad == 'comment_wrong_target': p['comment_resolutions'][0]['targets'] = ['missing']
    if bad == 'foreign_item': p['facts']['identity'][0]['basis'] = [dict(kind='review_item', id='foreign')]
    if bad == 'missing_role': p['facts']['commercial'].pop()
    if bad == 'negative_amount': p['facts']['commercial'][0].update(amount='-100', currency='EUR')
    if bad == 'wrong_quote':
        ref = session.view['sections']['summary'][0]['source_refs'][0]
        p['facts']['identity'][0]['basis'] = [dict(ref, kind='source_span', quote='Invented')]
    if bad == 'unsafe_pointer': p['crm_observations'][0]['baseline_pointer'] = '/nonexistent'
    if bad == 'unknown_time': p['events'][0]['occurred_at'] = '2026-10-08'
    if bad == 'naive_time': p['events'][0].update(occurred_at='2026-10-08T12:00:00', time_precision='datetime')
    if bad == 'mixed_projects': p['project_scopes'].append(copy.deepcopy(p['project_scopes'][0]))
    with pytest.raises(ProviderError):
        publish_revisions(store, [p])
    assert not (root / '30_cases/reviewed').exists()


def test_corrupt_completion_and_source_rejected(revision_setup):
    store, p, session, _, root = revision_setup
    path = store.path(p['completed_review_reference'])
    raw = path.read_bytes()
    envelope = json.loads(raw)
    envelope['record']['reviewer'] = 'Tampered'
    path.write_bytes(canonical(envelope))
    with pytest.raises(ProviderError, match='integrity'):
        build_revision(store, p)
    path.write_bytes(raw)
    source = next(iter(session.view['sources'].values()))
    (root / source['reference']).write_bytes(b'corruption')
    with pytest.raises(ProviderError, match='integrity'):
        build_revision(store, p)


def test_html_inspection_only_escaped_and_no_form(revision_setup):
    store, p, *_ = revision_setup
    p['facts']['identity'][0]['text'] = '<script>evil()</script>'
    page = render_comparison(build_revision(store, p))
    assert '&lt;script&gt;' in page
    assert '<script>' not in page and '<form' not in page and '<input' not in page
    assert all(label in page for label in ['Vor der Prüfung', 'Menschliche Korrekturen', 'Konsolidierter Fallstand', 'Mögliche CRM-Updates'])


def test_batch_scope_and_dry_run(revision_setup):
    store, p, _, _, root = revision_setup
    with pytest.raises(ProviderError, match='batch_scope'):
        publish_revisions(store, [p, p])
    result = publish_revisions(store, [p], dry_run=True)
    assert result and not (root / '30_cases/reviewed').exists()
    with pytest.raises(ProviderError):
        store.path('30_cases/reviewed/../../source.eml')


def test_cli_never_authenticates_or_loads_writer(revision_setup, monkeypatch, tmp_path):
    _, p, *_ = revision_setup
    path = tmp_path / 'plan.json'
    path.write_bytes(canonical({'cases': [p]}))
    monkeypatch.setattr(cli, 'validate_path', lambda path, _: path)
    monkeypatch.setattr(cli, 'authenticate', lambda *_: pytest.fail('Must not authenticate'))
    monkeypatch.setattr(cli, 'SQLiteState', lambda *_: pytest.fail('Must not open writer'))
    assert cli.main(['case-revise', '--plan', str(path), '--dry-run']) == 0


def test_supplement_reuses_original_parser_not_supplied_text(revision_setup):
    store, _, session, *_ = revision_setup
    sources = list(session.view['sources'].values())
    original = next(s for s in sources if s['kind'] == 'message')
    attachment = next(s for s in sources if s['kind'] == 'attachment')
    parsed = checked_supplement(store, {'sources': [original, attachment],
                                        'documents': {'fabricated': {'text': 'Fake paid invoice'}}})
    assert 'fabricated' not in parsed['documents']
    assert parsed['parser_version'] and parsed['parser_sha256']
    assert 'Fake paid invoice' not in json.dumps(parsed)
    assert parsed['documents'][attachment['id']]['sections']
    with pytest.raises(ProviderError, match='parent_missing'):
        checked_supplement(store, {'sources': [attachment]})
    tampered = original | {'message_id': 'different'}
    with pytest.raises(ProviderError, match='identity'):
        checked_supplement(store, {'sources': [tampered]})


def test_new_view_completion_supersedes_old_view(revision_setup):
    store, p, session, *_ = revision_setup
    presentation = dict(case_id=p['case_id'], baseline_id=p['baseline_id'],
                        human_feedback=session.view['human_feedback'], sections=copy.deepcopy(session.view['sections']))
    presentation['sections']['summary'][0]['text'] = 'Newer view text.'
    new = publish_view(store, p['baseline_id'], p['case_id'], presentation)
    newer_session = ReviewSession(store, new['view_id'])
    saved = newer_session.save(0, dict(mode='correction_comments_v2', reviewer='Second reviewer', fields=[]))
    newer_session.complete(saved['revision'], True, True)
    with pytest.raises(ProviderError, match='not_latest'):
        build_revision(store, p)
