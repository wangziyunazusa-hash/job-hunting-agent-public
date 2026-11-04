"""Manual CSV/JSON job imports, simulated previews and explicit atomic commit."""
import csv
import hashlib
import io
import json
import sqlite3
import time
from itertools import islice
from datetime import datetime, timezone
from .core import import_job, safe_url, check_source, norm, savepoint, possible_duplicates, source_company_allowed
from .resumes import digest

REQUIRED={'company','title','location','source','url','jd'}
OPTIONAL={'application_url','requisition_id'}
MAX_BYTES=1024*1024
MAX_ROWS=50



def decode_upload(content):
    if not isinstance(content,bytes) or len(content)>MAX_BYTES: raise ValueError('上传文件最多 1 MiB')
    try: return content.decode('utf-8-sig')
    except UnicodeError as exc: raise ValueError('文件需要 UTF-8 编码，请先另存为 UTF-8；不会猜测或替换乱码') from exc

def parse_rows(raw, format):
    if not isinstance(raw,str) or len(raw.encode())>MAX_BYTES: raise ValueError('单次导入最多 1 MiB 文本')
    raw=raw.removeprefix('\ufeff')
    if format=='json':
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result: raise ValueError('JSON 字段重复')
                result[key]=value
            return result
        try:
            rows=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('不允许非有限数字')))
        except (ValueError,RecursionError) as exc: raise ValueError('请输入严格 JSON 数组，不能含重复字段或代码围栏') from exc
    elif format=='csv':
        try:
            reader=csv.DictReader(io.StringIO(raw,newline=''),strict=True)
            headers=reader.fieldnames
            if not headers or len(headers)!=len(set(headers)): raise ValueError('CSV 表头为空或有重复列')
            if set(headers)-REQUIRED-OPTIONAL or REQUIRED-set(headers): raise ValueError('CSV 表头字段不完整或包含未知列')
            rows=list(islice(reader,MAX_ROWS+1))
        except csv.Error as exc:
            if 'field larger' in str(exc): raise ValueError('CSV 单字段超过标准库限制，请减少 JD 长度或改用 JSON') from exc
            raise ValueError('CSV 格式不正确，请检查引号及多行字段') from exc
    else: raise ValueError('只支持 JSON 或 CSV')
    if not isinstance(rows,list) or not rows or len(rows)>MAX_ROWS: raise ValueError('每批需有 1–50 条岗位')
    normalized=[]; seen={}
    for index,row in enumerate(rows,1):
        if not isinstance(row,dict) or REQUIRED-set(row) or set(row)-REQUIRED-OPTIONAL:
            raise ValueError(f'第 {index} 条字段不完整或含未知字段')
        if any(not isinstance(value,str) for value in row.values()): raise ValueError(f'第 {index} 条所有字段必须是文本')
        item={k:row.get(k,'').strip() if k!='jd' else row[k] for k in sorted(REQUIRED|OPTIONAL)}
        if any(not item[k].strip() for k in REQUIRED) or len(item['jd'])>200000:
            raise ValueError(f'第 {index} 条缺少必填值或 JD 超过 20 万字符')
        if any(len(item[k])>500 for k in ('company','title','location')) or len(item['requisition_id'])>200 or any(len(item[k])>4000 for k in ('url','application_url')):
            raise ValueError(f'第 {index} 条元信息超长')
        try:
            item['url']=safe_url(item['url']); check_source(item['source'],item['url'])
            if item['application_url']: item['application_url']=safe_url(item['application_url'])
            if any(len(item[k])>4000 for k in ('url','application_url')): raise ValueError('规范化后的链接过长')
        except ValueError as exc: raise ValueError(f'第 {index} 条：{exc}') from exc
        if item['url'] in seen and seen[item['url']]!=item:
            raise ValueError(f'第 {index} 条与同批同链接记录内容不同，请先明确保留哪版 JD')
        seen[item['url']]=item
        normalized.append(item)
    if len(json.dumps(normalized,ensure_ascii=False).encode())>MAX_BYTES:
        raise ValueError('规范化后的整批数据超过 1 MiB，请拆分导入')
    return normalized


