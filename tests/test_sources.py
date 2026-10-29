from pathlib import Path
import json
from datetime import timedelta
from unittest.mock import patch
import pytest
from job_agent.core import connect, import_job, preferred, add_fact, batch_match
from job_agent.sources import *

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'db.sqlite'); yield c; c.close()

def permit(db,provider='greenhouse'):
    return register_permission(db,provider=provider,board='fixture',company='Fixture Company',
        evidence='Offline test evidence only',official_url='https://careers.example.com',
        expires_at=stamp(now()+timedelta(days=1)),confirmed=True)

def gh(ident=1,title='Analyst'):
    return {'id':ident,'title':title,'location':{'name':'Tokyo'},
            'absolute_url':f'https://boards.greenhouse.io/fixture/jobs/{ident}',
            'content':'&lt;p&gt;SQL&lt;/p&gt;'}

class Fake:
    def __init__(self,*answers): self.answers=list(answers); self.urls=[]
    def get(self,url):
        self.urls.append(url)
        value=self.answers.pop(0)
        if isinstance(value,Exception): raise value
        return value

def old_run(db):
    with db: db.execute('UPDATE sync_runs SET started_at=?',(stamp(now()-timedelta(minutes=10)),))

def test_permission_lifecycle(db):
    ident=permit(db); assert require_permission(db,ident)['board']=='fixture'
    revoke_permission(db,ident)
    with pytest.raises(PermissionError): sync_board(db,ident,transport=Fake())

def test_missing_and_expired(db):
    with pytest.raises(PermissionError): sync_board(db,1,transport=Fake())
    ident=permit(db)
    with db: db.execute('UPDATE source_permissions SET expires_at=?',(stamp(now()-timedelta(seconds=1)),))
    with pytest.raises(PermissionError): sync_board(db,ident,transport=Fake())

@pytest.mark.parametrize('provider,board',[('linkedin','x'),('openwork','x'),('greenhouse','../x'),('lever','x?skip=0'),('ashby','https://evil.com')])
def test_fixed_endpoints(provider,board):
    with pytest.raises(ValueError): endpoint(provider,board)

def test_explicit_permission(db):
    with pytest.raises(ValueError):
        register_permission(db,provider='lever',board='x',company='X',evidence='public',official_url='https://example.com',expires_at=stamp(now()+timedelta(days=1)))

def test_platform_not_official(db):
    with pytest.raises(ValueError):
        register_permission(db,provider='lever',board='x',company='X',evidence='public',official_url='https://www.linkedin.com/jobs/1',expires_at=stamp(now()+timedelta(days=1)),confirmed=True)

def test_import_provenance_and_cache(db):
    ident=permit(db); fake=Fake((200,{'jobs':[gh()]},{}))
    assert sync_board(db,ident,transport=fake)['count']==1
    row=db.execute('SELECT * FROM job_source_links').fetchone()
    assert row['source']=='official' and row['method']=='api'
    assert preferred(db,row['job_id'])['content']=='SQL'
    old_run(db); empty=Fake(); sync_board(db,ident,transport=empty)
    assert empty.urls==[]
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1

def test_recent_run_throttle(db):
    ident=permit(db); sync_board(db,ident,transport=Fake((200,{'jobs':[]},{})))
    with pytest.raises(ValueError,match='5 秒'): sync_board(db,ident,transport=Fake())

def test_failure_does_not_mark_missing(db):
    ident=permit(db); sync_board(db,ident,transport=Fake((200,{'jobs':[gh()]},{})))
    old_run(db)
    with pytest.raises(ValueError): sync_board(db,ident,force=True,transport=Fake((403,None,{})))
    assert db.execute('SELECT active FROM source_postings').fetchone()[0]==1
    assert db.execute('SELECT status FROM sync_runs ORDER BY id DESC').fetchone()[0]=='failed'

def test_successful_missing_is_per_source(db):
    ident=permit(db); sync_board(db,ident,transport=Fake((200,{'jobs':[gh()]},{})))
    old_run(db); sync_board(db,ident,force=True,transport=Fake((200,{'jobs':[]},{})))
    assert db.execute('SELECT active FROM source_postings').fetchone()[0]==0
    assert db.execute('SELECT status FROM jobs').fetchone()[0]=='imported'

def test_invalid_payload_no_partial_import(db):
    ident=permit(db)
    with pytest.raises(ValueError): sync_board(db,ident,transport=Fake((200,{'jobs':[gh(),{'id':2}]},{})))
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0

