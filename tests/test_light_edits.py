import json
from pathlib import Path
import pytest
from job_agent.core import connect, import_job, add_fact, review_fact
from job_agent.light_edits import *
from job_agent.resumes import create_draft, approve_version, get_version, validate_version

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'edits.sqlite'); yield c; c.close()

def seed(db,original='Used SQL for reports.',language='en',locks=None,facts=True):
    job=import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1',jd='SQL reporting required. Python preferred.')
    fact=add_fact(db,'experience','SQL でレポートを作成した。','Used SQL for reports.','Fixture private evidence',True) if facts else None
    ident=save_passage(db,job,language,'Experience bullet 1',original,[fact] if fact else [],locks or [])
    return job,fact,ident

def proposal(db):
    job,fact,passage=seed(db)
    suggestion=propose_edit(db,passage,'Used SQL for reporting.','Use clearer wording for reporting.','SQL reporting required.')
    return passage,suggestion

def test_advice_keeps_original_and_does_not_add_jd_skills(db):
    job,fact,passage=seed(db)
    report=advice(db,passage)
    assert report['replacement']=='Used SQL for reports.'
    assert report['change_type']=='keep_original'
    assert report['matched_jd_terms']==['sql']
    assert any(g['term']=='python' and '不补写' in g['advice'] for g in report['gaps'])
    assert db.execute('SELECT count(*) FROM resume_edit_suggestions').fetchone()[0]==0

def test_whitespace_only_preserves_indentation_and_bullets(db):
    original='  - Used SQL for reports.  \n    - Assisted team.\t'
    job,fact,passage=seed(db,original)
    report=advice(db,passage)
    assert report['replacement']=='  - Used SQL for reports.\n    - Assisted team.'
    ident=propose_edit(db,passage,report['replacement'],'Remove trailing spaces only.','SQL reporting required.',origin='whitespace_rule')
    assert get_suggestion(db,ident)['status']=='review_required'

def test_small_suggestion_review_and_text_export(db):
    passage,suggestion=proposal(db)
    row=get_suggestion(db,suggestion)
    assert '-Used SQL for reports.' in row['diff'] and '+Used SQL for reporting.' in row['diff']
    with pytest.raises(ValueError): review_suggestion(db,suggestion,expected_hash=row['suggestion_hash'],accept=True)
    review_suggestion(db,suggestion,expected_hash=row['suggestion_hash'],confirmed=True,accept=True)
    assert get_suggestion(db,suggestion)['status']=='accepted'
    output=export_text(db,suggestion)
    assert 'Used SQL for reports.' in output and 'Used SQL for reporting.' in output and '修改理由' in output
    assert get_passage(db,passage)['original_text']=='Used SQL for reports.'
    assert db.execute('SELECT count(*) FROM facts').fetchone()[0]==1

def test_reject_does_not_change_original(db):
    passage,suggestion=proposal(db)
    review_suggestion(db,suggestion,expected_hash=get_suggestion(db,suggestion)['suggestion_hash'],confirmed=True,accept=False)
    assert get_suggestion(db,suggestion)['status']=='rejected'
    assert get_passage(db,passage)['original_text']=='Used SQL for reports.'

@pytest.mark.parametrize('original,replacement,locks',[
    ('Used SQL for 12 reports.','Used SQL for 20 reports.',[]),
    ('JLPT N2; used SQL.','JLPT N1; used SQL.',[]),
    ('Worked 2021/04–2023/03; SQL reports.','Worked 2021/04–2024/03; SQL reports.',[]),
    ('Used SQL at ExampleCo.','Used SQL at AnotherCo.',['ExampleCo']),
    ('Assistant: used SQL.','Manager: used SQL.',['Assistant']),
    ('Supported SQL reporting.','Led SQL reporting.',[]),
    ('SQL レポートの作成を支援した。','SQL レポートの作成を主導した。',[]),
    ('Used SQL for reports.','Used SQL and Python for reports.',[]),
    ('Used SQL for reports.','Used SQL.\nMade reports.',[]),
    ('Used SQL for reports.','Created a company-wide architecture and independently transformed the whole business.',[]),
])
def test_unsafe_or_large_edits_rejected(db,original,replacement,locks):
    job,fact,passage=seed(db,original,locks=locks)
    with pytest.raises(ValueError): propose_edit(db,passage,replacement,'Fixture proposal','SQL reporting required.')
    assert db.execute('SELECT count(*) FROM resume_edit_suggestions').fetchone()[0]==0

def test_no_facts_no_fabricated_replacement(db):
    job,fact,passage=seed(db,facts=False)
    assert advice(db,passage)['replacement']=='Used SQL for reports.'
    with pytest.raises(ValueError,match='已确认事实'): propose_edit(db,passage,'Used SQL for reporting.','Clarify','SQL reporting required.')

def test_exact_jd_quote_required(db):
    job,fact,passage=seed(db)
    with pytest.raises(ValueError): propose_edit(db,passage,'Used SQL for reporting.','Clarify','Invented JD requirement')

def test_japanese_small_text_suggestion(db):
    job,fact,passage=seed(db,'SQL でレポートを作成した。',language='ja')
    ident=propose_edit(db,passage,'SQL を用いてレポートを作成した。','語句のみ整える','SQL reporting required.')
    assert get_suggestion(db,ident)['replacement']=='SQL を用いてレポートを作成した。'

