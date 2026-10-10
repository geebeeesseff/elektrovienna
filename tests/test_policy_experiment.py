import copy
import json
import socket
import sqlite3

import pytest

from test_blind_eval import experiment
from elektro_vienna import blind_eval as b, policy_experiment as p
from elektro_vienna.pilot_sources import canonical


def ready(experiment):
    root, plan, guidance, *_ = experiment
    original = b.prepare(root, plan, guidance)
    old = {i['case_id']: {'sections': {s: 'Old answer' for s in b.SECTIONS},
                         'citations': {s: ['guidance:NORTH_STAR'] for s in b.SECTIONS}} for i in plan['selection']}
    b.freeze(b.EvalStore(root), original, old)
    b.reveal(b.EvalStore(root), original)
    store = p.ExperimentStore(root)
    run = p.prepare(store, original)
    answers = {i['case_id']: {'sections': {s: '<script>synthetic</script>' for s in p.SECTIONS},
                             'citations': {s: ['guidance:NORTH_STAR'] for s in p.SECTIONS},
                             'customer_questions': [], 'history_uses': []} for i in plan['selection']}
    for answer in answers.values():
        answer['sections']['questions'] = 'Keine Rückfrage für den nächsten Schritt.'
    return store, run, answers


def test_P_projection_no_history_future_CRM_and_PH_gate(experiment):
    store, run, answers = ready(experiment)
    projected = p.context(store, run, 'P')
    text = json.dumps(projected)
    assert not projected['knowledge'] and 'Existing work matters' not in text
    assert 'SECRET_' not in text and 'inputs' not in projected and 'original_run' not in projected
    with pytest.raises(ValueError, match='PH_locked'): p.context(store, run, 'PH')
    p.freeze_policy_only(store, run, answers)
    ph = p.context(store, run, 'PH')
    assert len(ph['knowledge']) == 5
    assert {k: v for k, v in ph.items() if k != 'knowledge'} == {k: v for k, v in projected.items() if k != 'knowledge'}


def test_six_answer_gate_before_existing_key_access(experiment, monkeypatch):
    store, run, answers = ready(experiment)
    old_load = b.load
    def guarded(path):
        assert not str(path).endswith('answer_key.json'), 'Key opened too early'
        return old_load(path)
    monkeypatch.setattr(b, 'load', guarded)
    with pytest.raises(ValueError, match='six_frozen'): p.reveal(store, run)
    p.freeze_policy_only(store, run, answers)
    with pytest.raises(ValueError, match='six_frozen'): p.reveal(store, run)
    partial = copy.deepcopy(answers); partial.pop(next(iter(partial)))
    with pytest.raises(ValueError, match='all_three'): p.freeze(store, run, partial)


def test_history_citation_prohibited_in_P_and_analogy_required_in_PH(experiment):
    store, run, answers = ready(experiment)
    cid = next(iter(answers))
    reference = next(iter(b.REVIEWED)) + ':known'
    answers[cid]['citations']['commercial'] = [reference]
    with pytest.raises(ValueError, match='outside_variant'): p.freeze_policy_only(store, run, answers)
    answers[cid]['citations']['commercial'] = ['policy:commercial']
    p.freeze_policy_only(store, run, answers)
    answers[cid]['citations']['commercial'] = [reference]
    with pytest.raises(ValueError, match='attribution_missing'): p.freeze(store, run, answers)
    answers[cid]['history_uses'] = [{'citation': reference, 'similarity': 'Existing work',
                                    'difference': 'Different job', 'effect': 'Check scope internally'}]
    p.freeze(store, run, answers)


@pytest.mark.parametrize('bad', ['section', 'questions', 'length', 'citation'])
def test_answer_contract(experiment, bad):
    store, run, answers = ready(experiment)
    a = answers[next(iter(answers))]
    if bad == 'section': a['sections'].pop('crm')
    if bad == 'questions': a['customer_questions'] = ['a', 'b', 'c', 'd', 'e']
    if bad == 'length': a['sections']['draft'] = 'word ' * 151
    if bad == 'citation': a['citations']['known'] = ['T0:other_case']
    with pytest.raises(ValueError): p.freeze_policy_only(store, run, answers)


