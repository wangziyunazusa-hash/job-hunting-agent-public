import pytest
from pathlib import Path
from job_agent.core import connect, import_job, add_fact
from job_agent.preferences import *
from job_agent.resumes import create_draft, approve_version, get_version, validate_version

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'prefs.sqlite'); yield c; c.close()

def prefs(**extra):
    result={k:[] for k in LISTS}; result.update(extra); return result

def job(db,n,title='Analyst',location='Tokyo',jd='SQL',company='Fixture'):
    return import_job(db,company=company,title=title,location=location,source='official',url=f'https://example.com/jobs/{n}',jd=jd)

def test_empty_profile_allowed_and_versioned(db):
    a=save_profile(db,'General',prefs()); b=save_profile(db,'General',prefs(titles=['Analyst']))
    assert a!=b and get_profile(db,a)[1]['titles']==[]
    _,report=rank_jobs(db,a)
    assert report['jobs']==[]

def test_normalization_and_dedup(db):
    ident=save_profile(db,'Target',prefs(titles=[' ＰＭ ','pm','PM'],locations=['Tokyo','東京']))
    assert get_profile(db,ident)[1]['titles']==['ＰＭ']

@pytest.mark.parametrize('value',[{},[],{'titles':'X','locations':[],'keywords':[],'review_keywords':[]},prefs(titles=['']),prefs(keywords=['a'*101]),prefs(locations=['X']*51)])
def test_invalid_preference_schema(db,value):
    with pytest.raises(ValueError): save_profile(db,'Target',value)

@pytest.mark.parametrize('name',['',' '*3,'X'*101,None])
def test_invalid_names(db,name):
    with pytest.raises(ValueError): save_profile(db,name,prefs())

def test_ranking_and_explanation(db):
    add_fact(db,'skill','','SQL','Fixture',True)
    a=job(db,1,title='Product Manager',location='Tokyo',jd='SQL product management')
    b=job(db,2,title='Analyst',location='Osaka',jd='Python')
    profile=save_profile(db,'PM Tokyo',prefs(titles=['Product Manager'],locations=['Tokyo'],keywords=['SQL']))
    ident,report=rank_jobs(db,profile)
    assert [j['job_id'] for j in report['jobs']]==[a,b]
    assert report['jobs'][0]['preference_coverage']==100
    assert report['jobs'][1]['preference_coverage']==0
    assert report['jobs'][1]['dimensions'][0]['status']=='未命中关键词'
    assert not get_ranking(db,ident)['stale']

def test_review_keyword_flags_not_automatic_rejection(db):
    a=job(db,1,jd='SQL no overtime')
    b=job(db,2,jd='SQL')
    profile=save_profile(db,'Review',prefs(review_keywords=['overtime']))
    _,report=rank_jobs(db,profile)
    assert [j['job_id'] for j in report['jobs']]==[b,a]
    assert len(filter_ranked(report)['shown'])==2
    view=filter_ranked(report,hide_review_flags=True)
    assert view['hidden'][0]['job_id']==a
    assert '用户选择' in view['hidden'][0]['reasons'][0]

def test_unknown_coverage_retained_at_high_threshold(db):
    a=job(db,1,jd='Unrecognized role text',location='未注明')
    b=job(db,2,jd='SQL')
    profile=save_profile(db,'Target',prefs(locations=['Tokyo']))
    _,report=rank_jobs(db,profile)
    view=filter_ranked(report,min_coverage=100)
    assert [j['job_id'] for j in view['shown']]==[a]
    assert view['unknown_retained']==1
    assert view['shown'][0]['dimensions'][0]['status']=='未知'
    assert view['shown'][0]['preference_coverage'] is None
    assert view['shown'][0]['preference_known_dimensions']==0
    assert view['hidden'][0]['job_id']==b

def test_ascii_boundary_no_pmp_match():
    explanation=explain_preferences({'title':'PMP Analyst','location':'Tokyo'},'PostgreSQL',prefs(titles=['PM'],keywords=['SQL']))
    assert explanation['preference_coverage']==0

def test_preference_uses_platform_jd_first(db):
    a=job(db,1)
    import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='linkedin',url='https://www.linkedin.com/jobs/view/1',jd='Python',application_url='https://example.com/apply')
    from job_agent.reviews import merge_jobs
    b=db.execute('SELECT max(id) FROM jobs').fetchone()[0]
    merge_jobs(db,b,a,'Verified same role',confirmed=True)
    profile=save_profile(db,'Python',prefs(keywords=['Python']))
    _,report=rank_jobs(db,profile)
    assert len(report['jobs'])==1
    assert report['jobs'][0]['source']=='linkedin' and report['jobs'][0]['preference_coverage']==100

