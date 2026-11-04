"""Local SQLite maintenance; managed backups and non-destructive restore copies."""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
NAME=re.compile(r'backup_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{12}\.sqlite3')
TABLES=('jobs','job_source_links','jd_snapshots','facts','match_reports','resume_versions',
        'applications','search_profiles','ranking_runs','semantic_requests','semantic_reports','batch_imports')
REQUIRED={
    1:{'schema_migrations','jobs','job_source_links','jd_snapshots','facts','match_reports','audit_events'},
    2:{'source_permissions','sync_runs','source_postings','api_cache'},
    3:{'job_merges','duplicate_reviews'},4:{'resume_versions','resume_claims'},
    5:{'applications','application_events'},6:{'search_profiles','ranking_runs'},
    7:{'semantic_requests','semantic_reports'},8:{'batch_imports'}}


def layout_errors(db, versions):
    actual={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required=set().union(*(tables for version,tables in REQUIRED.items() if version in versions))
    missing=sorted(required-actual)
    for version,table,columns in [(2,'job_source_links',{'latest_snapshot_id','last_observed_at'}),(3,'jobs',{'merged_into'}),(4,'facts',{'revision'})]:
        if version in versions and table in actual:
            found={r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
            missing.extend(table+'.'+column for column in sorted(columns-found))
    return missing



def migrations():
    files=sorted((ROOT/'migrations').glob('*.sql'))
    versions=[int(f.name.split('_')[0]) for f in files]
    if not versions or versions!=list(range(1,len(versions)+1)): raise ValueError('项目迁移文件缺失或顺序异常')
    return files


def schema_versions(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone(): return []
    return [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]


def health(db):
    versions=schema_versions(db); supported=list(range(1,len(migrations())+1))
    quick=[r[0] for r in db.execute('PRAGMA quick_check(20)')]
    fk=db.execute('PRAGMA foreign_key_check').fetchmany(21)
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts={name:db.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0] for name in TABLES if name in tables}
    missing=layout_errors(db,versions)
    compatible=bool(versions) and versions==supported[:len(versions)]
    return {'healthy':quick==['ok'] and not fk and compatible and not missing,'quick_check_ok':quick==['ok'],
            'foreign_key_errors':len(fk),'foreign_key_errors_truncated':len(fk)>20,
            'schema_versions':versions,'latest_supported_version':supported[-1],
            'schema_current':versions==supported,'missing_schema_objects':missing,'row_counts':counts}


def main_path(db):
    rows=db.execute('PRAGMA database_list').fetchall()
    filename=next((r[2] for r in rows if r[1]=='main'),'')
    if not filename: raise ValueError('维护操作需要磁盘数据库')
    return Path(filename)


def secure_file(path):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)


def managed_dir(path):
    path=Path(path)
    if path.is_symlink(): raise ValueError('维护目录不能是符号链接')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    return path


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)


def copy_database(source, destination):
    if source.in_transaction: raise ValueError('请先完成当前事务再备份，避免等待未提交写入')
    secure_file(destination)
    target=None
    started=time.monotonic()
    def progress(status, remaining, total):
        if time.monotonic()-started>15: raise TimeoutError('备份等待过久，请稍后重试')
    try:
        target=sqlite3.connect(destination)
        source.backup(target,pages=64,progress=progress,sleep=0.05)
        report=health(target)
        if not report['healthy']: raise ValueError('副本完整性或外键检查失败')
        return report
    except Exception:
        if target is not None: target.close(); target=None
        Path(destination).unlink(missing_ok=True)
        raise
    finally:
        if target is not None: target.close()


def sha256(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): value.update(block)
    return value.hexdigest()


