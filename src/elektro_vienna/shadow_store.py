"""Immutable local shadow artifacts and attributable human evidence; no provider writes."""

import json
import re
from datetime import datetime

from .archive_store import SourceArchive
from .blind_eval import require
from .first_response_validation import ValidationStore
from .pilot_sources import canonical, digest


class ShadowStore(SourceArchive):
    def path(self, reference):
        require(bool(re.fullmatch(
            r'(?:30_cases/live_shadow/[0-9a-f]{64}/(?:input.json|card.json|review.html|batch.json)|'
            r'90_manual_review/live_shadow/[0-9a-f]{64}/(?:original.json|owner-request.txt|result.json))',
            reference)), 'shadow_path_invalid')
        target = self.root.joinpath(*reference.split('/'))
        self._guard(target)
        return target

    def document(self, name, value, *, review=False):
        raw = canonical(value)
        prefix = '90_manual_review' if review else '30_cases'
        reference = f'{prefix}/live_shadow/{digest(raw)}/{name}.json'
        self.publish(reference, raw)
        return reference

    def read(self, reference):
        raw = self.path(reference).read_bytes()
        require(digest(raw) == reference.split('/')[-2], 'shadow_integrity')
        return json.loads(raw)


def preserve_validation(store, raw, owner_request):
    require(isinstance(owner_request, bytes) and bool(owner_request.strip()), 'owner_provenance_required')
    review = json.loads(raw)
    require(review['kind'] == 'first_response_validation_review' and review['format_version'] == 1
            and review['complete'] is True and review['reviewer'] == 'Giovanni Superti-Furga',
            'validation_review_invalid')
    require(datetime.fromisoformat(review['recorded_at'].replace('Z', '+00:00')).utcoffset() is not None,
            'validation_timestamp')
    validation = ValidationStore(store.root)
    packet_raw = validation.path(f'30_cases/first_response_validation/{review["set"]}/packet.json').read_bytes()
    require(digest(packet_raw) == review['set'], 'validation_packet_integrity')
    frozen_raw = validation.path(f'30_cases/first_response_validation/{review["set"]}/answers.json').read_bytes()
    frozen = json.loads(frozen_raw)
    require(frozen['packet_sha256'] == review['set'] and
            digest(canonical(frozen['answers'])) == review['answers_sha256'] == frozen['answers_sha256'],
            'validation_answers_integrity')
    ids = {i['id'] for i in json.loads(packet_raw)['inquiries']}
    require(len(review['cases']) == len(ids) == 3 and {c['example_id'] for c in review['cases']} == ids,
            'validation_cases')
    require(all(c['choice'] == 'send' and c['comment'] == '' for c in review['cases']), 'owner_result_mismatch')
    original = f'90_manual_review/live_shadow/{digest(raw)}/original.json'
    request = f'90_manual_review/live_shadow/{digest(owner_request)}/owner-request.txt'
    store.publish(original, raw)
    store.publish(request, owner_request)
    result = {'kind': 'owner_final_validation', 'format_version': 1, 'reviewer': review['reviewer'],
              'recorded_at': review['recorded_at'], 'original_reference': original,
              'original_sha256': digest(raw), 'owner_request_reference': request,
              'owner_request_sha256': digest(owner_request), 'validation_run': review['set'],
              'frozen_file_sha256': digest(frozen_raw), 'answers_sha256': review['answers_sha256'],
              'cases': review['cases'], 'approved': 3, 'edits': 0, 'rejected': 0,
              'interpretation': 'Small human validation passed; owner ends historical testing and authorizes a live shadow pilot. No statistical proof; no external action authorized.'}
    return store.document('result', result, review=True)
