"""Loopback review: mutable private drafts, explicit immutable human completion."""

import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from threading import RLock
from urllib.parse import urlsplit

from .config import local_app_data, validate_path
from .models import ProviderError
from .pilot_sources import canonical, digest
from .review_ui import checked_json, render_html

MAX_BODY = 1024 * 1024
DRAFT_MODE = 'correction_comments_v2'


class ReviewSession:
    """One recoverable local draft per view; only complete() publishes human decisions."""

    def __init__(self, store, view_id):
        self.store, self.view_id = store, view_id
        self.view, _ = checked_json(store, f'30_cases/review_ux/{view_id}/view.json', view_id)
        self.local_root = local_app_data()
        self.draft_path = validate_path(self.local_root / 'ElektroViennaKnowledge/review/drafts' / f'{view_id}.json', self.local_root)
        self.directory = store.path(f'90_manual_review/completed/{view_id}/00000001.json').parent
        self.lock = RLock()
        self.allowed = {('item',i['id']):i for rows in self.view['sections'].values() for i in rows}
        self.thread_for = {}
        for thread in self.view['conversations']:
            self.allowed[('thread',thread['id'])] = thread
            for message in thread['messages']:
                self.allowed[('message',message['id'])] = message
                self.thread_for[message['id']] = thread['id']

    def normalize(self, review):
        try:
            if (set(review) != {'mode','reviewer','fields'} or review['mode'] != DRAFT_MODE
                    or not isinstance(review['reviewer'], str) or len(review['reviewer']) > 120
                    or not isinstance(review['fields'], list) or len(canonical(review)) > MAX_BODY):
                raise ValueError
            supplied = {}
            for field in review['fields']:
                key = (field['kind'], field['id'])
                if (set(field) != {'kind','id','comment'} or key not in self.allowed or key in supplied
                        or not isinstance(field['comment'], str) or len(field['comment']) > 10000):
                    raise ValueError
                supplied[key] = field['comment']
            return {'mode':DRAFT_MODE, 'reviewer':review['reviewer'].strip(),
                    'fields':[{'kind':kind,'id':identity,'comment':supplied.get((kind,identity),'')}
                              for kind,identity in self.allowed]}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError('review_draft_invalid') from None

    def history(self, namespace):
        directory = self.store.path(f'90_manual_review/{namespace}/{self.view_id}/00000001.json').parent
        if not directory.exists():
            return []
        self.store._guard(directory)
        results, parent = [], None
        for number, path in enumerate(sorted(directory.glob('*.json')), 1):
            self.store._guard(path)
            try:
                envelope = json.loads(path.read_bytes())
                record = envelope['record']; sha = digest(canonical(record))
                if (path.name != f'{number:08d}.json' or record['revision'] != number
                        or record['view_id'] != self.view_id or record['parent_sha256'] != parent
                        or envelope['sha256'] != sha):
                    raise ValueError
                if namespace == 'completed' and (record['kind'] != 'completed_case_review'
                        or record['status'] != 'completed' or record['whole_case_acknowledged'] is not True
                        or record['baseline_id'] != self.view['baseline_id'] or record['case_id'] != self.view['case_id']):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise ProviderError('review_history_integrity_mismatch') from None
            reference = f'90_manual_review/{namespace}/{self.view_id}/{path.name}'
            results.append(record | {'sha256':sha,'reference':reference})
            parent = sha
        return results

    def latest(self):
        with self.lock:
            validate_path(self.draft_path, self.local_root)
            if self.draft_path.exists():
                try:
                    envelope = json.loads(self.draft_path.read_bytes()); record = envelope['record']
                    if (digest(canonical(record)) != envelope['sha256'] or record['view_id'] != self.view_id
                            or record['status'] != 'draft' or type(record['revision']) is not int or record['revision'] < 1):
                        raise ValueError
                    self.normalize(record['review'])
                except (ValueError, KeyError, TypeError):
                    raise ProviderError('review_draft_integrity_mismatch') from None
            else:
                # Import comments only. Legacy status/ack/overlay values stay in history.
                legacy = self.history('sessions')
                review = {'mode':DRAFT_MODE,'reviewer':'','fields':[]}
                provenance = self.view.get('prior_review')
                if legacy:
                    old = legacy[-1]
                    review = {'mode':DRAFT_MODE,'reviewer':old['review']['reviewer'],
                              'fields':[{k:f[k] for k in ('kind','id','comment')} for f in old['review']['fields']]}
                    provenance = {'reference':old['reference'],'sha256':old['sha256']}
                record = {'kind':'local_case_review_draft','status':'draft','view_id':self.view_id,
                          'revision':0,'review':self.normalize(review),'prior_review':provenance}
            completed = self.history('completed')
            return record | {'latest_completed':completed[-1] if completed else None}

    def save(self, base_revision, review):
        with self.lock:
            review = self.normalize(review)
            current = self.latest()
            if type(base_revision) is not int:
                raise ProviderError('review_revision_conflict')
            if self.draft_path.exists() and current['review'] == review:
                return current
            if base_revision != current['revision']:
                raise ProviderError('review_revision_conflict')
            record = {'format_version':2,'kind':'local_case_review_draft','status':'draft',
                      'view_id':self.view_id,'case_id':self.view['case_id'],'baseline_id':self.view['baseline_id'],
                      'revision':current['revision']+1,'saved_at':datetime.now(timezone.utc).isoformat(),
                      'review':review,'prior_review':current.get('prior_review'),
                      'effect':'work_in_progress_only; no_acceptance_or_source_confirmation'}
            validate_path(self.draft_path, self.local_root)
            self.draft_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix='.draft-', suffix='.tmp', dir=self.draft_path.parent)
            try:
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(canonical({'record':record,'sha256':digest(canonical(record))}))
                    handle.flush(); os.fsync(handle.fileno())
                validate_path(self.draft_path, self.local_root)
                os.replace(name, self.draft_path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return record | {'latest_completed':current['latest_completed']}

    def complete(self, base_revision, whole_case_acknowledged, sources_acknowledged):
        with self.lock:
            if whole_case_acknowledged is not True or type(sources_acknowledged) is not bool:
                raise ProviderError('review_completion_ack_required')
            current = self.latest()
            if type(base_revision) is not int or base_revision != current['revision']:
                raise ProviderError('review_revision_conflict')
            review = self.normalize(current['review'])
            if not review['reviewer']:
                raise ProviderError('review_reviewer_missing')
            submission = {'view_id':self.view_id,'draft_revision':base_revision,'review':review,
                          'whole_case_acknowledged':True,'sources_acknowledged':sources_acknowledged}
            submission_sha = digest(canonical(submission))
            history = self.history('completed')
            existing = next((r for r in history if r['submission_sha256'] == submission_sha), None)
            if existing:
                return existing
            comments = {(f['kind'],f['id']):f['comment'].strip() for f in review['fields']}
            decisions = []
            for field in review['fields']:
                kind, identity = field['kind'], field['id']
                comment = comments[(kind,identity)]
                if kind == 'item':
                    state = 'correction_requested' if comment else 'accepted_by_default'
                elif not sources_acknowledged:
                    state = 'unreviewed'
                elif comment:
                    state = 'correction_requested'
                elif kind == 'message' and comments[('thread',self.thread_for[identity])]:
                    state = 'unreviewed'
                elif kind == 'thread' and any(comments[('message',m['id'])] for m in self.allowed[(kind,identity)]['messages']):
                    state = 'human_confirmed_with_exceptions'
                else:
                    state = 'human_confirmed'
                decisions.append(field | {'state':state})
            record = {'format_version':2,'kind':'completed_case_review','status':'completed',
                      'view_id':self.view_id,'case_id':self.view['case_id'],'baseline_id':self.view['baseline_id'],
                      'revision':len(history)+1,'parent_sha256':history[-1]['sha256'] if history else None,
                      'completed_at':datetime.now(timezone.utc).isoformat(),'reviewer':review['reviewer'],
                      'review':review,'draft_revision':base_revision,'submission_sha256':submission_sha,
                      'whole_case_acknowledged':True,'sources_acknowledged':sources_acknowledged,
                      'decisions':decisions,'source_exceptions':[f for f in review['fields'] if f['kind']!='item' and f['comment'].strip()],
                      'prior_review':current.get('prior_review'),
                      'previous_completed_reference':history[-1]['reference'] if history else None,
                      'effect':'input_to_agent_case_revision; no_source_airtable_or_global_knowledge_mutation'}
            sha = digest(canonical(record))
            reference = f'90_manual_review/completed/{self.view_id}/{record["revision"]:08d}.json'
            self.store.publish(reference,canonical({'record':record,'sha256':sha}))
            return record | {'sha256':sha,'reference':reference}


def make_server(store, view_id, port=0):
    session = ReviewSession(store, view_id)
    token = secrets.token_urlsafe(32)
    prefix = f'/s/{token}/'
    view = session.view
    assets = {n:files('elektro_vienna').joinpath(n).read_text(encoding='utf-8') for n in ('review.css','review.js')}
    allowed = {s['reference']:s for s in view['sources'].values()}
    collection = None
    if view.get('document_collection_reference'):
        ref = view['document_collection_reference']
        collection, _ = checked_json(store, ref, ref.split('/')[-2])
        allowed.update({d['source']['reference']:d['source'] for d in collection['documents']})
        allowed.update({d['source']['reference']:d['source'] for d in collection.get('context_documents',[])})
    routes = {f'original/{i}':row for i,row in enumerate(allowed.values())}
    urls = {row['reference']:prefix+route for route,row in routes.items()}

    def rewrite(page):
        for ref, url in urls.items():
            page = page.replace('../../../'+ref, url)
        if view.get('document_collection_reference'):
            page = page.replace('../../../'+view['document_collection_reference'].replace('index.json','index.html'),prefix+'documents')
        return page

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # no customer content or capability URLs in logs

        def respond(self, status, body, mime='application/json', disposition=None):
            data = body if isinstance(body,bytes) else canonical(body)
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"frame-ancestors 'none'")
            if disposition:self.send_header('Content-Disposition',disposition)
            self.end_headers(); self.wfile.write(data)

        def route(self):
            authority = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != authority:
                return None
            if self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
                return None
            path = urlsplit(self.path).path
            if not path.startswith(prefix) or '%' in path or '..' in path:
                return None
            return path[len(prefix):]

        def do_GET(self):
            route = self.route()
            try:
                if route == '':
                    page = render_html(view,view_id,assets,live={'api':prefix+'draft','complete_api':prefix+'complete'})
                    self.respond(200,rewrite(page).encode(),'text/html; charset=utf-8')
                elif route == 'draft':
                    self.respond(200,session.latest())
                elif route == 'documents' and collection:
                    from .review_documents import render_documents
                    self.respond(200,rewrite(render_documents(collection)).encode(),'text/html; charset=utf-8')
                elif route in routes:
                    row = routes[route]
                    from .archive_store import SourceArchive
                    owner = store if row['reference'].startswith('00_raw/manual_documents/') else SourceArchive(store.root)
                    data = owner.path(row['reference']).read_bytes()
                    if digest(data) != row['sha256']:
                        raise ProviderError('review_source_integrity_mismatch')
                    pdf = row.get('mime_type') == 'application/pdf'
                    self.respond(200,data,'application/pdf' if pdf else 'application/octet-stream',
                                 'inline; filename="source.pdf"' if pdf else 'attachment; filename="source.eml"' if row.get('kind')=='message' else 'attachment; filename="source.bin"')
                else:self.respond(404,{'error':'not_found'})
            except (OSError,ValueError,ProviderError,KeyError):
                self.respond(500,{'error':'local_read_failed'})

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= MAX_BODY or self.headers.get('Transfer-Encoding'):
                    self.respond(413,{'error':'request_size_invalid'});return
                raw = self.rfile.read(length)
                # Drain bounded bodies before rejecting: closing over unread bytes may reset
                # the Windows TCP connection before the caller receives the error response.
                route = self.route()
                if (route not in ('draft','complete') or self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}'
                        or self.headers.get('Content-Type') != 'application/json'):
                    self.respond(403,{'error':'request_not_allowed'});return
                data = json.loads(raw)
                if route == 'draft':
                    self.respond(200,session.save(data['base_revision'],data['review']))
                else:
                    self.respond(200,session.complete(data['base_revision'],data['whole_case_acknowledged'],data['sources_acknowledged']))
            except ProviderError as exc:
                self.respond(409 if str(exc)=='review_revision_conflict' else 400,{'error':str(exc)})
            except (ValueError,TypeError,KeyError):
                self.respond(400,{'error':'invalid_request'})
            except OSError:
                self.respond(500,{'error':'local_save_failed'})

    server = HTTPServer(('127.0.0.1',port),Handler)
    server.session = session
    server.review_url = f'http://127.0.0.1:{server.server_port}{prefix}'
    return server
