from pathlib import Path
from streamlit.testing.v1 import AppTest
import job_agent.core as core

def test_ui_workflow(tmp_path, monkeypatch):
    original=core.connect
    monkeypatch.setattr(core,'connect',lambda:original(tmp_path/'ui.sqlite'))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    assert not app.exception
    app.text_input[0].set_value('UI Fixture Co')
    app.text_input[1].set_value('Analyst')
    app.text_input[2].set_value('Tokyo')
    app.text_input[3].set_value('https://careers.example.com/ui')
    app.text_area[0].set_value('SQL data analysis')
    app.button[0].click().run()
    assert not app.exception
    assert app.success
    app.text_input[6].set_value('skill')
    app.text_area[2].set_value('SQL')
    app.text_input[7].set_value('Test fixture only')
    app.checkbox[0].check()
    app.button[1].click().run()
    assert not app.exception
    app.button[2].click().run()
    assert not app.exception
    assert len(app.json)==1
    db=original(tmp_path/'ui.sqlite')
    assert db.execute('SELECT count(*) FROM facts WHERE confirmed=1').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM match_reports').fetchone()[0]==1
    db.close()


def test_ui_collection_empty_sources_does_not_network(tmp_path,monkeypatch):
    original=core.connect
    monkeypatch.setattr(core,'connect',lambda:original(tmp_path/'ui.sqlite'))
    import job_agent.sources as sources
    monkeypatch.setattr(sources.ReadOnlyTransport,'get',lambda *a:(_ for _ in ()).throw(AssertionError('network forbidden')))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(button for button in app.button if button.label=='采集已授权来源并匹配').click().run()
    assert not app.exception
    assert len(app.json)==1
