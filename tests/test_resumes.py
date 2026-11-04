import json
from pathlib import Path
import pytest
from job_agent.core import connect, import_job, add_fact
from job_agent.resumes import *
from job_agent.reviews import merge_jobs, undo_merge

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'resumes.sqlite'); yield c; c.close()

def seed_fixture(db):
    job=import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1',jd='SQL')
    fact=add_fact(db,'skill','SQL を使用しました。','Used SQL.','Fixture evidence only',True)
    return job,fact

def draft(db,lang='en'):
    job,fact=seed_fixture(db)
    return create_draft(db,job,lang,[fact])

def approve(db,ident):
    return approve_version(db,ident,expected_hash=get_version(db,ident)['content_hash'],confirmed=True)

@pytest.mark.parametrize('lang,text',[('en','Used SQL.'),('ja','SQL を使用しました。')])
def test_draft_approval_export(db,lang,text):
    ident=draft(db,lang); row=validate_version(db,ident)
    assert row['status']=='review_required' and text in row['content']
    assert row['claims'][0]['original_text']==text
    with pytest.raises(ValueError): export_version(db,ident)
    binding=approve(db,ident)
    body,trace=export_version(db,ident)
    trace=json.loads(trace)
    assert text in body and binding==trace['approval_hash']
    assert trace['claims'][0]['fact_id']==row['claims'][0]['fact_id']

def test_empty_or_pending_facts_blocked(db):
    job,fact=seed_fixture(db)
    with pytest.raises(ValueError): create_draft(db,job,'en',[])
    with pytest.raises(ValueError): create_draft(db,job,'en',[fact,fact])
    pending=add_fact(db,'skill','','Python','fixture',False)
    with pytest.raises(ValueError): create_draft(db,job,'en',[pending])
    with pytest.raises(ValueError): create_draft(db,job,'en',[999])
    assert db.execute('SELECT count(*) FROM resume_versions').fetchone()[0]==0

def test_missing_language_not_translated(db):
    job,fact=seed_fixture(db); ja=add_fact(db,'skill','日本語のみ','','fixture',True)
    with pytest.raises(ValueError,match='不会自动翻译'): create_draft(db,job,'en',[ja])
    with pytest.raises(ValueError): create_draft(db,job,'zh',[fact])

def test_order_preserved(db):
    job,a=seed_fixture(db); b=add_fact(db,'skill','','Used Python.','fixture',True)
    ident=create_draft(db,job,'en',[b,a])
    row=validate_version(db,ident)
    assert [c['fact_id'] for c in row['claims']]==[b,a]
    assert row['content'].index('Used Python.')<row['content'].index('Used SQL.')

def test_review_and_preview_required(db):
    ident=draft(db)
    with pytest.raises(ValueError): approve_version(db,ident,expected_hash=get_version(db,ident)['content_hash'])
    with pytest.raises(ValueError): approve_version(db,ident,expected_hash='wrong',confirmed=True)
    assert get_version(db,ident)['status']=='review_required'

def test_jd_change_invalidates_and_does_not_revive(db):
    ident=draft(db); approve(db,ident)
    args=dict(company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1')
    import_job(db,**args,jd='Python')
    with pytest.raises(ValueError): export_version(db,ident)
    assert get_version(db,ident)['status']=='obsolete'
    import_job(db,**args,jd='SQL')
    with pytest.raises(ValueError): approve(db,ident)

def test_same_jd_observation_requires_fresh_review(db):
    ident=draft(db); approve(db,ident)
    import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1',jd='SQL')
    assert validate_version(db,ident)['status']=='obsolete'

def test_fact_bank_change_invalidates(db):
    ident=draft(db); approve(db,ident)
    add_fact(db,'skill','','New pending fact','fixture',False)
    assert validate_version(db,ident)['status']=='obsolete'

@pytest.mark.parametrize('statement',[
    "UPDATE facts SET confirmed=0", "UPDATE facts SET text_en='Altered'", "UPDATE facts SET evidence='changed'",
    "UPDATE resume_versions SET content='Invented claim'", "UPDATE resume_claims SET original_text='Invented claim'",
    "UPDATE resume_versions SET approval_hash='forged'", "UPDATE resume_claims SET evidence='forged'",
    "UPDATE jobs SET status='expired'", "UPDATE jobs SET company='changed'"])
def test_invalidated_on_material_change(db,statement):
    ident=draft(db); approve(db,ident)
    with db: db.execute(statement)
    with pytest.raises(ValueError): export_version(db,ident)
    assert get_version(db,ident)['status']=='obsolete'

def test_merge_invalidates_versions_and_preserves_history(db):
    job,fact=seed_fixture(db); ident=create_draft(db,job,'en',[fact]); approve(db,ident)
    other=import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='linkedin',url='https://www.linkedin.com/jobs/view/1',jd='SQL English')
    merge=merge_jobs(db,other,job,'same job verified',confirmed=True)
    assert validate_version(db,ident)['status']=='obsolete'
    undo_merge(db,merge)
    assert validate_version(db,ident)['status']=='obsolete'
    assert db.execute('SELECT count(*) FROM resume_versions').fetchone()[0]==1

