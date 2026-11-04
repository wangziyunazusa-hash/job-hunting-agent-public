import csv
import io
import json
from pathlib import Path
import pytest
from job_agent.core import connect, import_job, add_fact
from job_agent.bulk_import import *

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'bulk.sqlite'); yield c; c.close()

def row(n=1,**extra):
    data={'company':'Fixture','title':'Analyst','location':'Tokyo','source':'official','url':f'https://example.com/jobs/{n}','jd':'SQL required.'}
    data.update(extra); return data

def prepare(db, rows): return prepare_batch(db,json.dumps(rows,ensure_ascii=False),'json')

def commit(db, ident): return commit_batch(db,ident,expected_hash=get_batch(db,ident)['preview_hash'],confirmed=True)

def test_preview_never_writes_jobs_or_facts(db):
    before=fingerprint(db)
    ident=prepare(db,[row(),row(2)])
    assert fingerprint(db)==before
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM facts').fetchone()[0]==0
    assert get_batch(db,ident)['preview']['new_jobs']==2
    report=commit(db,ident)
    assert report['new_jobs']==2
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==2
    assert get_batch(db,ident)['state']=='committed'

def test_confirmation_and_hash_required(db):
    ident=prepare(db,[row()])
    with pytest.raises(ValueError): commit_batch(db,ident,expected_hash=get_batch(db,ident)['preview_hash'])
    with pytest.raises(ValueError): commit_batch(db,ident,expected_hash='wrong',confirmed=True)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0

def test_committed_batch_cannot_replay(db):
    ident=prepare(db,[row()]); commit(db,ident)
    with pytest.raises(ValueError): commit(db,ident)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1

def test_same_batch_exact_duplicates_skipped(db):
    duplicate=row(url='https://example.com/jobs/1?utm_source=test#fragment')
    ident=prepare(db,[row(),duplicate])
    preview=get_batch(db,ident)['preview']
    assert preview['new_jobs']==1 and preview['skipped_duplicates']==1
    commit(db,ident)
    assert db.execute('SELECT count(*) FROM job_source_links').fetchone()[0]==1

def test_conflicting_same_url_refused(db):
    with pytest.raises(ValueError): prepare(db,[row(),row(jd='Conflicting JD')])
    assert db.execute('SELECT count(*) FROM batch_imports').fetchone()[0]==0

def test_cross_source_strong_merge_preview(db):
    official=row(application_url='https://example.com/apply/1',requisition_id='R1')
    platform=row(source='linkedin',url='https://www.linkedin.com/jobs/view/1',application_url='https://example.com/apply/1',jd='English preferred.')
    ident=prepare(db,[official,platform]); preview=get_batch(db,ident)['preview']
    assert preview['new_jobs']==1 and preview['attached_sources']==1
    assert preview['rows'][1]['warnings']
    commit(db,ident)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM job_source_links').fetchone()[0]==2

def test_weak_same_title_not_merged(db):
    ident=prepare(db,[row(),row(2)])
    assert get_batch(db,ident)['preview']['new_jobs']==2

def test_existing_snapshot_and_refresh_previews(db):
    import_job(db,**row())
    a=prepare(db,[row(jd='Changed SQL')])
    assert get_batch(db,a)['preview']['new_jd_snapshots']==1
    commit(db,a)
    b=prepare(db,[row(jd='Changed SQL')])
    assert get_batch(db,b)['preview']['refreshed_existing']==1
    commit(db,b)
    assert db.execute('SELECT count(*) FROM jd_snapshots').fetchone()[0]==2

def test_company_conflict_refused_globally(db):
    import_job(db,**row())
    with pytest.raises(ValueError): prepare(db,[row(company='Other')])
    with pytest.raises(ValueError): import_job(db,**row(company='Other'))
    assert db.execute('SELECT count(*) FROM jd_snapshots').fetchone()[0]==1

def test_metadata_difference_warns_and_retains_original(db):
    import_job(db,**row())
    ident=prepare(db,[row(title='New title',location='Osaka')])
    assert get_batch(db,ident)['preview']['rows'][0]['warnings']
    commit(db,ident)
    assert db.execute('SELECT title FROM jobs').fetchone()[0]=='Analyst'

def test_csv_multiline_japanese_bom(db):
    output=io.StringIO(newline=''); writer=csv.DictWriter(output,fieldnames=['company','title','location','source','url','jd'])
    writer.writeheader(); writer.writerow(row(company='テスト株式会社',jd='SQL required.\n日本語、英語'))
    ident=prepare_batch(db,'\ufeff'+output.getvalue(),'csv')
    commit(db,ident)
    assert db.execute('SELECT content FROM jd_snapshots').fetchone()[0]=='SQL required.\n日本語、英語'

