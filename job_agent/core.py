from datetime import datetime, timezone
from contextlib import contextmanager
import uuid
import hashlib
import ipaddress
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parent.parent

def connect(path=None):
    path = Path(path) if path else ROOT / 'data/jobs.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript((ROOT / 'migrations/001_initial.sql').read_text())
    for migration in sorted((ROOT / 'migrations').glob('*.sql')):
        version = int(migration.name.split('_')[0])
        if not db.execute('SELECT 1 FROM schema_migrations WHERE version=?',(version,)).fetchone():
            db.executescript(migration.read_text())
    return db

@contextmanager
def savepoint(db):
    name = 'sp_' + uuid.uuid4().hex
    db.execute('SAVEPOINT '+name)
    try:
        yield
        db.execute('RELEASE SAVEPOINT '+name)
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT '+name)
        db.execute('RELEASE SAVEPOINT '+name)
        raise

def norm(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())

def safe_url(value):
    u = urlsplit(value.strip())
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None,443):
        raise ValueError('仅接受无凭证的 HTTPS 链接')
    host = u.hostname.lower()
    if host == 'localhost' or '.' not in host or host.endswith(('.local','.internal')):
        raise ValueError('不允许本地地址')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not ip.is_global:
            raise ValueError('不允许私有地址')
    query = [(k,v) for k,v in parse_qsl(u.query,keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('trk','trackingid')]
    return urlunsplit(('https',host,u.path or '/',urlencode(sorted(query)),''))

def check_source(source, url):
    if source not in ('official','linkedin','openwork'):
        raise ValueError('未知来源')
    host = urlsplit(url).hostname
    for domain, expected in [('linkedin.com','linkedin'),('openwork.jp','openwork')]:
        if host == domain or host.endswith('.'+domain):
            if source != expected:
                raise ValueError('来源标签与平台域名不符')
    if source in ('linkedin','openwork'):
        domain = {'linkedin':'linkedin.com','openwork':'openwork.jp'}[source]
        if host != domain and not host.endswith('.'+domain):
            raise ValueError('来源链接必须属于所选平台')

def import_job(db, *, company, title, location, source, url, jd, application_url='', requisition_id='', method='manual'):
    if not all(x.strip() for x in (company,title,location,jd)) or len(jd)>200000:
        raise ValueError('公司、岗位、地点和 JD 必填，JD 上限 20 万字符')
    if method not in ('manual','api') or (method == 'api' and source != 'official'):
        raise ValueError('不允许的导入方式')
    url = safe_url(url)
    check_source(source,url)
    app = safe_url(application_url) if application_url.strip() else None
    with savepoint(db):
        link = db.execute('SELECT * FROM job_source_links WHERE url=?',(url,)).fetchone()
        job_id = link['job_id'] if link else None
        if not job_id:
            # Company/title/location similarity alone never causes an automatic merge.
            candidates = db.execute('SELECT * FROM jobs').fetchall()
            for candidate in candidates:
                if norm(candidate['company']) != norm(company):
                    continue
                same_req = requisition_id.strip() and candidate['requisition_id'] == requisition_id.strip()
                same_app = app and db.execute('SELECT 1 FROM job_source_links WHERE job_id=? AND application_url=?',(candidate['id'],app)).fetchone()
                if same_req or same_app:
                    job_id = candidate['id']
                    break
        if not job_id:
            job_id = db.execute('INSERT INTO jobs(company,title,location,requisition_id) VALUES(?,?,?,?)',
                                (company.strip(),title.strip(),location.strip(),requisition_id.strip() or None)).lastrowid
        if not link:
            link_id = db.execute('INSERT INTO job_source_links(job_id,source,url,application_url,method) VALUES(?,?,?,?,?)',
                                 (job_id,source,url,app,method)).lastrowid
        else:
            link_id = link['id']
            if app:
                db.execute('UPDATE job_source_links SET application_url=? WHERE id=?',(app,link_id))
        digest = hashlib.sha256(jd.encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO jd_snapshots(source_link_id,content,content_hash) VALUES(?,?,?)',(link_id,jd,digest))
        snapshot_id=db.execute('SELECT id FROM jd_snapshots WHERE source_link_id=? AND content_hash=?',(link_id,digest)).fetchone()[0]
        db.execute('UPDATE job_source_links SET latest_snapshot_id=?,last_observed_at=? WHERE id=?',(snapshot_id,datetime.now(timezone.utc).isoformat(),link_id))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('manual_import' if method == 'manual' else 'api_import',job_id))
    return job_id

def preferred(db, job_id):
    return db.execute("""SELECT l.*, s.content, s.content_hash FROM job_source_links l
        JOIN jd_snapshots s ON s.id=l.latest_snapshot_id WHERE l.job_id=?
        ORDER BY CASE l.source WHEN 'official' THEN 0 ELSE 1 END, l.last_observed_at DESC, s.id DESC LIMIT 1""",(job_id,)).fetchone()

def possible_duplicates(db, job_id):
    job = db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE id<>?',(job_id,))
            if all(norm(r[k])==norm(job[k]) for k in ('company','title','location'))]

