import json
from pathlib import Path
import sqlite3
import pytest
from job_agent.core import connect, import_job, add_fact
from job_agent.database import *

@pytest.fixture
def db(tmp_path):
    c=connect(tmp_path/'active.sqlite'); yield c; c.close()

def seed(db):
    import_job(db,company='Fixture',title='Analyst',location='Tokyo',source='official',url='https://example.com/1',jd='SQL')
    add_fact(db,'skill','','SQL','Fixture evidence only',True)

def test_health_new_db(db):
    report=health(db)
    assert report['healthy'] and report['schema_current']
    assert report['schema_versions']==list(range(1,8))
    assert report['row_counts']['facts']==0
    assert report['missing_schema_objects']==[]

def test_backup_and_non_destructive_restore(db):
    seed(db); backup=create_backup(db); folder=main_path(db).parent/'backups'
    assert verify_backup(folder,backup['name'])['sha256']==backup['sha256']
    assert list_backups(folder)[0]['valid']
    add_fact(db,'skill','','Python','Fixture',True)
    with pytest.raises(ValueError): restore_copy(db,folder,backup['name'])
    restored=restore_copy(db,folder,backup['name'],confirmed=True)
    assert restored.resolve()!=main_path(db).resolve()
    c=readonly(restored)
    assert health(c)['row_counts']['facts']==1
    assert health(db)['row_counts']['facts']==2
    c.close()
    assert restored.stat().st_mode & 0o777==0o600
    assert (folder/backup['name']).stat().st_mode & 0o777==0o600

def test_unique_backup_names_and_permissions(db):
    a=create_backup(db); b=create_backup(db)
    assert a['name']!=b['name']
    assert NAME.fullmatch(a['name'])
    assert main_path(db).stat().st_mode & 0o777==0o600

def test_transaction_backup_refused(db):
    db.execute("INSERT INTO audit_events(action) VALUES('uncommitted')")
    with pytest.raises(ValueError,match='事务'): create_backup(db)
    db.rollback()
    assert list_backups(main_path(db).parent/'backups')==[]

def test_backup_tampering_refused(db):
    metadata=create_backup(db); folder=main_path(db).parent/'backups'
    with (folder/metadata['name']).open('ab') as file: file.write(b'corrupt')
    with pytest.raises(ValueError): verify_backup(folder,metadata['name'])
    assert list_backups(folder)[0]['valid'] is False
    with pytest.raises(ValueError): restore_copy(db,folder,metadata['name'],confirmed=True)

def test_manifest_tampering_refused(db):
    metadata=create_backup(db); folder=main_path(db).parent/'backups'
    file=folder/(metadata['name']+'.json'); data=json.loads(file.read_text()); data['sha256']='wrong'; file.write_text(json.dumps(data))
    with pytest.raises(ValueError): verify_backup(folder,metadata['name'])

@pytest.mark.parametrize('name',['../active.sqlite','active.sqlite','/tmp/active.sqlite','backup_bad.sqlite3'])
def test_path_traversal_refused(tmp_path,name):
    with pytest.raises(ValueError): verify_backup(tmp_path,name)

def test_symlink_backup_refused(db,tmp_path):
    metadata=create_backup(db); folder=main_path(db).parent/'backups'; path=folder/metadata['name']
    other=tmp_path/'other.sqlite'; path.rename(other); path.symlink_to(other)
    with pytest.raises(ValueError): verify_backup(folder,metadata['name'])

def test_symlink_db_refused(db,tmp_path):
    link=tmp_path/'link.sqlite'; link.symlink_to(main_path(db))
    with pytest.raises(ValueError): connect(link)

def test_unknown_future_schema_not_modified(tmp_path):
    path=tmp_path/'future.sqlite'; c=sqlite3.connect(path)
    c.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)')
    c.executemany('INSERT INTO schema_migrations VALUES(?)',[(i,) for i in range(1,9)]); c.commit(); c.close()
    before=sha256(path)
    with pytest.raises(ValueError): connect(path)
    assert sha256(path)==before

def test_schema_gap_not_modified(tmp_path):
    path=tmp_path/'gap.sqlite'; c=sqlite3.connect(path)
    c.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)'); c.execute('INSERT INTO schema_migrations VALUES(2)'); c.commit(); c.close()
    before=sha256(path)
    with pytest.raises(ValueError): connect(path)
    assert sha256(path)==before

def test_unrecognized_database_not_modified(tmp_path):
    path=tmp_path/'other.sqlite'; c=sqlite3.connect(path); c.execute('CREATE TABLE unrelated(id INTEGER)'); c.close()
    before=sha256(path)
    with pytest.raises(ValueError): connect(path)
    assert sha256(path)==before

def test_missing_schema_table_detected(db):
    with db: db.execute('DROP TABLE semantic_reports')
    assert not health(db)['healthy']
    assert 'semantic_reports' in health(db)['missing_schema_objects']