@pytest.mark.parametrize('raw,format',[
    ('[]','json'),('{}','json'),('```json\n[]\n```','json'),
    ('[{"company":"A","company":"B"}]','json'),
    ('company,company\nA,B','csv'),('company,title\nA,B','csv'),
    ('company,title,location,source,url,jd\nA,B,C,official,https://example.com/1,D,extra','csv'),
    ('anything','xml')])
def test_bad_format_rejected(db,raw,format):
    with pytest.raises(ValueError): prepare_batch(db,raw,format)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0

@pytest.mark.parametrize('changes',[
    {'company':''},{'jd':None},{'company':True},{'source':'linkedin'},
    {'source':'unrecognized'},{'url':'http://example.com/1'},
    {'url':'https://127.0.0.1/1'},{'application_url':'javascript:alert(1)'},
    {'method':'api'},{'title':'x'*501},{'jd':'x'*200001}])
def test_bad_rows_refused(db,changes):
    with pytest.raises(ValueError): prepare(db,[row(**changes)])
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0

def test_stale_preview_refused_on_job_change(db):
    ident=prepare(db,[row()]); import_job(db,**row(2))
    assert get_batch(db,ident)['state']=='obsolete'
    with pytest.raises(ValueError): commit(db,ident)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1

def test_non_job_change_does_not_invalidate_preview(db):
    ident=prepare(db,[row()]); add_fact(db,'skill','','SQL','Fixture',False)
    assert not get_batch(db,ident)['stale']
    commit(db,ident)

def test_atomic_rollback_after_partial_work(db,monkeypatch):
    import job_agent.bulk_import as module
    ident=prepare(db,[row(),row(2)])
    original=module.import_job; calls=[]
    def fail_second(*a,**kw):
        calls.append(1)
        if len(calls)==2: raise ValueError('Fixture failure')
        return original(*a,**kw)
    monkeypatch.setattr(module,'import_job',fail_second)
    with pytest.raises(ValueError): commit(db,ident)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM jd_snapshots').fetchone()[0]==0
    assert db.execute("SELECT count(*) FROM audit_events WHERE action='manual_import'").fetchone()[0]==0
    assert get_batch(db,ident)['state']=='preview'

def test_tampered_preview_refused(db):
    ident=prepare(db,[row()])
    with db: db.execute("UPDATE batch_imports SET preview_hash='corrupt' WHERE id=?",(ident,))
    assert get_batch(db,ident)['state']=='obsolete'
    with pytest.raises(ValueError): commit(db,ident)

def test_size_and_row_limits(db):
    with pytest.raises(ValueError): prepare(db,[row(i) for i in range(51)])
    with pytest.raises(ValueError): prepare_batch(db,'x'*(1024*1024+1),'json')

def test_blank_template_has_no_fake_jobs():
    reader=csv.DictReader(io.StringIO(csv_template().removeprefix('\ufeff')))
    assert set(reader.fieldnames)==REQUIRED|OPTIONAL
    assert list(reader)==[]

def test_unknown_batch(db):
    with pytest.raises(ValueError): get_batch(db,999)