@pytest.mark.parametrize('change',['fact','job','source','status'])
def test_historical_ranking_stale_on_change(db,change):
    a=job(db,1); profile=save_profile(db,'Target',prefs())
    ident,report=rank_jobs(db,profile)
    if change=='fact': add_fact(db,'skill','','SQL','Fixture',False)
    elif change=='job': job(db,2)
    elif change=='source': job(db,1,jd='Python')
    else:
        with db: db.execute("UPDATE jobs SET status='expired' WHERE id=?",(a,))
    assert get_ranking(db,ident)['stale']
    assert get_ranking(db,ident)['report']==report

def test_expired_rejected_and_merged_excluded(db):
    a=job(db,1); b=job(db,2); c=job(db,3)
    with db:
        db.execute("UPDATE jobs SET status='expired' WHERE id=?",(a,))
        db.execute("UPDATE jobs SET status='rejected' WHERE id=?",(b,))
    profile=save_profile(db,'Target',prefs())
    _,report=rank_jobs(db,profile)
    assert [j['job_id'] for j in report['jobs']]==[c]

def test_profile_does_not_create_facts_or_stale_resume(db):
    a=job(db,1); fact=add_fact(db,'skill','','SQL','Fixture',True)
    version=create_draft(db,a,'en',[fact]); approve_version(db,version,expected_hash=get_version(db,version)['content_hash'],confirmed=True)
    profile=save_profile(db,'Target',prefs(keywords=['Python']))
    rank_jobs(db,profile)
    assert db.execute('SELECT count(*) FROM facts').fetchone()[0]==1
    assert validate_version(db,version)['status']=='approved'

def test_unknown_profile_and_run(db):
    with pytest.raises(ValueError): get_profile(db,999)
    with pytest.raises(ValueError): get_ranking(db,999)

@pytest.mark.parametrize('minimum',[-1,101,'80',True,float('nan')])
def test_bad_filter_threshold(minimum):
    with pytest.raises(ValueError): filter_ranked({'jobs':[]},min_coverage=minimum)

def test_profile_tampering_detected(db):
    ident=save_profile(db,'Target',prefs())
    with db: db.execute("UPDATE search_profiles SET preferences_json='{}' WHERE id=?",(ident,))
    with pytest.raises(ValueError): get_profile(db,ident)

def test_ranking_batch_rollback(db,monkeypatch):
    import job_agent.preferences as module
    a=job(db,1); b=job(db,2)
    profile=save_profile(db,'Target',prefs())
    monkeypatch.setattr(module,'explain_preferences',lambda *args: (_ for _ in ()).throw(ValueError('Fixture failure')))
    with pytest.raises(ValueError): rank_jobs(db,profile)
    assert db.execute('SELECT count(*) FROM ranking_runs').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM match_reports').fetchone()[0]==0
    assert db.execute('SELECT status FROM jobs WHERE id=?',(a,)).fetchone()[0]=='imported'

def test_ui_profile_and_ranking(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    c=original(path); job(c,1); c.close()
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(w for w in app.text_input if w.label=='偏好名称').set_value('Analyst Tokyo')
    next(w for w in app.text_area if w.label=='目标职位关键词（每行一个，可填写日英别名）').set_value('Analyst')
    next(w for w in app.text_area if w.label=='偏好地点关键词（每行一个，可填写城市别名）').set_value('Tokyo')
    next(w for w in app.button if w.label=='保存偏好版本').click().run()
    assert not app.exception
    next(w for w in app.button if w.label=='按偏好匹配并排序本地岗位').click().run()
    assert not app.exception
    c=original(path)
    report=get_ranking(c,1)
    assert report['report']['jobs'][0]['preference_coverage']==100 and not report['stale']
    c.close()


def test_pipeline_with_preferences_and_no_sources_stays_offline(db):
    from job_agent.pipeline import collect_and_match
    a=job(db,1,title='Analyst'); b=job(db,2,title='Product Manager')
    profile=save_profile(db,'PM',prefs(titles=['Product Manager']))
    class NoNetwork:
        def get(self,url): pytest.fail('Network must not be used without authorized sources')
    result=collect_and_match(db,profile_id=profile,transport=NoNetwork())
    assert result['sources']==[] and result['matches'][0]['job_id']==b
    assert result['ranking_run_id'] and not get_ranking(db,result['ranking_run_id'])['stale']

def test_pipeline_invalid_profile_fails_before_fetch(db):
    from job_agent.pipeline import collect_and_match
    with pytest.raises(ValueError): collect_and_match(db,profile_id=999)

def test_snapshot_integrity_warning(db):
    job(db,1); profile=save_profile(db,'Target',prefs())
    ident,report=rank_jobs(db,profile)
    report['jobs'][0]['coverage']=100
    with db: db.execute('UPDATE ranking_runs SET report_json=? WHERE id=?',(json.dumps(report),ident))
    result=get_ranking(db,ident)
    assert result['stale'] and not result['integrity_ok']
