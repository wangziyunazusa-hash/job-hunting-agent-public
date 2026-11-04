import pytest
from job_agent.core import connect, import_job, preferred, matching_source, match_job, batch_match, possible_duplicates
from job_agent.reviews import merge_jobs, undo_merge, mark_distinct

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'review.sqlite'); yield c; c.close()

def pair(db):
    base=dict(company='Fixture',title='PM',location='Tokyo')
    a=import_job(db,**base,source='official',url='https://example.com/1',jd='Python')
    b=import_job(db,**base,source='linkedin',url='https://www.linkedin.com/jobs/view/1',jd='SQL')
    return a,b

def test_merge_preserves_history_and_priorities(db):
    official,platform=pair(db)
    report=match_job(db,platform)
    ident=merge_jobs(db,platform,official,'Verified same requisition',confirmed=True)
    assert preferred(db,official)['source']=='official'
    assert matching_source(db,official)['source']=='linkedin'
    assert len(batch_match(db))==1
    assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==2
    assert db.execute('SELECT count(*) FROM jd_snapshots').fetchone()[0]==2
    assert db.execute('SELECT job_id FROM match_reports WHERE job_id=?',(platform,)).fetchone()[0]==platform
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    undo_merge(db,ident)
    assert matching_source(db,official)['source']=='official'
    assert matching_source(db,platform)['source']=='linkedin'
    assert len(batch_match(db))==2

def test_distinct_persists_and_is_symmetric(db):
    a,b=pair(db)
    assert len(possible_duplicates(db,a))==1
    mark_distinct(db,b,a,'Different teams',confirmed=True)
    assert possible_duplicates(db,a)==[] and possible_duplicates(db,b)==[]
    mark_distinct(db,a,b,'Different requisitions',confirmed=True)
    assert db.execute('SELECT count(*) FROM duplicate_reviews').fetchone()[0]==1

@pytest.mark.parametrize('action',[merge_jobs,mark_distinct])
def test_review_requires_explicit_confirmation(db,action):
    a,b=pair(db)
    with pytest.raises(ValueError): action(db,a,b,'checked')
    with pytest.raises(ValueError): action(db,a,b,'',confirmed=True)
    with pytest.raises(ValueError): action(db,a,a,'checked',confirmed=True)
    with pytest.raises(ValueError): action(db,a,999,'checked',confirmed=True)
    assert len(batch_match(db))==2

def test_reimport_follows_merged_link_and_undo(db):
    a,b=pair(db); ident=merge_jobs(db,b,a,'Verified',confirmed=True)
    assert import_job(db,company='Fixture',title='PM',location='Tokyo',source='linkedin',url='https://www.linkedin.com/jobs/view/1',jd='Updated SQL')==a
    undo_merge(db,ident)
    assert matching_source(db,b)['content']=='Updated SQL'

def test_undo_does_not_move_new_links(db):
    a,b=pair(db)
    with db: db.execute('UPDATE job_source_links SET application_url=? WHERE job_id=?',('https://example.com/new',a))
    ident=merge_jobs(db,b,a,'Verified',confirmed=True)
    assert import_job(db,company='Fixture',title='PM',location='Tokyo',source='openwork',url='https://www.openwork.jp/jobs/1',application_url='https://example.com/new',jd='English')==a
    undo_merge(db,ident)
    assert db.execute('SELECT count(*) FROM job_source_links WHERE job_id=?',(b,)).fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM job_source_links WHERE job_id=?',(a,)).fetchone()[0]==2

def test_undo_refuses_changed_membership_atomically(db):
    a,b=pair(db); ident=merge_jobs(db,b,a,'Verified',confirmed=True)
    with db: db.execute('UPDATE job_source_links SET job_id=? WHERE source=?',(b,'linkedin'))
    with pytest.raises(ValueError): undo_merge(db,ident)
    assert db.execute('SELECT merged_into FROM jobs WHERE id=?',(b,)).fetchone()[0]==a
    assert db.execute('SELECT undone_at FROM job_merges').fetchone()[0] is None

def test_no_nested_or_double_undo(db):
    a,b=pair(db); ident=merge_jobs(db,b,a,'Verified',confirmed=True)
    c=import_job(db,company='Fixture',title='PM 2',location='Tokyo',source='official',url='https://example.com/2',jd='SQL')
    with pytest.raises(ValueError): merge_jobs(db,a,c,'Checked',confirmed=True)
    with pytest.raises(ValueError): merge_jobs(db,c,a,'Checked',confirmed=True)
    undo_merge(db,ident)
    with pytest.raises(ValueError): undo_merge(db,ident)

def test_invalid_candidate(db):
    assert possible_duplicates(db,999)==[]


def test_ui_merge_and_undo(tmp_path,monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    c=original(path); a,b=pair(c); c.close()
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    assert not app.exception
    next(w for w in app.text_area if w.label=='核对依据').set_value('Verified matching requisition')
    next(w for w in app.checkbox if w.label=='我已核实这两个岗位的关系').check()
    next(w for w in app.button if w.label=='保存核对结论').click().run()
    assert not app.exception
    c=original(path)
    assert c.execute('SELECT merged_into FROM jobs WHERE id=?',(a,)).fetchone()[0]==b
    c.close()
    next(w for w in app.button if w.label=='撤销这次合并').click().run()
    assert not app.exception
    c=original(path)
    assert c.execute('SELECT merged_into FROM jobs WHERE id=?',(a,)).fetchone()[0] is None
    c.close()
