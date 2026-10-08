"""Synthetic local drafts and explicit completion. No real customer decisions or providers."""
import http.client
import json
import socket
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from test_pilot import setup
from test_review_ui import review_setup
from elektro_vienna.models import ProviderError
from elektro_vienna.pilot_sources import canonical, digest
from elektro_vienna.review_ui import ReviewStore, publish_view, build_view
from elektro_vienna.review_local import ReviewSession, make_server, DRAFT_MODE
from elektro_vienna import review_documents, review_local

CONNECT = socket.create_connection


@pytest.fixture(autouse=True)
def private_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(review_local, 'local_app_data', lambda: tmp_path / 'private')


@pytest.fixture
def session(review_setup):
    run,p,state,root = review_setup
    result=publish_view(ReviewStore(),run['processing_run'],p['case_id'],p)
    return ReviewSession(ReviewStore(),result['view_id']),state,root


def draft(comment='Check scope', reviewer='Synthetic Reviewer'):
    return {'mode':DRAFT_MODE,'reviewer':reviewer,'fields':[{'kind':'item','id':'summary','comment':comment}]}


def test_woodward_review_draft_recovery_then_explicit_completion(session):
    s,state,root=session
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()};state_before=state.read_bytes()
    first=s.save(0,draft())
    assert first['status']=='draft' and 'interpretations' not in first and 'decisions' not in first
    assert first['latest_completed'] is None
    assert not s.directory.exists()
    assert not s.draft_path.is_relative_to(root)
    lower=next(f for f in first['review']['fields'] if f['id']=='outcome')
    assert lower['comment']=='' and 'state' not in lower
    assert s.save(0,draft())==first
    reopened=ReviewSession(s.store,s.view_id)
    assert reopened.latest()==first
    done=reopened.complete(first['revision'], True, False)
    states={(f['kind'],f['id']):f['state'] for f in done['decisions']}
    assert states[('item','summary')]=='correction_requested'
    assert states[('item','outcome')]=='accepted_by_default'
    assert all(value=='unreviewed' for (kind,_),value in states.items() if kind!='item')
    assert done['status']=='completed' and done['whole_case_acknowledged'] is True
    assert done['baseline_id']==s.view['baseline_id'] and done['case_id']==s.view['case_id']
    assert reopened.complete(first['revision'],True,False)==done
    assert len(list(s.directory.glob('*.json')))==1
    assert all(p.read_bytes()==raw for p,raw in before.items()) and state.read_bytes()==state_before


def test_changed_review_adds_one_new_completion_and_mutates_only_one_draft(session):
    s,_,_=session
    first=s.save(0,draft());done=s.complete(first['revision'],True,False)
    old=s.store.path(done['reference']).read_bytes()
    updated=s.save(first['revision'],draft('Different correction'))
    assert updated['latest_completed']['reference']==done['reference']
    with pytest.raises(ProviderError,match='conflict'):s.save(0,draft('Stale'))
    with pytest.raises(ProviderError,match='conflict'):s.complete(first['revision'],True,False)
    second=s.complete(updated['revision'],True,True)
    assert second['previous_completed_reference']==done['reference']
    assert second['parent_sha256']==done['sha256']
    assert len(list(s.directory.glob('*.json')))==2
    assert len(list(s.draft_path.parent.glob('*.json')))==1
    assert s.store.path(done['reference']).read_bytes()==old


@pytest.mark.parametrize('change',['foreign','duplicate','hidden-status','replacement','bad-mode','long-name','html'])
def test_draft_only_accepts_current_comment_interaction(session,change):
    s,_,_=session;r=draft()
    if change=='foreign':r['fields'][0]['id']='another-case'
    if change=='duplicate':r['fields']*=2
    if change=='hidden-status':r['fields'][0]['status']='correct'
    if change=='replacement':r['fields'][0]['replacement']='Hidden prose'
    if change=='bad-mode':r['mode']='correction_comments_v1'
    if change=='long-name':r['reviewer']='a'*121
    if change=='html':
        r['fields'][0]['comment']='<script>alert(1)</script>'
        assert next(f for f in s.save(0,r)['review']['fields'] if f['id']=='summary')['comment']==r['fields'][0]['comment']
    else:
        with pytest.raises(ProviderError):s.save(0,r)
        assert not s.draft_path.exists() and not s.directory.exists()


@pytest.mark.parametrize('whole,sources',[(False,False),(None,False),(1,False),(True,1)])
def test_completion_requires_explicit_boolean_acknowledgements(session,whole,sources):
    s,_,_=session;s.save(0,draft())
    with pytest.raises(ProviderError,match='ack_required'):s.complete(1,whole,sources)
    assert not s.directory.exists()


def test_anonymous_draft_recovers_but_cannot_complete(session):
    s,_,_=session;s.save(0,draft(reviewer=' '))
    assert ReviewSession(s.store,s.view_id).latest()['review']['reviewer']==''
    with pytest.raises(ProviderError,match='reviewer_missing'):s.complete(1,True,False)
    assert not s.directory.exists()