def test_merged_source_draft_invalidated(db):
    job,fact=seed_fixture(db); ident=create_draft(db,job,'en',[fact]); approve(db,ident)
    other=import_job(db,company='Other',title='Analyst',location='Tokyo',source='official',url='https://example.com/2',jd='SQL')
    merge_jobs(db,job,other,'same job verified',confirmed=True)
    assert validate_version(db,ident)['status']=='obsolete'

def test_source_priority_in_context(db):
    job,fact=seed_fixture(db)
    import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='linkedin',url='https://www.linkedin.com/jobs/view/1',application_url='https://example.com/apply',jd='SQL')
    # Separate records until explicit grouping, then matching context chooses platform.
    other=db.execute('SELECT max(id) FROM jobs').fetchone()[0]
    merge_jobs(db,other,job,'verified',confirmed=True)
    ident=create_draft(db,job,'en',[fact])
    assert 'linkedin.com' in json.loads(get_version(db,ident)['context_json'])['source_url']

def test_new_draft_is_immutable_new_version(db):
    job,fact=seed_fixture(db)
    a=create_draft(db,job,'en',[fact]); b=create_draft(db,job,'en',[fact])
    assert a!=b and get_version(db,a)['status']=='review_required'

def test_no_unknown_version(db):
    with pytest.raises(ValueError): validate_version(db,999)

def test_ui_draft_review_export(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    c=original(path); seed_fixture(c); c.close()
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    assert not app.exception
    selector=next(w for w in app.selectbox if w.label=='简历语言')
    selector.select('en').run()
    picker=next(w for w in app.multiselect if w.label=='按希望呈现的顺序选择事实')
    picker.select(picker.options[0])
    next(w for w in app.button if w.label=='生成待审阅草稿').click().run()
    assert not app.exception
    next(w for w in app.checkbox if w.label=='我已逐条核实事实、语言和证据，并审阅此版本全文').check()
    next(w for w in app.button if w.label=='批准此版本').click().run()
    assert not app.exception
    c=original(path)
    assert c.execute('SELECT status FROM resume_versions').fetchone()[0]=='approved'
    assert export_version(c,1)[0].find('Used SQL.')>=0
    c.close()


def test_fact_revocation_and_reconfirmation_does_not_revive(db):
    from job_agent.core import review_fact
    job,fact=seed_fixture(db); ident=create_draft(db,job,'en',[fact]); approve(db,ident)
    review_fact(db,fact,confirmed=False,reviewed=True)
    review_fact(db,fact,confirmed=True,reviewed=True)
    assert validate_version(db,ident)['status']=='obsolete'
    assert db.execute('SELECT revision FROM facts WHERE id=?',(fact,)).fetchone()[0]==3

def test_fact_review_requires_confirmation(db):
    from job_agent.core import review_fact
    job,fact=seed_fixture(db)
    with pytest.raises(ValueError): review_fact(db,fact,confirmed=False)
    with pytest.raises(ValueError): review_fact(db,999,confirmed=True,reviewed=True)
    assert db.execute('SELECT confirmed FROM facts WHERE id=?',(fact,)).fetchone()[0]==1

def test_recommendations_are_extractive(db):
    job,a=seed_fixture(db)
    b=add_fact(db,'skill','','Unrelated experience.','fixture',True)
    pending=add_fact(db,'skill','','SQL Python','fixture',False)
    ja=add_fact(db,'skill','SQL のみ','','fixture',True)
    assert [r['fact_id'] for r in recommend_facts(db,job,'en')]==[a]
    ident=create_matched_draft(db,job,'en')
    assert validate_version(db,ident)['claims'][0]['original_text']=='Used SQL.'
    assert validate_version(db,ident)['status']=='review_required'

def test_no_automatic_content_when_no_overlap(db):
    job=import_job(db,company='Fixture',title='X',location='Tokyo',source='official',url='https://example.com/1',jd='Sales')
    add_fact(db,'skill','','SQL','fixture',True)
    with pytest.raises(ValueError,match='不会生成经历'): create_matched_draft(db,job,'en')

def test_fact_ranking_keeps_reference_order(db):
    job,a=seed_fixture(db)
    import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1',jd='SQL Python')
    b=add_fact(db,'skill','','Used SQL and Python.','fixture',True)
    assert [r['fact_id'] for r in recommend_facts(db,job,'en')]==[b,a]
    ident=create_matched_draft(db,job,'en')
    assert [r['fact_id'] for r in validate_version(db,ident)['claims']]==[b,a]


def test_ui_fact_revocation_invalidates_approved_draft(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    c=original(path); job,fact=seed_fixture(c); ident=create_draft(c,job,'en',[fact]); approve(c,ident); c.close()
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(w for w in app.radio if w.label=='事实状态操作').set_value('撤销确认').run()
    next(w for w in app.checkbox if w.label=='我已核对此条事实和证据').check()
    next(w for w in app.button if w.label=='保存事实状态').click().run()
    assert not app.exception
    assert not app.error
    c=original(path)
    assert c.execute('SELECT confirmed FROM facts WHERE id=?',(fact,)).fetchone()[0]==0
    assert validate_version(c,ident)['status']=='obsolete'
    c.close()