def test_database_batch_rollback(db,monkeypatch):
    import job_agent.sources as mod
    ident=permit(db); original=mod.import_job; calls=[]
    def fail_second(*args,**kwargs):
        calls.append(1)
        if len(calls)==2: raise ValueError('fixture error')
        return original(*args,**kwargs)
    monkeypatch.setattr(mod,'import_job',fail_second)
    with pytest.raises(ValueError): sync_board(db,ident,transport=Fake((200,{'jobs':[gh(),gh(2)]},{})))
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM source_postings').fetchone()[0]==0

def test_retry_and_redirect(db):
    ident=permit(db); sleeps=[]
    fake=Fake((429,None,{'Retry-After':'2'}),(503,None,{}),(200,{'jobs':[]},{}))
    sync_board(db,ident,transport=fake,sleeper=sleeps.append)
    assert sleeps==[2,2] and len(fake.urls)==3
    old_run(db)
    with pytest.raises(ValueError,match='302'):
        sync_board(db,ident,transport=Fake((302,None,{'Location':'https://127.0.0.1'})),force=True)

def test_long_retry_not_ignored(db):
    ident=permit(db)
    with pytest.raises(ValueError,match='较长等待'):
        sync_board(db,ident,transport=Fake((429,None,{'Retry-After':'300'})),sleeper=lambda _:pytest.fail('must not retry'))

def lever(ident):
    return {'id':str(ident),'text':'PM','hostedUrl':f'https://jobs.lever.co/fixture/{ident}',
            'applyUrl':f'https://jobs.lever.co/fixture/{ident}/apply','categories':{'location':'Tokyo'},
            'descriptionPlain':'Python','lists':[{'text':'Required','content':'<li>SQL</li>'}], 'additionalPlain':'English'}

def test_lever_pagination(db):
    ident=permit(db,'lever'); fake=Fake((200,[lever(i) for i in range(100)],{}),(200,[lever(100)],{}))
    assert sync_board(db,ident,transport=fake,sleeper=lambda _:None)['count']==101
    assert 'skip=100' in fake.urls[1]
    assert 'SQL' in preferred(db,1)['content']

def test_partial_page_failure_no_expiry(db):
    ident=permit(db,'lever'); fake=Fake((200,[lever(i) for i in range(100)],{}),(403,None,{}))
    with pytest.raises(ValueError): sync_board(db,ident,transport=fake,sleeper=lambda _:None)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0

def test_ashby_unlisted_and_missing_fields():
    assert normalize('ashby',{'jobs':[{'isListed':False}]})==[]
    assert normalize('ashby',{'jobs':[{'title':'PM','jobUrl':'https://jobs.ashbyhq.com/x/1','descriptionPlain':'SQL'}]})[0]['location']=='未注明'
    with pytest.raises(ValueError): normalize('greenhouse',{'wrong':[]})

def test_eu_and_html():
    assert endpoint('lever_eu','x').startswith('https://api.eu.lever.co/')
    assert 'evil' not in plain('<script>evil</script><p>SQL</p>')

def test_dns_block_before_connection():
    with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
        with pytest.raises(PermissionError): ReadOnlyTransport().get(endpoint('ashby','x'))

@pytest.mark.parametrize('url',['https://linkedin.com/jobs','https://127.0.0.1/x','http://api.lever.co/v0/postings/x'])
def test_transport_host_guard(url):
    with pytest.raises(PermissionError): ReadOnlyTransport().get(url)

def test_snapshot_reversion(db):
    params=dict(company='X',title='PM',location='Tokyo',source='official',url='https://example.com/jobs/1')
    ident=import_job(db,**params,jd='SQL')
    import_job(db,**params,jd='Python')
    import_job(db,**params,jd='SQL')
    assert preferred(db,ident)['content']=='SQL'

def test_upgrade_preserves_foreign_keys(tmp_path):
    import sqlite3
    path=tmp_path/'old.sqlite'; c=sqlite3.connect(path)
    c.executescript((Path(__file__).resolve().parents[1]/'migrations/001_initial.sql').read_text())
    c.execute("INSERT INTO jobs VALUES(1,'X','PM','Tokyo',NULL,'imported','old')")
    c.execute("INSERT INTO job_source_links VALUES(1,1,'official','https://example.com',NULL,'old','manual')")
    c.execute("INSERT INTO jd_snapshots VALUES(1,1,'SQL','hash','old')"); c.commit(); c.close()
    c=connect(path)
    assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert preferred(c,1)['content']=='SQL'
    c.close()

def test_batch_matching(db):
    params=dict(company='X',title='PM',location='Tokyo',source='official')
    a=import_job(db,**params,url='https://example.com/1',jd='SQL')
    b=import_job(db,**params,url='https://example.com/2',jd='Python')
    add_fact(db,'skill','','SQL','fixture confirmed',True)
    result=batch_match(db)
    assert [r['job_id'] for r in result]==[a,b]
    assert result[0]['coverage']==100 and result[1]['coverage']==0


