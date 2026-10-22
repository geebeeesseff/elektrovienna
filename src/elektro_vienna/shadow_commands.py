"""Manual Pending Replies and preserved review commands; no business write adapter."""

import json

from .blind_eval import require
from .config import Config, local_app_data, validate_path
from .gmail import GmailReader, authenticate
from .shadow_cards import import_feedback, publish_card, render_batch
from .shadow_input import guidance, runtime_file, save_checkpoint
from .shadow_store import ShadowStore
from .state import single_operator


def run(args):
    store = ShadowStore()
    checkpoint = runtime_file('checkpoint.json')
    with single_operator(runtime_file('check.lock')):
        if args.command == 'shadow-feedback':
            raw = args.file.read_bytes()
            if json.loads(raw).get('kind') == 'operational_feedback':
                from .operational_replies import OperationalEvidence
                evidence = OperationalEvidence(store, runtime_file('operational.json'))
                reference = evidence.import_feedback(raw)
                return {'feedback_reference': reference, 'external_actions': []}
            reference = import_feedback(store, raw)
            state = json.loads(checkpoint.read_bytes())
            if reference not in state['reviews']:
                state['reviews'].append(reference)
                save_checkpoint(checkpoint, state)
            return {'feedback_reference': reference, 'external_actions': []}
        if args.command == 'shadow-check':
            from .pending_replies import reconcile, atomic_view
            config = Config.from_environment()
            contexts = guidance(args.guidance_root.resolve())
            state = json.loads(checkpoint.read_bytes()) if checkpoint.exists() else {}
            feedback = []
            for reference in state.get('reviews', []):
                review = store.read(reference)
                feedback.append({'reference': reference, 'reviewer': review.get('reviewer'),
                    'items': [i for i in review.get('items', []) if i.get('choice') or i.get('comment')]})
            try:
                from .operational_replies import OperationalEvidence
                operational = OperationalEvidence(store, runtime_file('operational.json'),
                    snapshot_path=getattr(args, 'airtable_snapshot', None))
                with authenticate(config) as session:
                    return reconcile(GmailReader(session), store, runtime_file('pending.json'), contexts, feedback=feedback,
                        operational=operational, current_candidates_only=getattr(args, 'current_candidates_only', False))
            except Exception:
                atomic_view(store, '<!doctype html><meta charset="utf-8"><title>Pending Replies refresh failed</title>'
                    '<h1>Pending Replies could not be refreshed</h1>'
                    '<p>The previous snapshot has not been presented as current. Check the command error and retry.</p>')
                raise
        else:
            state = json.loads(checkpoint.read_bytes())
            path = validate_path(args.plan, local_app_data())
            plans = json.loads(path.read_bytes())
            require(isinstance(plans, list) and 1 <= len(plans) <= 10 and
                    len({p['input_reference'] for p in plans}) == len(plans), 'one_to_ten_unique_cards')
            published = {}
            for plan in plans:
                require(plan['input_reference'] in state['done'].values(), 'input_not_in_live_pilot')
                published[plan['input_reference']] = publish_card(store, plan, args.guidance_root.resolve())
            state['cards'].update(published)
            save_checkpoint(checkpoint, state)
            result = {'published_cards': list(published.values())}
        return {**result, **render_batch(store, state), 'checkpoint_path': str(checkpoint), 'external_actions': []}
