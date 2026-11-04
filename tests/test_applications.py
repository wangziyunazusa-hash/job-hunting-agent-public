import json
from pathlib import Path
import pytest
from job_agent.core import connect, import_job, add_fact, review_fact
from job_agent.resumes import create_draft, approve_version, get_version
from job_agent.applications import *

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'app.sqlite'); yield c; c.close()

def seed(db,app_url='https://careers.example.com/apply/1',approve=True):
    job=import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://careers.example.com/1',application_url=app_url,jd='SQL')
    fact=add_fact(db,'skill','SQL を使った。','Used SQL.','Fixture evidence only',True)
    version=create_draft(db,job,'en',[fact])
    if approve: approve_version(db,version,expected_hash=get_version(db,version)['content_hash'],confirmed=True)
    return job,fact,version

def packet(db,**kwargs):
    job,fact,version=seed(db,**kwargs)
    return create_application(db,version,[{'field':'experience','fact_ids':[fact]}])

def ready(db,ident):
    return mark_ready(db,ident,expected_hash=get_application(db,ident)['payload_hash'],checks={k:True for k in CHECKS})

def test_packet_ready_and_user_report(db):
    ident=packet(db)
    row=validate_application(db,ident)
    assert row['state']=='prepared' and row['payload']['fields'][0]['value']=='Used SQL.'
    assert row['payload']['entry']['source']=='official'
    with pytest.raises(ValueError): export_application(db,ident)
    ready(db,ident)
    export=json.loads(export_application(db,ident))
    assert export['submitted_by_system'] is False
    assert 'Used SQL.' in export['resume_text']
    assert export['resume_evidence']['claims'][0]['original_text']=='Used SQL.'
    with pytest.raises(ValueError): report_submitted(db,ident)
    report_submitted(db,ident,confirmed=True,note='User fixture report')
    assert validate_application(db,ident)['state']=='user_reported_submitted'
    events=db.execute('SELECT * FROM application_events WHERE application_id=?',(ident,)).fetchall()
    assert all(e['actor']=='user' for e in events)
    assert events[-1]['event']=='application_user_reported_submitted'

def test_draft_cannot_prepare(db):
    job,fact,version=seed(db,approve=False)
    with pytest.raises(ValueError): create_application(db,version,[])

@pytest.mark.parametrize('checks',[{}, {k:False for k in CHECKS}, {k:'true' for k in CHECKS}])
def test_all_checks_must_be_explicit_boolean(db,checks):
    ident=packet(db)
    with pytest.raises(ValueError): mark_ready(db,ident,expected_hash=get_application(db,ident)['payload_hash'],checks=checks)
    assert get_application(db,ident)['state']=='prepared'

def test_preview_hash_guard(db):
    ident=packet(db)
    with pytest.raises(ValueError): mark_ready(db,ident,expected_hash='wrong',checks={k:True for k in CHECKS})

def test_missing_entry_stops_readiness(db):
    ident=packet(db,app_url='')
    with pytest.raises(ValueError,match='没有已登记申请链接'): ready(db,ident)

def test_unresolved_questions(db):
    job,fact,version=seed(db)
    ident=create_application(db,version,[],questions=['User must verify eligibility'])
    with pytest.raises(ValueError,match='待回答问题'): ready(db,ident)
    with pytest.raises(ValueError): create_application(db,version,[],questions=[''])

@pytest.mark.parametrize('specs',[
    [{'field':'legal_consent','fact_ids':[1]}],
    [{'field':'experience','fact_ids':[]}],
    [{'field':'experience','fact_ids':[1,1]}],
    [{'field':'experience','fact_ids':[1],'value':'Invented leadership'}],
    [{'field':'experience','fact_ids':[999]}],
    [{'field':'experience','fact_ids':[1]},{'field':'experience','fact_ids':[1]}]])
def test_no_free_text_or_unsupported_fields(db,specs):
    job,fact,version=seed(db)
    with pytest.raises(ValueError): create_application(db,version,specs)
    assert db.execute('SELECT count(*) FROM applications').fetchone()[0]==0

def test_stale_packet_after_fact_change(db):
    ident=packet(db); ready(db,ident)
    review_fact(db,1,confirmed=False,reviewed=True)
    assert validate_application(db,ident)['state']=='needs_review'
    with pytest.raises(ValueError): export_application(db,ident)
    with pytest.raises(ValueError): report_submitted(db,ident,confirmed=True)
    assert db.execute("SELECT actor FROM application_events WHERE event='application_needs_review'").fetchone()[0]=='system'

def test_past_submission_is_history(db):
    ident=packet(db); ready(db,ident); report_submitted(db,ident,confirmed=True)
    review_fact(db,1,confirmed=False,reviewed=True)
    row=validate_application(db,ident)
    assert row['state']=='user_reported_submitted' and not row['currently_valid']
    assert row['user_reported_at']
    with pytest.raises(ValueError): export_application(db,ident)
    with pytest.raises(ValueError): abandon_application(db,ident,confirmed=True)

def test_entry_change_invalidates(db):
    ident=packet(db); ready(db,ident)
    with db: db.execute("UPDATE job_source_links SET application_url='https://careers.example.com/other'")
    assert validate_application(db,ident)['state']=='needs_review'