@pytest.mark.parametrize('scope',['none','thread','message'])
def test_source_acknowledgement_and_comment_exceptions(session,scope):
    s,_,_=session;r=draft('')
    thread=s.view['conversations'][0];message=thread['messages'][0]
    if scope!='none':r['fields'].append({'kind':scope,'id':thread['id'] if scope=='thread' else message['id'],'comment':'Wrong source / mixed topic'})
    s.save(0,r);done=s.complete(1,True,True)
    states={(f['kind'],f['id']):f['state'] for f in done['decisions']}
    assert states[('thread',thread['id'])]=={'none':'human_confirmed','thread':'correction_requested','message':'human_confirmed_with_exceptions'}[scope]
    assert states[('message',message['id'])]=={'none':'human_confirmed','thread':'unreviewed','message':'correction_requested'}[scope]
    assert len(done['source_exceptions'])==(scope!='none')
    no_ack=s.complete(1,True,False)
    assert all(f['state']=='unreviewed' for f in no_ack['decisions'] if f['kind']!='item')


def test_legacy_history_migrates_comments_only_and_is_never_rewritten(session):
    s,_,_=session
    old={'kind':'case_review_draft','view_id':s.view_id,'revision':1,'parent_sha256':None,
         'review':{'reviewer':'Original human','fields':[{'kind':'item','id':'summary','comment':'Keep exact typo','status':'partially_correct','apply':True,'replacement':'Old prose'}]}}
    raw=canonical({'record':old,'sha256':digest(canonical(old))})
    ref=f'90_manual_review/sessions/{s.view_id}/00000001.json';s.store.publish(ref,raw)
    migrated=s.latest()
    assert migrated['prior_review']['reference']==ref
    assert next(f for f in migrated['review']['fields'] if f['id']=='summary')=={'kind':'item','id':'summary','comment':'Keep exact typo'}
    assert migrated['latest_completed'] is None and 'decisions' not in migrated
    s.save(0,migrated['review'])
    assert s.store.path(ref).read_bytes()==raw
    assert not s.directory.exists()


def test_crash_before_atomic_replace_keeps_previous_draft(session,monkeypatch):
    s,_,_=session;s.save(0,draft());old=s.draft_path.read_bytes()
    def fail(*args):raise OSError('synthetic replace failure')
    monkeypatch.setattr(review_local.os,'replace',fail)
    with pytest.raises(OSError):s.save(1,draft('Interrupted edit'))
    assert s.draft_path.read_bytes()==old
    assert not list(s.draft_path.parent.glob('*.tmp'))


def test_completion_retry_after_lost_response_does_not_duplicate(session,monkeypatch):
    s,_,_=session;s.save(0,draft());publish=s.store.publish
    def interrupted(ref,raw):publish(ref,raw);raise OSError('lost acknowledgment')
    monkeypatch.setattr(s.store,'publish',interrupted)
    with pytest.raises(OSError):s.complete(1,True,False)
    monkeypatch.setattr(s.store,'publish',publish)
    result=s.complete(1,True,False)
    assert result['revision']==1 and len(list(s.directory.glob('*.json')))==1


@pytest.mark.parametrize('target',['draft','completed','legacy'])
def test_corruption_blocks_reads_without_repair(session,target):
    s,_,_=session;s.save(0,draft())
    if target=='draft':path=s.draft_path
    elif target=='completed':path=s.store.path(s.complete(1,True,False)['reference'])
    else:
        s.draft_path.unlink();path=s.store.path(f'90_manual_review/sessions/{s.view_id}/00000001.json');path.parent.mkdir(parents=True)
    path.write_bytes(b'{}')
    with pytest.raises(ProviderError,match='integrity'):s.latest()
    assert path.read_bytes()==b'{}'


def test_live_form_keeps_comment_ui_and_requires_separate_completion(session):
    from elektro_vienna.review_ui import render_html
    from importlib.resources import files
    s,_,_=session
    assets={n:files('elektro_vienna').joinpath(n).read_text(encoding='utf-8') for n in ('review.css','review.js')}
    page=render_html(s.view,s.view_id,assets,live={'api':'/draft','complete_api':'/complete'})
    assert 'Korrekturkommentar' in page and 'Fallprüfung abschließen' in page
    assert 'id="whole-case-ack"' in page and 'id="sources-ack"' in page
    assert '<select' not in page and 'class="replacement"' not in page
    assert 'Automatisches Speichern sichert nur deinen Entwurf.' in page
    assert '<select' in render_html(s.view,s.view_id,assets)