def test_upgrade_creates_valid_pre_migration_backup(tmp_path):
    path=tmp_path/'old.sqlite'; c=sqlite3.connect(path)
    c.executescript((ROOT/'migrations/001_initial.sql').read_text())
    c.execute("INSERT INTO facts(category,text_en,evidence,confirmed) VALUES('skill','SQL','Fixture',1)"); c.commit(); c.close()
    upgraded=connect(path)
    assert health(upgraded)['schema_current']
    assert health(upgraded)['row_counts']['facts']==1
    copies=list_backups(path.parent/'backups')
    assert len(copies)==1 and copies[0]['schema_versions']==[1] and copies[0]['reason']=='before_migration'
    restored=restore_copy(upgraded,path.parent/'backups',copies[0]['name'],confirmed=True)
    old=readonly(restored); assert schema_versions(old)==[1]; old.close()
    opened=connect(restored)
    assert health(opened)['schema_current'] and health(opened)['row_counts']['facts']==1
    opened.close(); upgraded.close()

def test_failed_backup_prevents_upgrade(tmp_path,monkeypatch):
    import job_agent.database as module
    path=tmp_path/'old.sqlite'; c=sqlite3.connect(path); c.executescript((ROOT/'migrations/001_initial.sql').read_text()); c.close()
    before=sha256(path)
    monkeypatch.setattr(module,'create_backup',lambda *a,**kw:(_ for _ in ()).throw(OSError('Fixture disk full')))
    with pytest.raises(OSError): connect(path)
    assert sha256(path)==before

def test_migration_failure_keeps_backup_and_rolls_back(tmp_path,monkeypatch):
    import job_agent.database as module
    path=tmp_path/'old.sqlite'; c=sqlite3.connect(path); c.executescript((ROOT/'migrations/001_initial.sql').read_text()); c.close()
    bad=tmp_path/'bad_migration.sql'; bad.write_text('BEGIN IMMEDIATE; CREATE TABLE should_rollback(id); INSERT INTO nonexistent VALUES(1); COMMIT;')
    original=module.migrations
    monkeypatch.setattr(module,'migrations',lambda:[original()[0],bad]+original()[2:])
    with pytest.raises(sqlite3.Error): connect(path)
    c=readonly(path)
    assert schema_versions(c)==[1]
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='should_rollback'").fetchone()
    assert len(list_backups(path.parent/'backups'))==1
    c.close()

def test_current_reopen_does_not_create_backups(db):
    c=connect(main_path(db)); c.close()
    assert list_backups(main_path(db).parent/'backups')==[]

def test_in_memory_maintenance_refused():
    c=sqlite3.connect(':memory:')
    with pytest.raises(ValueError): main_path(c)
    c.close()

def test_ui_health_backup_and_restore(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import job_agent.core as core
    original=core.connect; path=tmp_path/'ui.sqlite'
    c=original(path); seed(c); c.close()
    monkeypatch.setattr(core,'connect',lambda:original(path))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
    next(w for w in app.button if w.label=='检查数据库状态').click().run()
    assert not app.exception and not app.error
    next(w for w in app.button if w.label=='创建本地一致性备份').click().run()
    assert not app.exception and not app.error
    next(w for w in app.checkbox if w.label=='确认只创建独立恢复副本，不覆盖正在使用的数据库').check()
    next(w for w in app.button if w.label=='从备份生成恢复副本').click().run()
    assert not app.exception and not app.error
    assert len(list((tmp_path/'restored').glob('*.sqlite3')))==1
    c=original(path); assert health(c)['row_counts']['facts']==1; c.close()


def test_copy_destination_collision_never_deletes_existing_file(db,tmp_path):
    target=tmp_path/'existing.sqlite'; target.write_bytes(b'keep-existing-bytes')
    with pytest.raises(FileExistsError): copy_database(db,target)
    assert target.read_bytes()==b'keep-existing-bytes'


def test_backup_name_collision_keeps_existing_backup(db,monkeypatch):
    import job_agent.database as module
    metadata=create_backup(db); folder=main_path(db).parent/'backups'
    original=module.datetime
    class FrozenDatetime:
        @staticmethod
        def now(tz):
            timestamp=metadata['name'].split('_')[1]
            return original.strptime(timestamp,'%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
    class FrozenUUID:
        hex=metadata['name'].split('_')[2].split('.')[0]+'0'*20
    monkeypatch.setattr(module,'datetime',FrozenDatetime)
    monkeypatch.setattr(module.uuid,'uuid4',lambda:FrozenUUID())
    before=(folder/metadata['name']).read_bytes()
    with pytest.raises(FileExistsError): create_backup(db)
    assert (folder/metadata['name']).read_bytes()==before
    assert verify_backup(folder,metadata['name'])['sha256']==metadata['sha256']



def test_explicit_alternate_db_path_for_restore(tmp_path,monkeypatch):
    original=tmp_path/'original.sqlite3'
    c=connect(original); seed(c)
    metadata=create_backup(c); restored=restore_copy(c,original.parent/'backups',metadata['name'],confirmed=True)
    c.close()
    monkeypatch.setenv('JOB_AGENT_DB_PATH',str(restored))
    selected=connect()
    assert main_path(selected).resolve()==restored.resolve()
    assert health(selected)['row_counts']['facts']==1
    add_fact(selected,'skill','','New restored copy fact','Fixture',True)
    selected.close()
    original_db=connect(original)
    assert health(original_db)['row_counts']['facts']==1
    original_db.close()

@pytest.mark.parametrize('value',['relative.sqlite3','postgres://localhost/db','/tmp/not-a-database.txt'])
def test_alternate_db_config_validation(monkeypatch,value):
    monkeypatch.setenv('JOB_AGENT_DB_PATH',value)
    with pytest.raises(ValueError): connect()