def test_matching_platform_priority(db):
    from job_agent.core import matching_source, match_job
    params=dict(company='X',title='PM',location='Tokyo',requisition_id='R1')
    ident=import_job(db,**params,source='official',url='https://example.com/1',jd='Python')
    import_job(db,**params,source='linkedin',url='https://www.linkedin.com/jobs/view/123',jd='SQL')
    assert preferred(db,ident)['source']=='official'
    assert matching_source(db,ident)['source']=='linkedin'
    report=match_job(db,ident)
    assert report['source']=='linkedin' and report['requirements'][0]['term']=='sql'
    import_job(db,**params,source='openwork',url='https://www.openwork.jp/jobs/123',jd='English')
    assert matching_source(db,ident)['source']=='openwork'
    import_job(db,**params,source='official',url='https://example.com/1',jd='SQL Python')
    assert matching_source(db,ident)['source']=='openwork'

def test_fallback_official(db):
    from job_agent.core import match_job
    ident=import_job(db,company='X',title='PM',location='Tokyo',source='official',url='https://example.com/1',jd='SQL')
    assert match_job(db,ident)['source']=='official'

def test_incomplete_board_rejected():
    with pytest.raises(ValueError,match='不完整'):
        normalize('greenhouse',{'jobs':[gh()],'meta':{'total':100}})

@pytest.mark.parametrize('url',[
    'https://api.lever.co/apply',
    'https://api.lever.co/v0/postings/x?mode=json&skip=0&limit=100&evil=1',
    'https://boards-api.greenhouse.io/v1/boards/x/jobs/1?content=true',
    'https://api.ashbyhq.com/posting-api/job-board/x?x=1'])
def test_transport_path_guard(url):
    with pytest.raises(PermissionError): ReadOnlyTransport().get(url)

def test_transport_pinned_get_without_real_network():
    class Response:
        status=200
        def getheader(self,name,default=''): return 'application/json'
        def getheaders(self): return [('Content-Type','application/json')]
        def read(self,limit): return b'{"jobs":[]}'
    class Connection:
        def __init__(self,host,ip): assert host=='api.ashbyhq.com' and ip=='8.8.8.8'
        def request(self,method,path,headers): assert method=='GET' and path=='/posting-api/job-board/x'
        def getresponse(self): return Response()
        def close(self): pass
    with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]), patch('job_agent.sources.PinnedHTTPS',Connection):
        status,payload,_=ReadOnlyTransport().get(endpoint('ashby','x'))
        assert status==200 and payload=={'jobs':[]}

def test_pipeline_no_sources_no_network(db):
    from job_agent.pipeline import collect_and_match
    fake=Fake(); result=collect_and_match(db,transport=fake)
    assert result['sources']==[] and result['matches']==[] and fake.urls==[]

def test_pipeline_isolates_failures(db):
    from job_agent.pipeline import collect_and_match
    a=permit(db); b=permit(db,'ashby')
    fake=Fake((403,None,{}),(200,{'jobs':[{'title':'PM','jobUrl':'https://jobs.ashbyhq.com/x/1','descriptionPlain':'SQL'}]},{}))
    result=collect_and_match(db,transport=fake)
    assert [r['status'] for r in result['sources']]==['failed','success']
    assert len(result['matches'])==1

def test_pipeline_skips_revoked_and_expired(db):
    from job_agent.pipeline import collect_and_match
    a=permit(db); revoke_permission(db,a); b=permit(db,'ashby')
    with db: db.execute('UPDATE source_permissions SET expires_at=? WHERE id=?',(stamp(now()-timedelta(days=1)),b))
    fake=Fake(); result=collect_and_match(db,transport=fake)
    assert result['sources'][0]['status']=='skipped' and fake.urls==[]


def test_read_page_cannot_cross_permission_scope(db):
    ident=permit(db); permission=require_permission(db,ident)
    with pytest.raises(PermissionError):
        read_page(db,permission,endpoint('greenhouse','other'),Fake())
    with pytest.raises(PermissionError):
        read_page(db,permission,endpoint('ashby','fixture'),Fake())

def test_latest_platform_observation_even_when_content_reverts(db):
    from job_agent.core import matching_source
    params=dict(company='X',title='PM',location='Tokyo',requisition_id='R1')
    ident=import_job(db,**params,source='linkedin',url='https://www.linkedin.com/jobs/view/1',jd='SQL')
    import_job(db,**params,source='openwork',url='https://www.openwork.jp/jobs/1',jd='English')
    import_job(db,**params,source='linkedin',url='https://www.linkedin.com/jobs/view/1',jd='SQL')
    assert matching_source(db,ident)['source']=='linkedin'