def test_freeze_replay_preserves_originals_and_prohibits_network_database(experiment, monkeypatch):
    store, run, answers = ready(experiment)
    before = {f: f.read_bytes() for f in store.root.rglob('*') if f.is_file()}
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network'))
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('database'))
    first = p.freeze_policy_only(store, run, answers)
    assert first == p.freeze_policy_only(store, run, answers)
    frozen = p.freeze(store, run, answers)
    assert frozen == p.freeze(store, run, answers)
    assert p.reveal(store, run) == p.reveal(store, run)
    changed = copy.deepcopy(answers); changed[next(iter(changed))]['sections']['action'] = 'changed'
    with pytest.raises(ValueError, match='cannot_change'): p.freeze_policy_only(store, run, changed)
    with pytest.raises(ValueError, match='cannot_change'): p.freeze(store, run, changed)
    assert all(f.read_bytes() == raw for f, raw in before.items())


def test_comparison_hides_mapping_and_escapes_text(experiment):
    store, run, answers = ready(experiment)
    p.freeze_policy_only(store, run, answers); p.freeze(store, run, answers)
    path = p.comparison(store, run)
    page = b.Path(path).read_text('utf-8')
    assert p.comparison(store, run) == path
    assert '<script>synthetic</script>' not in page and '&lt;script&gt;synthetic' in page
    assert all(s not in page for s in ['mapping.json', '"PH"', '"P"', 'Existing work matters', 'SECRET_', 'policy_only'])
    assert page.count('type="radio"') == 9 and page.count('<textarea') == 3
    assert 'connect-src &#x27;none&#x27;' in page
    mapping = b.load(store.path(p.ref(run, 'mapping.json')))
    assert all(set(m.values()) == {'P', 'PH'} for m in mapping['cases'].values())


def test_secondary_evaluation_reproducible_and_bound(experiment):
    store, run, answers = ready(experiment)
    p.freeze_policy_only(store, run, answers); p.freeze(store, run, answers); p.reveal(store, run)
    result = {'method': 'Synthetic annotations', 'cases': {cid: {v: {
        'scores': {d: {'score': 1, 'reason': 'Synthetic reason', 'citations': ['ANSWER']} for d in p.DIMENSIONS},
        'flags': {d: {'present': False, 'reason': 'Synthetic reason', 'citations': ['ANSWER']} for d in p.FLAGS}}
        for v in ('P', 'PH')} for cid in answers}}
    evaluation = p.evaluate(store, run, result)
    assert evaluation == p.evaluate(store, run, result)
    assert evaluation['totals_out_of_66'] == {'P': 33, 'PH': 33}
    result['cases'][next(iter(answers))]['P']['scores']['understanding']['score'] = 3
    with pytest.raises(ValueError, match='score_range'): p.evaluate(store, run, result)


def test_packet_and_freeze_corruption_rejected(experiment):
    store, run, answers = ready(experiment)
    p.freeze_policy_only(store, run, answers); p.freeze(store, run, answers)
    path = store.path(p.ref(run, 'frozen.json')); obj = b.load(path)
    obj['answers']['P'][next(iter(answers))]['sections']['need'] = 'changed'
    path.write_bytes(canonical(obj))
    with pytest.raises(ValueError, match='integrity'): p.reveal(store, run)
    path = store.path(p.ref(run, 'packet.json')); obj = b.load(path)
    obj['policy']['prices']['travel']['amount_net_eur'] = '500'
    path.write_bytes(canonical(obj))
    with pytest.raises(ValueError, match='packet_changed'): p.context(store, run, 'P')


@pytest.mark.parametrize('reference', ['../packet.json', '00_raw/gmail/a.eml', '30_cases/blind_eval/' + 'a'*64 + '/frozen.json'])
def test_restricted_publication(experiment, reference):
    with pytest.raises(ValueError, match='path_invalid'): p.ExperimentStore().path(reference)