def create_backup(db, directory=None, *, reason='manual'):
    if reason not in ('manual','before_migration'): raise ValueError('未知备份类型')
    folder=managed_dir(directory or main_path(db).parent/'backups')
    name='backup_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex[:12]+'.sqlite3'
    path=folder/name; manifest=folder/(name+'.json')
    copied=False; manifest_created=False
    try:
        report=copy_database(db,path)
        copied=True
        metadata={'format_version':1,'name':name,'created_at':datetime.now(timezone.utc).isoformat(),
                  'reason':reason,'bytes':path.stat().st_size,'sha256':sha256(path),'schema_versions':report['schema_versions']}
        secure_file(manifest)
        manifest_created=True
        manifest.write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
        return metadata
    except Exception:
        if copied: path.unlink(missing_ok=True)
        if manifest_created: manifest.unlink(missing_ok=True)
        raise


def verify_backup(directory, name):
    if not isinstance(name,str) or NAME.fullmatch(name) is None: raise ValueError('非法备份文件名')
    folder=Path(directory)
    if folder.is_symlink(): raise ValueError('备份目录不能是符号链接')
    path=folder/name; manifest=folder/(name+'.json')
    if path.is_symlink() or manifest.is_symlink() or not path.is_file() or not manifest.is_file(): raise ValueError('备份或元信息缺失')
    if manifest.stat().st_size>16384: raise ValueError('备份元信息过大')
    try: data=json.loads(manifest.read_text())
    except (ValueError,UnicodeError) as exc: raise ValueError('备份元信息无效') from exc
    if not isinstance(data,dict) or data.get('format_version')!=1 or data.get('name')!=name or data.get('bytes')!=path.stat().st_size or data.get('sha256')!=sha256(path):
        raise ValueError('备份大小或摘要校验失败')
    source=readonly(path)
    try:
        report=health(source)
        if not report['healthy'] or report['schema_versions']!=data.get('schema_versions'): raise ValueError('备份数据库检查失败或版本不支持')
    finally: source.close()
    return data


def list_backups(directory):
    folder=Path(directory)
    if folder.is_symlink(): raise ValueError('备份目录不能是符号链接')
    if not folder.exists(): return []
    result=[]
    for path in sorted(folder.glob('backup_*.sqlite3'),reverse=True)[:50]:
        try: result.append({**verify_backup(folder,path.name),'valid':True})
        except (ValueError,sqlite3.Error,OSError): result.append({'name':path.name,'valid':False,'error':'备份缺失、损坏、摘要不符或版本不受支持'})
    return result


def restore_copy(db, directory, name, *, confirmed=False):
    if not confirmed: raise ValueError('请明确确认创建独立恢复副本；不会覆盖当前数据库')
    original=verify_backup(directory,name)
    active=main_path(db).resolve()
    folder=managed_dir(active.parent/'restored')
    target=folder/('restored_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex[:12]+'.sqlite3')
    source=readonly(Path(directory)/name)
    copied=False
    try:
        copy_database(source,target)
        copied=True
        # Validate again after copying, not just before.
        if verify_backup(directory,name)['sha256']!=original['sha256']: raise ValueError('复制期间备份发生变化')
        return target
    except Exception:
        if copied: target.unlink(missing_ok=True)
        raise
    finally: source.close()


def migrate(db):
    files=migrations(); versions=schema_versions(db)
    tables=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    if versions!=list(range(1,len(versions)+1)) or (versions and versions[-1]>len(files)) or (tables and not versions):
        raise ValueError('数据库版本缺失、超出当前程序或不是本项目格式；不会自动改写')
    if versions and layout_errors(db,versions): raise ValueError('数据库表结构与记录版本不一致；不会自动改写')
    if versions==list(range(1,len(files)+1)): return
    # A consistent copy must succeed before modifying an existing database.
    if versions: create_backup(db,reason='before_migration')
    try:
        for index,file in enumerate(files,1):
            if index in versions: continue
            source=file.read_text()
            if index==1: source='BEGIN IMMEDIATE;\n'+source+'\nCOMMIT;'
            db.executescript(source)
            db.execute('PRAGMA foreign_keys=ON')
        if not health(db)['healthy']: raise ValueError('迁移后检查失败，请保留原备份进行检查')
    except Exception:
        if db.in_transaction: db.rollback()
        db.execute('PRAGMA foreign_keys=ON')
        raise