def test_pending_or_wrong_language_facts_rejected(db):
    job,fact,passage=seed(db)
    pending=add_fact(db,'skill','','SQL','Fixture',False)
    with pytest.raises(ValueError): save_passage(db,job,'en','Bullet','SQL',[pending],[])
    ja=add_fact(db,'skill','SQL','','Fixture',True)
    with pytest.raises(ValueError): save_passage(db,job,'en','Bullet','SQL',[ja],[])

@pytest.mark.parametrize('original,locks',[
    ('X'*3001,[]),('Line\n'*9,[]),('SQL',['not in original']),('',[])
])
def test_passage_scope_and_lock_validation(db,original,locks):
    job,fact,passage=seed(db)
    with pytest.raises(ValueError): save_passage(db,job,'en','Bullet',original,[fact],locks)

def test_underlying_fact_change_invalidates_suggestion(db):
    passage,suggestion=proposal(db)
    review_fact(db,1,confirmed=False,reviewed=True)
    assert get_suggestion(db,suggestion)['status']=='obsolete'
    with pytest.raises(ValueError): export_text(db,suggestion)

def test_suggestion_does_not_modify_approved_resume(db):
    job,fact,passage=seed(db)
    resume=create_draft(db,job,'en',[fact]); approve_version(db,resume,expected_hash=get_version(db,resume)['content_hash'],confirmed=True)
    suggestion=propose_edit(db,passage,'Used SQL for reporting.','Clarify','SQL reporting required.')
    review_suggestion(db,suggestion,expected_hash=get_suggestion(db,suggestion)['suggestion_hash'],confirmed=True,accept=True)
    assert validate_version(db,resume)['status']=='approved'
    assert 'Used SQL for reporting.' not in get_version(db,resume)['content']

def test_offline_brief_is_text_only_no_evidence_path(db):
    job,fact,passage=seed(db)
    brief=editing_brief(db,passage)
    assert brief['api_enabled'] is False and brief['scope']=='light_touch_text_only'
    assert 'Fixture private evidence' not in json.dumps(brief)

def test_tampering_and_wrong_review_hash(db):
    passage,suggestion=proposal(db)
    with pytest.raises(ValueError): review_suggestion(db,suggestion,expected_hash='wrong',confirmed=True,accept=True)
    with db: db.execute("UPDATE resume_edit_suggestions SET replacement='Invented replacement' WHERE id=?",(suggestion,))
    assert get_suggestion(db,suggestion)['status']=='obsolete'

def test_unknown_ids(db):
    with pytest.raises(ValueError): get_passage(db,999)
    with pytest.raises(ValueError): get_suggestion(db,999)

def test_ui_small_edit_text_review(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    c=original(path)
    job=import_job(c,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1',jd='SQL reporting required.')
    add_fact(c,'experience','SQL でレポートを作成した。','Used SQL for reports.','Fixture',True); c.close()
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(w for w in app.selectbox if w.label=='原文语言').select('en').run()
    next(w for w in app.text_input if w.label=='原文位置（例如：工作经历第 2 条）').set_value('Experience 1')
    next(w for w in app.text_area if w.label=='粘贴这一条日语或英语简历原文').set_value('Used SQL for reports.')
    facts=next(w for w in app.multiselect if w.label=='支持这段经历的已确认事实'); facts.select(facts.options[0])
    next(w for w in app.button if w.label=='保存原文并查看轻量建议').click().run()
    assert not app.exception and not app.error
    next(w for w in app.text_area if w.label=='建议替换文本（仅小改）').set_value('Used SQL for reporting.')
    next(w for w in app.text_area if w.label=='修改理由及对应岗位诉求').set_value('Clarify reporting wording')
    next(w for w in app.text_area if w.label=='对应 JD 逐字原文').set_value('SQL reporting required.')
    next(w for w in app.button if w.label=='保存待审阅的小改建议').click().run()
    assert not app.exception and not app.error
    next(w for w in app.checkbox if w.label=='我已核实原文、替换文本及事实依据，硬信息和责任范围没有改变').check()
    next(w for w in app.radio if w.label=='这条建议的决定').set_value('采纳文字建议，由我自行替换')
    next(w for w in app.button if w.label=='保存这条建议的决定').click().run()
    assert not app.exception and not app.error
    c=original(path); assert get_suggestion(c,1)['status']=='accepted'; assert c.execute('SELECT text_en FROM facts').fetchone()[0]=='Used SQL for reports.'; c.close()

@pytest.mark.parametrize('original,replacement',[
    ('Supported SQL reporting.','Managed SQL reporting.'),
    ('  - Used SQL for reports.','- Used SQL for reporting.'),
    ('Used SQL for reports.','- Used SQL for reports.'),
])
def test_additional_responsibility_and_structure_guards(db,original,replacement):
    job,fact,passage=seed(db,original)
    with pytest.raises(ValueError): propose_edit(db,passage,replacement,'Fixture','SQL reporting required.')


def test_jd_change_requires_new_edit_review(db):
    passage,suggestion=proposal(db)
    import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1',jd='A different role')
    assert get_suggestion(db,suggestion)['status']=='obsolete'
    with pytest.raises(ValueError): advice(db,passage)
