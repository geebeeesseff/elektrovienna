"""Small versioned operational policy contract; no inference or rules engine."""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

CATEGORIES = {'customer_friction', 'technical_escalation', 'workforce', 'commercial',
              'b2b', 'procurement', 'site_visits', 'case_state'}
STATUSES = {'owner_supplied', 'confirmed', 'uncertain', 'requires_owner_confirmation'}


def _require(condition):
    if not condition:
        raise ValueError('operating_policy_invalid')


def validate(policy):
    try:
        _require(policy['format_version'] == 1)
        _require(re.fullmatch(r'\d{4}-\d{2}-\d{2}\.\d+(?:-draft)?', policy['version']))
        date.fromisoformat(policy['effective_from'])
        _require(policy['status'] in {'owner_supplied_draft', 'confirmed'})
        _require(set(policy['categories']) == CATEGORIES)
        _require(policy['sources'] and policy['scope'])
        items = policy['categories'] | policy['prices']
        _require(len(items) == len(policy['categories']) + len(policy['prices']))
        for item in items.values():
            _require(item['status'] in STATUSES)
            _require(item['source_refs'] and set(item['source_refs']) <= set(policy['sources']))
        for item in policy['categories'].values():
            _require(item['rules'] and all(isinstance(r, str) and r.strip() for r in item['rules']))
        for item in policy['prices'].values():
            _require(isinstance(item['amount_net_eur'], str))
            amount = Decimal(item['amount_net_eur'])
            _require(amount.is_finite() and amount >= 0 and item['unit'] and item['applicability'])
        for conflict in policy['conflicts']:
            _require(conflict['item'] in items and conflict['status'] == 'requires_owner_confirmation')
            _require(items[conflict['item']]['status'] == 'requires_owner_confirmation')
            _require(len(conflict['claims']) >= 2)
            for claim in conflict['claims']:
                _require(claim['text'] and claim['source_refs'])
                _require(set(claim['source_refs']) <= set(policy['sources']))
        for key, item in items.items():
            if item['status'] == 'requires_owner_confirmation':
                _require(any(c['item'] == key for c in policy['conflicts']))
        return policy
    except (AssertionError, KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError('operating_policy_invalid') from None


def load_policy(path=None):
    path = Path(path) if path else Path(__file__).with_name('operating_policy.json')
    return validate(json.loads(path.read_text('utf-8')))


def price(policy, item_id, *, on_date):
    """Explicit date and unresolved-state guard; no historical-price fallback."""
    validate(policy)
    if date.fromisoformat(on_date) < date.fromisoformat(policy['effective_from']):
        raise ValueError('policy_not_yet_effective')
    item = policy['prices'][item_id]
    if item['status'] not in {'owner_supplied', 'confirmed'}:
        raise ValueError('price_requires_owner_confirmation')
    return Decimal(item['amount_net_eur'])