def test_ui_batch_preview_and_confirm(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(w for w in app.text_area if w.label=='粘贴手动整理的岗位数据').set_value(json.dumps([row(),row(2)]))
    next(w for w in app.button if w.label=='校验并预览整批导入').click().run()
    assert not app.exception and not app.error
    c=original(path); assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==0; c.close()
    next(w for w in app.checkbox if w.label=='我已逐条核对 JD、来源、归并结果和全部警告').check()
    next(w for w in app.button if w.label=='确认并整批保存岗位').click().run()
    assert not app.exception and not app.error
    c=original(path); assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==2; assert get_batch(c,1)['state']=='committed'; c.close()


def test_existing_old_jd_reuse_is_explained(db):
    import_job(db,**row(jd='A SQL'))
    import_job(db,**row(jd='B Python'))
    ident=prepare(db,[row(jd='A SQL')])
    preview=get_batch(db,ident)['preview']
    assert preview['reused_jd_snapshots']==1
    commit(db,ident)
    from job_agent.core import matching_source
    assert matching_source(db,1)['content']=='A SQL'

def test_application_link_update_and_target_metadata_visible(db):
    import_job(db,**row(application_url='https://example.com/apply/old'))
    ident=prepare(db,[row(title='Changed title',application_url='https://example.com/apply/new')])
    detail=get_batch(db,ident)['preview']['rows'][0]
    assert detail['target_job']['title']=='Analyst'
    assert any('申请链接' in warning for warning in detail['warnings'])
    commit(db,ident)
    assert db.execute('SELECT application_url FROM job_source_links').fetchone()[0]=='https://example.com/apply/new'

def test_weak_duplicate_warning_is_explicit(db):
    import_job(db,**row())
    ident=prepare(db,[row(2)])
    detail=get_batch(db,ident)['preview']['rows'][0]
    assert detail['possible_duplicate_ids']==[1]
    assert any('未自动合并' in warning for warning in detail['warnings'])


def test_large_csv_field_reports_specific_limit(db):
    import csv
    field='X'*(csv.field_size_limit()+1)
    if len(field)<200000:
        output=io.StringIO(newline=''); writer=csv.DictWriter(output,fieldnames=['company','title','location','source','url','jd'])
        writer.writeheader(); writer.writerow(row(jd=field))
        with pytest.raises(ValueError,match='CSV 单字段'): prepare_batch(db,output.getvalue(),'csv')
        assert prepare(db,[row(jd=field)])


def test_inconsistent_plan_rolls_back_all_changes(db,monkeypatch):
    import job_agent.bulk_import as module
    ident=prepare(db,[row()]); original=module.simulate
    def changed_report(*args):
        report=original(*args); report['new_jobs']=999; return report
    monkeypatch.setattr(module,'simulate',changed_report)
    with pytest.raises(ValueError,match='预览不一致'): commit(db,ident)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert get_batch(db,ident)['state']=='preview'


def test_explicit_merged_company_alias_can_refresh_original_source(db):
    from job_agent.reviews import merge_jobs
    source=import_job(db,**row(company='Original alias'))
    target=import_job(db,**row(2,company='Official employer'))
    merge_jobs(db,source,target,'User verified employer alias',confirmed=True)
    ident=prepare(db,[row(company='Original alias',jd='Updated SQL')])
    detail=get_batch(db,ident)['preview']['rows'][0]
    assert detail['job_id']==target
    assert any('人工确认' in warning for warning in detail['warnings'])
    commit(db,ident)
    assert db.execute('SELECT company FROM jobs WHERE id=?',(target,)).fetchone()[0]=='Official employer'
    with pytest.raises(ValueError): prepare(db,[row(2,company='Original alias')])


def test_utf8_upload_preserves_japanese_and_bom():
    from job_agent.bulk_import import decode_upload
    raw=json.dumps([row(company='テスト株式会社')],ensure_ascii=False)
    assert parse_rows(decode_upload(b'\xef\xbb\xbf'+raw.encode('utf-8')),'json')[0]['company']=='テスト株式会社'


def test_upload_rejects_invalid_encoding_or_size():
    from job_agent.bulk_import import decode_upload
    with pytest.raises(ValueError,match='UTF-8'): decode_upload(b'\xff\xfeinvalid')
    with pytest.raises(ValueError): decode_upload(b'x'*(1024*1024+1))
    with pytest.raises(ValueError): decode_upload('not bytes')


def test_duplicate_review_change_expires_preview(db):
    from job_agent.reviews import mark_distinct
    a=import_job(db,**row()); b=import_job(db,**row(2))
    ident=prepare(db,[row(3)])
    mark_distinct(db,a,b,'Verified separate teams',confirmed=True)
    assert get_batch(db,ident)['state']=='obsolete'
    with pytest.raises(ValueError): commit(db,ident)


def test_ui_uploaded_file_preview(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    import job_agent.core as core
    original=core.connect; path=tmp_path/'uploaded.sqlite'
    monkeypatch.setattr(core,'connect',lambda:original(path))
    class Uploaded:
        name='manual_jobs.json'
        def getvalue(self): return json.dumps([row(company='テスト社')],ensure_ascii=False).encode('utf-8')
    # Exercise uploaded-byte decoding and integration without browser/file/network I/O.
    monkeypatch.setattr(st,'file_uploader',lambda *args,**kwargs:Uploaded())
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(w for w in app.radio if w.label=='批量导入方式').set_value('上传文件').run()
    next(w for w in app.button if w.label=='校验并预览整批导入').click().run()
    assert not app.exception and not app.error
    c=original(path)
    assert get_batch(c,1)['rows'][0]['company']=='テスト社'
    assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    c.close()


@pytest.mark.parametrize('confirmed',[False,None,1,'true'])
def test_confirmation_requires_actual_boolean_true(db,confirmed):
    ident=prepare(db,[row()])
    with pytest.raises(ValueError): commit_batch(db,ident,expected_hash=get_batch(db,ident)['preview_hash'],confirmed=confirmed)
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