def test_agent_iteration_binds_completed_review_and_rejects_forged_reference(review_setup):
    run,p,_,_=review_setup
    store=ReviewStore();original=publish_view(store,run['processing_run'],p['case_id'],p)
    s=ReviewSession(store,original['view_id']);s.save(0,draft());done=s.complete(1,True,False)
    p['prior_review']={'reference':done['reference'],'sha256':done['sha256']}
    revised=publish_view(store,run['processing_run'],p['case_id'],p)
    assert revised['view_id']!=original['view_id']
    assert publish_view(store,run['processing_run'],p['case_id'],p)==revised
    assert len(s.history('completed'))==1
    p['prior_review']['sha256']='0'*64
    with pytest.raises(ProviderError,match='prior_feedback'):build_view(store,run['processing_run'],p['case_id'],p)


def test_http_is_loopback_only_scoped_and_requires_origin(session,monkeypatch):
    s,_,_=session
    def local_connect(address,*args,**kwargs):
        assert address[0]=='127.0.0.1'
        return CONNECT(address,*args,**kwargs)
    monkeypatch.setattr(socket,'create_connection',local_connect)
    with make_server(s.store,s.view_id) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url=urlsplit(server.review_url);authority=url.netloc;prefix=url.path
        def request(method,path,body=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            connection.request(method,path,body=body,headers=headers or {})
            response=connection.getresponse();result=response.status,response.read(),dict(response.getheaders());connection.close();return result
        try:
            code,page,headers=request('GET',prefix);assert code==200
            assert b'Jetzt speichern' in page and b'Zur Quellmail' in page
            assert b'connect-src &#x27;self&#x27;' in page and headers['Cache-Control']=='no-store'
            assert request('GET','/')[0]==404
            assert request('GET',prefix+'../draft')[0]==404
            assert request('GET',prefix,headers={'Host':'attacker.invalid'})[0]==404
            assert request('GET',prefix,headers={'Sec-Fetch-Site':'cross-site'})[0]==404
            body=canonical({'base_revision':0,'review':draft()})
            assert request('POST',prefix+'draft',body,{'Content-Type':'application/json'})[0]==403
            assert request('POST',prefix+'complete',body,{'Content-Type':'application/json','Origin':'https://attacker.invalid'})[0]==403
            headers={'Content-Type':'application/json','Origin':'http://'+authority}
            assert request('POST',prefix+'draft',body,headers)[0]==200
            assert json.loads(request('GET',prefix+'draft')[1])['review']==s.normalize(draft())
            assert not s.directory.exists()
            assert request('POST',prefix+'draft',canonical({'base_revision':0,'review':draft('Stale')}),headers)[0]==409
            assert request('POST',prefix+'complete',canonical({'base_revision':1,'whole_case_acknowledged':False,'sources_acknowledged':False}),headers)[0]==400
            submission=canonical({'base_revision':1,'whole_case_acknowledged':True,'sources_acknowledged':False})
            assert request('POST',prefix+'complete',submission,headers)[0]==200
            assert request('POST',prefix+'complete',submission,headers)[0]==200
            assert len(list(s.directory.glob('*.json')))==1
            assert request('POST',prefix+'draft',b'{bad json',headers)[0]==400
        finally:server.shutdown();worker.join(timeout=5)


def test_documents_import_preserves_inputs_and_scopes_case_refs(review_setup,tmp_path,monkeypatch):
    run,p,_,root=review_setup
    pdf=tmp_path/'synthetic.pdf';raw=b'%PDF-synthetic bytes';pdf.write_bytes(raw)
    monkeypatch.setattr(review_documents,'parse_document',lambda *a:{'sections':[{'locator':'page:1:text','text':'Customer quote 123 EUR.'}],'issues':[]})
    manifest={'title':'Synthetic comparison','reviewer':'Tester','received_on':'2026-10-07','observations':[],
      'documents':[{'path':str(pdf),'case_id':p['case_id'],'title':'Synthetic quote','association':'Candidate',
                    'claims':[{'text':'A quote, not an invoice.','source_refs':[{'locator':'page:1:text','quote':'123 EUR'}]}]}]}
    store=ReviewStore();first=review_documents.publish_documents(store,manifest)
    assert review_documents.publish_documents(store,manifest)==first
    assert pdf.read_bytes()==raw
    p['document_collection_reference']=first['reference']
    ref={'source_id':digest(raw),'locator':'page:1:text','start':15,'end':22,'quote':'123 EUR'}
    assert 'Customer quote 123 EUR.'[15:22]=='123 EUR'
    p['sections']['commercial'][0]['source_refs']=[ref]
    view,*_=build_view(store,run['processing_run'],p['case_id'],p)
    assert digest(raw) in view['sources']
    result=publish_view(store,run['processing_run'],p['case_id'],p)
    assert 'Original öffnen' in Path(result['html']).read_text(encoding='utf-8')
    manifest['documents'][0]['case_id']='different-case'
    second=review_documents.publish_documents(store,manifest)
    p['document_collection_reference']=second['reference']
    with pytest.raises(ProviderError,match='citation'):build_view(store,run['processing_run'],p['case_id'],p)
