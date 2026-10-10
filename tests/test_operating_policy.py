import copy

import pytest

from elektro_vienna.operating_policy import load_policy, price, validate


def test_owner_prices_and_date_are_explicit():
    p = load_policy()
    assert price(p, 'single_hour', on_date='2026-10-09') == 100
    assert price(p, 'team_hour', on_date='2026-10-09') == 170
    assert price(p, 'single_first_hour_travel', on_date='2026-10-09') == 150
    assert p['prices']['consultation_credit']['unit'] == 'conditional_credit'
    with pytest.raises(ValueError, match='not_yet_effective'):
        price(p, 'single_hour', on_date='2026-10-08')


def test_conflicting_current_claims_preserved_and_price_blocked():
    p = load_policy()
    p['sources']['document'] = {'kind': 'current_document', 'reference': 'synthetic.md'}
    p['prices']['single_hour']['status'] = 'requires_owner_confirmation'
    p['conflicts'] = [{'item': 'single_hour', 'status': 'requires_owner_confirmation', 'claims': [
        {'text': '100 EUR owner claim', 'source_refs': ['owner-2026-10-09']},
        {'text': '90 EUR document claim', 'source_refs': ['document']}]}]
    before = copy.deepcopy(p)
    assert validate(p) == before
    with pytest.raises(ValueError, match='requires_owner_confirmation'):
        price(p, 'single_hour', on_date='2026-10-09')
    assert p == before


@pytest.mark.parametrize('bad', ['date', 'missing_category', 'no_source', 'nan', 'negative', 'conflict', 'status'])
def test_invalid_policy_fails_closed(bad):
    p = load_policy()
    if bad == 'date': p['effective_from'] = 'yesterday'
    if bad == 'missing_category': p['categories'].pop('b2b')
    if bad == 'no_source': p['prices']['travel']['source_refs'] = ['historical-average']
    if bad == 'nan': p['prices']['travel']['amount_net_eur'] = 'NaN'
    if bad == 'negative': p['prices']['travel']['amount_net_eur'] = '-1'
    if bad == 'conflict': p['prices']['travel']['status'] = 'requires_owner_confirmation'
    if bad == 'status': p['categories']['workforce']['status'] = 'guessed'
    with pytest.raises(ValueError, match='invalid'): validate(p)