def fingerprint(db):
    result=hashlib.sha256()
    for table,order in (('jobs','id'),('job_source_links','id'),('jd_snapshots','id'),('job_merges','id'),('duplicate_reviews','left_job_id,right_job_id')):
        result.update(table.encode())
        for row in db.execute('SELECT * FROM '+table+' ORDER BY '+order):
            result.update(json.dumps(dict(row),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
            result.update(b'\n')
    return result.hexdigest()


def simulate(db, rows):
    report=[]; seen=set()
    for index,item in enumerate(rows,1):
        if item['url'] in seen:
            report.append({'row':index,'action':'skip_duplicate_in_batch','url':item['url'],'job_id':None,'warnings':['同批内容完全相同，只导入首次出现的记录']})
            continue
        seen.add(item['url'])
        old=db.execute('SELECT l.*,j.company,j.title,j.location,j.requisition_id FROM job_source_links l JOIN jobs j ON j.id=l.job_id WHERE l.url=?',(item['url'],)).fetchone()
        warnings=[]
        if old and not source_company_allowed(db,old,item['company']): raise ValueError(f'第 {index} 条链接已属于另一家公司，禁止改写归属')
        if old and norm(old['company'])!=norm(item['company']): warnings.append('沿用该来源经人工确认的合并关系，主岗位公司名称与输入不同')
        old_hash=None
        if old:
            previous=db.execute('SELECT content_hash FROM jd_snapshots WHERE id=?',(old['latest_snapshot_id'],)).fetchone()
            old_hash=previous[0] if previous else None
            if item['application_url'] and item['application_url']!=old['application_url']:
                warnings.append('将更新该来源的申请链接，请重新核对入口')
            if item['requisition_id'] and item['requisition_id']!=(old['requisition_id'] or ''):
                warnings.append('现有岗位企业编号保持不变，不使用输入值覆盖')
            differences=[k for k in ('title','location') if norm(old[k])!=norm(item[k])]
            if differences: warnings.append('保留首次导入的岗位元信息，输入不同字段：'+', '.join(differences))
        before_jobs=db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        before_snapshots=db.execute('SELECT count(*) FROM jd_snapshots').fetchone()[0]
        job_id=import_job(db,**item)
        link=db.execute('SELECT * FROM job_source_links WHERE url=?',(item['url'],)).fetchone()
        if db.execute('SELECT count(*) FROM jobs').fetchone()[0]>before_jobs: action='new_job'
        elif old is None: action='attach_source'
        elif db.execute('SELECT count(*) FROM jd_snapshots').fetchone()[0]>before_snapshots: action='new_jd_snapshot'
        elif old_hash!=hashlib.sha256(item['jd'].encode()).hexdigest(): action='reuse_jd_snapshot'
        else: action='refresh_existing'
        if action=='attach_source': warnings.append('按同公司及企业岗位编号或申请链接归并，请核对是否确为同一岗位')
        target=db.execute('SELECT id,company,title,location,requisition_id FROM jobs WHERE id=?',(job_id,)).fetchone()
        candidates=possible_duplicates(db,job_id)
        if candidates: warnings.append('存在同公司、同名、同地点的独立岗位，未自动合并，请人工复核')
        report.append({'row':index,'action':action,'url':item['url'],'job_id':job_id,'source_link_id':link['id'],
                       'target_job':dict(target),'possible_duplicate_ids':[r['id'] for r in candidates],'warnings':warnings})
    return {'rows':report,'new_jobs':sum(r['action']=='new_job' for r in report),
            'attached_sources':sum(r['action']=='attach_source' for r in report),
            'new_jd_snapshots':sum(r['action']=='new_jd_snapshot' for r in report),
            'reused_jd_snapshots':sum(r['action']=='reuse_jd_snapshot' for r in report),
            'refreshed_existing':sum(r['action']=='refresh_existing' for r in report),
            'skipped_duplicates':sum(r['action']=='skip_duplicate_in_batch' for r in report),
            'notice':'仅手动数据导入，不访问来源网站。刷新既有来源会更新观察时间，可能使已有简历或申请准备失效。'}


def prepare_batch(db, raw, format):
    rows=parse_rows(raw,format)
    if db.in_transaction: raise ValueError('请先结束当前事务再生成导入预览')
    copy=sqlite3.connect(':memory:'); copy.row_factory=sqlite3.Row
    started=time.monotonic()
    def progress(status, remaining, total):
        if time.monotonic()-started>15: raise TimeoutError('读取预览快照超时，请稍后重试')
    try:
        db.backup(copy,pages=64,progress=progress,sleep=0.05)
        copy.execute('PRAGMA foreign_keys=ON')
        base=fingerprint(copy)
        preview=simulate(copy,rows)
    finally: copy.close()
    with savepoint(db):
        db.execute('UPDATE schema_migrations SET version=version WHERE version=1')
        if fingerprint(db)!=base: raise ValueError('岗位数据已变化，请重新生成预览')
        ident=db.execute('INSERT INTO batch_imports(format,rows_json,rows_hash,base_hash,preview_json,preview_hash) VALUES(?,?,?,?,?,?)',
                         (format,json.dumps(rows,ensure_ascii=False),digest(rows),base,json.dumps(preview,ensure_ascii=False),digest(preview))).lastrowid
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('batch_preview_created',ident))
    return ident


def get_batch(db, ident):
    row=db.execute('SELECT * FROM batch_imports WHERE id=?',(ident,)).fetchone()
    if row is None: raise ValueError('批量导入记录不存在')
    rows=json.loads(row['rows_json']); preview=json.loads(row['preview_json'])
    intact=digest(rows)==row['rows_hash'] and digest(preview)==row['preview_hash']
    stale=row['state']=='obsolete' or (row['state']=='preview' and row['base_hash']!=fingerprint(db)) or not intact
    if row['state']=='preview' and stale:
        with savepoint(db): db.execute("UPDATE batch_imports SET state='obsolete' WHERE id=?",(ident,))
    state=db.execute('SELECT state FROM batch_imports WHERE id=?',(ident,)).fetchone()[0]
    return {**dict(row),'state':state,'rows':rows,'preview':preview,'stale':stale,'integrity_ok':intact}


def commit_batch(db, ident, *, expected_hash, confirmed=False):
    row=get_batch(db,ident)
    if confirmed is not True or row['state']!='preview' or row['stale'] or row['preview_hash']!=expected_hash:
        raise ValueError('请确认当前有效预览；已提交或已过期的批次不能重复导入')
    rows=parse_rows(json.dumps(row['rows'],ensure_ascii=False),'json')
    with savepoint(db):
        db.execute('UPDATE batch_imports SET id=id WHERE id=?',(ident,))
        current=get_batch(db,ident)
        if current['state']!='preview' or current['stale']: raise ValueError('岗位或批次状态已变化')
        result=simulate(db,rows)
        if digest(result)!=row['preview_hash']: raise ValueError('实际导入效果与预览不一致，已取消全部岗位变更')
        db.execute("UPDATE batch_imports SET state='committed',result_json=?,committed_at=? WHERE id=?",(json.dumps(result,ensure_ascii=False),datetime.now(timezone.utc).isoformat(),ident))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('batch_import_committed',ident))
    return result


def csv_template():
    output=io.StringIO(newline=''); writer=csv.writer(output)
    writer.writerow(['company','title','location','source','url','jd','application_url','requisition_id'])
    return '\ufeff'+output.getvalue()
