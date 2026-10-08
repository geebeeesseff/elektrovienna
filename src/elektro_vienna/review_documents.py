"""Bounded user-supplied PDF comparison; separate from Gmail ingestion and case matching."""

from importlib.resources import files
from importlib.metadata import version
from pathlib import Path

from .models import ProviderError
from .pilot_parse import parse_document
from .pilot_sources import canonical, digest
from .review_ui import checked_json, details, esc


def publish_documents(store, manifest):
    inputs = manifest['documents']
    if not 1 <= len(inputs) <= 10:
        raise ProviderError('review_document_limit')
    outputs, documents, contexts = [], [], []
    if manifest.get('context_view_id'):
        identity = manifest['context_view_id']
        view, _ = checked_json(store,f'30_cases/review_ux/{identity}/view.json',identity)
        ref = view['extraction_reference']
        extraction, _ = checked_json(store,ref,ref.split('/')[-1][:-5])
        for sid in manifest.get('context_source_ids',[]):
            contexts.append({'source':view['sources'][sid],'sections':extraction['documents'][sid]['sections'],
                             'context_view_id':identity,'extraction_reference':ref})
    for item in inputs:
        path = Path(item['path'])
        if path.suffix.lower() != '.pdf' or path.stat().st_size > 20 * 1024 * 1024:
            raise ProviderError('review_document_invalid')
        raw = path.read_bytes()
        if not raw.startswith(b'%PDF-'):
            raise ProviderError('review_document_invalid')
        sha = digest(raw)
        ref = f'00_raw/manual_documents/{sha}.pdf'
        parsed = parse_document(raw, 'application/pdf', path.name)
        extracted = {'format_version': 1, 'source_sha256': sha, 'parser': 'existing_pilot_pdf_parser',
                     'parser_sha256':digest(files('elektro_vienna').joinpath('pilot_parse.py').read_bytes()),
                     'pypdf_version':version('pypdf'), **parsed}
        extraction_raw = canonical(extracted)
        extraction_id = digest(extraction_raw)
        extraction_ref = f'20_extractions/manual_documents/{extraction_id}.json'
        # Curation is separate from extracted source text; every statement carries an exact quote.
        for claim in item['claims']:
            if not claim['text'] or not claim['source_refs']:
                raise ProviderError('review_document_claim_missing')
            for cite in claim['source_refs']:
                sections = parsed['sections']
                if cite.get('context_source_id'):
                    sections = next((d['sections'] for d in contexts if d['source']['id']==cite['context_source_id']),[])
                section = next((s for s in sections if s['locator'] == cite['locator']), None)
                if not section or not cite['quote'] or cite['quote'] not in section['text']:
                    raise ProviderError('review_document_citation_invalid')
        source = dict(id=sha, sha256=sha, byte_length=len(raw), reference=ref, filename=path.name,
                      kind='manual_document', mime_type='application/pdf', supplied_path=str(path),
                      supplied_by=manifest['reviewer'], received_on=manifest['received_on'])
        documents.append({k:v for k,v in item.items() if k != 'path'} | {'source':source,
            'extraction_reference':extraction_ref, 'extraction_sha256':extraction_id})
        outputs.extend([(ref,raw),(extraction_ref,extraction_raw)])
    collection = {'format_version':1, 'kind':'document_comparison', 'title':manifest['title'],
        'received_on':manifest['received_on'], 'reviewer':manifest['reviewer'],
        'curation':'Source-backed manual interpretation; no automatic case association or knowledge promotion.',
        'observations':manifest.get('observations', []), 'documents':documents,'context_documents':contexts,
        'renderer_sha256':digest(files('elektro_vienna').joinpath('review_documents.py').read_bytes()),
        'css_sha256':digest(files('elektro_vienna').joinpath('review.css').read_bytes())}
    data = canonical(collection); identity = digest(data)
    prefix = f'30_cases/document_review/{identity}'
    for ref, raw in outputs:
        store.publish(ref, raw)
    store.publish(prefix+'/index.html', render_documents(collection).encode())
    store.publish(prefix+'/index.json', data)
    return {'reference':prefix+'/index.json', 'html':str(store.path(prefix+'/index.html'))}


def render_documents(collection):
    css = files('elektro_vienna').joinpath('review.css').read_text(encoding='utf-8')
    parts = []
    for d in collection['documents']:
        source = d['source']
        claims = ''.join('<p>'+esc(c['text'])+'</p>'+details('Beleg', ''.join('<pre>'+esc(r['locator']+' · '+r['quote'])+'</pre>' for r in c['source_refs'])) for c in d['claims'])
        parts.append('<article><h2>'+esc(d['title'])+'</h2><p class="certainty">'+esc(d['association'])+'</p>'
                     +claims+'<p><a href="../../../'+esc(source['reference'])+'" target="_blank" rel="noopener">Original-PDF öffnen</a></p></article>')
    observations = ''.join('<p>'+esc(o)+'</p>' for o in collection['observations'])
    for context in collection.get('context_documents',[]):
        row=context['source']
        parts.append('<article><h2>Vergleichsquelle aus dem bestehenden Mailarchiv</h2><p>'+esc(row.get('filename','Rechnung'))+'</p><p><a href="../../../'+esc(row['reference'])+'" target="_blank" rel="noopener">Archiviertes Original-PDF öffnen</a></p>'
                     +details('Dokumenttext', ''.join('<pre>'+esc(s['text'])+'</pre>' for s in context['sections']))+'</article>')
    return ('<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<title>'+esc(collection['title'])+'</title><style>'+css+'</style><header><p class="eyebrow">ELEKTRO VIENNA / DOKUMENTEINORDNUNG</p><h1>'+esc(collection['title'])+'</h1>'
            '<p>'+str(len(collection['documents']))+' einzeln geprüfte Dokumente. Angebote, Rechnungen und Fassungen bleiben getrennt; keine allgemeine Preisliste.</p></header><main><section><h2>Was sich daraus lernen lässt</h2>'+observations+'</section>'
            +''.join(parts)+'</main></html>')