def add_fact(db, category, text_ja, text_en, evidence, confirmed=False):
    if not category.strip() or not evidence.strip() or not (text_ja.strip() or text_en.strip()):
        raise ValueError('事实内容、类别和证据必填')
    with db:
        fact_id = db.execute('INSERT INTO facts(category,text_ja,text_en,evidence,confirmed) VALUES(?,?,?,?,?)',
                            (category,text_ja,text_en,evidence,int(confirmed))).lastrowid
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('fact_confirmed' if confirmed else 'fact_added',fact_id))
    return fact_id

# Explicit vocabulary avoids claiming semantic equivalence or Japanese proficiency.
TERMS = {'python':('python',), 'sql':('sql',), '英語':('英語','english'),
         '日本語':('日本語','japanese'), 'プロジェクト管理':('プロジェクト管理','project management'),
         'データ分析':('データ分析','data analysis'), '製品管理':('製品管理','プロダクトマネジメント','product management')}

def contains(text, term):
    if term.isascii():
        return bool(re.search(r'(?<![a-z0-9_])'+re.escape(term)+r'(?![a-z0-9_])',text))
    return term in text

def matching_source(db, job_id):
    """LinkedIn/OpenWork first for matching; official canonical preference is unchanged."""
    return db.execute("""SELECT l.*, s.content, s.content_hash FROM job_source_links l
        JOIN jd_snapshots s ON s.id=l.latest_snapshot_id WHERE l.job_id=?
        ORDER BY CASE l.source WHEN 'official' THEN 1 ELSE 0 END,
        l.last_observed_at DESC, s.id DESC, l.id DESC LIMIT 1""",(job_id,)).fetchone()

def match_job(db, job_id):
    row = matching_source(db, job_id)
    if row is None:
        raise ValueError('岗位不存在')
    jd = norm(row['content'])
    facts = db.execute('SELECT * FROM facts WHERE confirmed=1').fetchall()
    requirements=[]
    for label, aliases in TERMS.items():
        if any(contains(jd,a) for a in aliases):
            ids=[f['id'] for f in facts if any(contains(norm(f['text_ja']+' '+f['text_en']),a) for a in aliases)]
            requirements.append({'term':label,'fact_ids':ids,'status':'有事实提及，需人工核对' if ids else '未知/缺少已确认事实'})
    hits=sum(bool(r['fact_ids']) for r in requirements)
    report={'algorithm':'keyword-coverage-v1','source_policy':'linkedin_openwork_first','source':row['source'],'source_url':row['url'],'source_link_id':row['id'],'jd_hash':row['content_hash'],
            'coverage':round(hits/len(requirements)*100,1) if requirements else None,
            'requirements':requirements,'limitations':'仅词项覆盖，非录用概率；否定、等级、年限、签证、薪资及未识别条件均需人工核对。',
            'confirmed_fact_count':len(facts)}
    with db:
        db.execute('INSERT INTO match_reports(job_id,report_json) VALUES(?,?)',(job_id,json.dumps(report,ensure_ascii=False)))
        db.execute("UPDATE jobs SET status='matched' WHERE id=? AND status IN ('imported','matched')",(job_id,))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('match',job_id))
    return report

def authorize(action, source='official'):
    if action != 'manual_import':
        raise PermissionError('Phase 1 仅允许手动导入；网络、填写、提交均禁用')
    if source not in ('official','linkedin','openwork'):
        raise ValueError('未知来源')


def batch_match(db):
    """Offline, explicit batch action. No source fetching or scheduling."""
    result=[]
    for row in db.execute("SELECT * FROM jobs WHERE status IN ('imported','matched') ORDER BY id").fetchall():
        report=match_job(db,row['id'])
        result.append({'job_id':row['id'],'company':row['company'],'title':row['title'],**report})
    return sorted(result,key=lambda r:(r['coverage'] is not None,r['coverage'] or 0),reverse=True)