def test_application_link_official_first_independent_of_matching(db):
    job,fact,version=seed(db)
    import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='linkedin',url='https://www.linkedin.com/jobs/view/1',jd='SQL',application_url='https://careers.example.com/apply/1')
    entry=application_entry(db,job)
    assert entry['source']=='official'

def test_abandon_and_terminal_guards(db):
    ident=packet(db)
    with pytest.raises(ValueError): abandon_application(db,ident)
    abandon_application(db,ident,confirmed=True)
    assert validate_application(db,ident)['state']=='abandoned'
    with pytest.raises(ValueError): ready(db,ident)
    with pytest.raises(ValueError): report_submitted(db,ident,confirmed=True)
    with pytest.raises(ValueError): abandon_application(db,ident,confirmed=True)

def test_packet_modified_rejected(db):
    ident=packet(db); ready(db,ident)
    row=get_application(db,ident); payload=json.loads(row['payload_json'])
    payload['fields'][0]['value']='Invented skill'
    with db: db.execute('UPDATE applications SET payload_json=? WHERE id=?',(json.dumps(payload),ident))
    assert validate_application(db,ident)['state']=='needs_review'

def test_unknown_application(db):
    with pytest.raises(ValueError): validate_application(db,999)


def test_ui_prepare_ready_and_manual_report(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    c=original(path); seed(c); c.close()
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    picker=next(w for w in app.multiselect if w.label=='经历/自我介绍：引用哪些事实')
    picker.select(picker.options[0])
    next(w for w in app.button if w.label=='创建本地申请准备清单').click().run()
    assert not app.exception
    labels=['我已核实申请入口及岗位仍开放','我已核对该简历适用于本次申请','我已逐项核对字段原文，其他字段将自行填写','资格、声明、隐私条款及实际表单问题由我自行阅读和回答']
    for label in labels: next(w for w in app.checkbox if w.label==label).check()
    next(w for w in app.button if w.label=='完成本地核对，等待我自行提交').click().run()
    assert not app.exception
    assert not app.error, [e.value for e in app.error]
    state_db=original(path)
    assert get_application(state_db,1)['state']=='awaiting_user_submit', dict(get_application(state_db,1))
    state_db.close()
    next(w for w in app.checkbox if w.label=='我已自行在网站完成最终提交，仅在此记录结果').check()
    next(w for w in app.button if w.label=='记录我已自行提交').click().run()
    assert not app.exception
    c=original(path)
    assert get_application(c,1)['state']=='user_reported_submitted'
    c.close()


def test_pending_or_wrong_language_fields_refused(db):
    job,fact,version=seed(db)
    pending=add_fact(db,'contact','','fixture@example.com','fixture',False)
    # Changes to any facts require a fresh approved resume first.
    version=create_draft(db,job,'en',[fact])
    approve_version(db,version,expected_hash=get_version(db,version)['content_hash'],confirmed=True)
    with pytest.raises(ValueError): create_application(db,version,[{'field':'email','fact_ids':[pending]}])
    ja=add_fact(db,'contact','日本語のみ','','fixture',True)
    version=create_draft(db,job,'en',[fact])
    approve_version(db,version,expected_hash=get_version(db,version)['content_hash'],confirmed=True)
    with pytest.raises(ValueError): create_application(db,version,[{'field':'full_name','fact_ids':[ja]}])

def test_duplicate_user_submission_report_rejected(db):
    ident=packet(db); ready(db,ident); report_submitted(db,ident,confirmed=True)
    with pytest.raises(ValueError): report_submitted(db,ident,confirmed=True)
    assert db.execute("SELECT count(*) FROM application_events WHERE event='application_user_reported_submitted'").fetchone()[0]==1

@pytest.mark.parametrize('fact_ids',[[True],[-1],['1'],[[]]])
def test_invalid_fact_ids_refused(db,fact_ids):
    job,fact,version=seed(db)
    with pytest.raises(ValueError): create_application(db,version,[{'field':'email','fact_ids':fact_ids}])

def test_readiness_hash_corruption_refused(db):
    ident=packet(db); ready(db,ident)
    with db: db.execute("UPDATE applications SET readiness_hash='corrupt' WHERE id=?",(ident,))
    assert validate_application(db,ident)['state']=='needs_review'
    with pytest.raises(ValueError): report_submitted(db,ident,confirmed=True)

def test_api_unlisted_entry_not_selected(db):
    from job_agent.sources import register_permission, stamp, now
    from datetime import timedelta
    job,fact,version=seed(db)
    permission=register_permission(db,provider='greenhouse',board='fixture',company='Fixture',evidence='Fixture only',official_url='https://careers.example.com',expires_at=stamp(now()+timedelta(days=1)),confirmed=True)
    with db:
        run=db.execute("INSERT INTO sync_runs(permission_id,started_at,status) VALUES(?,?,'success')",(permission,stamp())).lastrowid
        link=db.execute('SELECT id FROM job_source_links WHERE job_id=?',(job,)).fetchone()[0]
        db.execute('INSERT INTO source_postings VALUES(?,?,?,0,?)',(permission,'fixture-post',link,run))
    assert application_entry(db,job) is None


def test_prepared_packet_cannot_skip_ready_state(db):
    ident=packet(db)
    with pytest.raises(ValueError): report_submitted(db,ident,confirmed=True)
    assert get_application(db,ident)['state']=='prepared'
