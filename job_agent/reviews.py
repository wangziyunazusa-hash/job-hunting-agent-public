"""Explicit, reversible source-link grouping. Original jobs and reports are retained."""
import json
from datetime import datetime, timezone
from .core import savepoint


def active_job(db, ident):
    row=db.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone()
    if row is None or row['merged_into'] is not None:
        raise ValueError('岗位不存在或已合并，请刷新列表')
    return row


def validate_pair(db, left, right, reason, confirmed):
    if left==right or not reason.strip() or not confirmed:
        raise ValueError('请选择两个不同岗位、填写核对依据并明确确认')
    active_job(db,left); active_job(db,right)


def mark_distinct(db, left, right, reason, *, confirmed=False):
    with savepoint(db):
        validate_pair(db,left,right,reason,confirmed)
        left,right=sorted((left,right))
        db.execute('INSERT INTO duplicate_reviews(left_job_id,right_job_id,reason) VALUES(?,?,?) ON CONFLICT(left_job_id,right_job_id) DO UPDATE SET reason=excluded.reason,created_at=CURRENT_TIMESTAMP',(left,right,reason.strip()))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('jobs_distinct',left))


def merge_jobs(db, source, target, reason, *, confirmed=False):
    with savepoint(db):
        validate_pair(db,source,target,reason,confirmed)
        # Disallow nested groups so each undo has an unambiguous original membership.
        if db.execute('SELECT 1 FROM jobs WHERE merged_into IN (?,?)',(source,target)).fetchone():
            raise ValueError('这两个岗位已有合并成员；请先撤销已有合并')
        ids=[r[0] for r in db.execute('SELECT id FROM job_source_links WHERE job_id=?',(source,))]
        if not ids: raise ValueError('来源岗位没有可合并链接')
        db.execute('UPDATE job_source_links SET job_id=? WHERE job_id=?',(target,source))
        db.execute('UPDATE jobs SET merged_into=? WHERE id=?',(target,source))
        ident=db.execute('INSERT INTO job_merges(source_job_id,target_job_id,link_ids_json,reason) VALUES(?,?,?,?)',(source,target,json.dumps(ids),reason.strip())).lastrowid
        db.execute('DELETE FROM duplicate_reviews WHERE left_job_id=? AND right_job_id=?',tuple(sorted((source,target))))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('jobs_merged',ident))
    return ident


def undo_merge(db, ident):
    with savepoint(db):
        row=db.execute('SELECT * FROM job_merges WHERE id=?',(ident,)).fetchone()
        if row is None or row['undone_at']: raise ValueError('合并记录不存在或已撤销')
        active_job(db,row['target_job_id'])
        source=db.execute('SELECT * FROM jobs WHERE id=?',(row['source_job_id'],)).fetchone()
        if source['merged_into']!=row['target_job_id']: raise ValueError('合并关系发生变化，不能撤销')
        ids=json.loads(row['link_ids_json'])
        for link_id in ids:
            link=db.execute('SELECT job_id FROM job_source_links WHERE id=?',(link_id,)).fetchone()
            if link is None or link[0]!=row['target_job_id']: raise ValueError('来源关系发生变化，撤销已取消')
        for link_id in ids:
            db.execute('UPDATE job_source_links SET job_id=? WHERE id=?',(row['source_job_id'],link_id))
        db.execute('UPDATE jobs SET merged_into=NULL WHERE id=?',(row['source_job_id'],))
        db.execute('UPDATE job_merges SET undone_at=? WHERE id=?',(datetime.now(timezone.utc).isoformat(),ident))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('merge_undone',ident))
