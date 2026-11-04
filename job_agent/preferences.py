"""Versioned search preferences and explainable offline ranking, not eligibility scores."""
import json
from .core import norm, contains, batch_match, matching_source, savepoint
from .resumes import digest

LISTS=('titles','locations','keywords','review_keywords')

def validate_preferences(value):
    if not isinstance(value,dict) or set(value)!=set(LISTS): raise ValueError('偏好格式错误')
    clean={}
    for key in LISTS:
        items=value[key]
        if not isinstance(items,list) or len(items)>50 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in items):
            raise ValueError('每类最多 50 个关键词，每项 1–100 字符')
        seen=set(); clean[key]=[]
        for item in items:
            normalized=norm(item)
            if normalized not in seen:
                seen.add(normalized); clean[key].append(item.strip())
    return clean


def save_profile(db, name, preferences):
    if not isinstance(name,str) or not name.strip() or len(name)>100: raise ValueError('请输入 1–100 字符的偏好名称')
    clean=validate_preferences(preferences)
    with savepoint(db):
        ident=db.execute('INSERT INTO search_profiles(name,preferences_json,preferences_hash) VALUES(?,?,?)',
                         (name.strip(),json.dumps(clean,ensure_ascii=False),digest(clean))).lastrowid
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('profile_saved',ident))
    return ident


def get_profile(db, ident):
    row=db.execute('SELECT * FROM search_profiles WHERE id=?',(ident,)).fetchone()
    if row is None: raise ValueError('偏好版本不存在')
    prefs=validate_preferences(json.loads(row['preferences_json']))
    if digest(prefs)!=row['preferences_hash']: raise ValueError('偏好版本内容与摘要不一致')
    return dict(row),prefs


def hits(text, terms):
    text=norm(text)
    return [term for term in terms if contains(text,norm(term))]


def explain_preferences(job, jd, prefs):
    dimensions=[]; flags=[]
    for key,column,label in [('titles','title','职位关键词'),('locations','location','地点关键词')]:
        if prefs[key]:
            text=job[column] or ''
            matched=hits(text,prefs[key])
            status='命中' if matched else ('未知' if norm(text) in ('','未注明','unknown','not specified') else '未命中关键词')
            dimensions.append({'dimension':key,'label':label,'status':status,'matched':matched,'observed':text})
    for term in prefs['keywords']:
        matched=hits(jd,[term])
        dimensions.append({'dimension':'keyword','label':term,'status':'命中' if matched else '未命中关键词','matched':matched})
    review=hits(job['title']+' '+job['location']+' '+jd,prefs['review_keywords'])
    if review: flags.append({'type':'review_keyword','terms':review,'reason':'发现需复核词，仅词面命中，可能存在否定或上下文差异'})
    known=[d for d in dimensions if d['status']!='未知']
    score=round(sum(d['status']=='命中' for d in known)/len(known)*100,1) if known else None
    return {'preference_coverage':score,'preference_known_dimensions':len(known),
            'preference_total_dimensions':len(dimensions),'dimensions':dimensions,'review_flags':flags,
            'notice':'偏好命中仅用于排序，关键词未命中不等于不适合；地点别名、远程范围与否定句需核对。'}


def context_hash(db):
    with savepoint(db):
        snapshot={
            'jobs':[dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id')],
            'facts':[dict(r) for r in db.execute('SELECT * FROM facts ORDER BY id')],
            'sources':[dict(r) for r in db.execute('SELECT l.*,s.content,s.content_hash FROM job_source_links l LEFT JOIN jd_snapshots s ON s.id=l.latest_snapshot_id ORDER BY l.id')],
            'availability':[dict(r) for r in db.execute('SELECT * FROM source_postings ORDER BY permission_id,external_id')],
        }
        return digest(snapshot)


def rank_jobs(db, profile_id):
    row,prefs=get_profile(db,profile_id)
    # One consistent local snapshot for matching, explanation and stored context.
    with savepoint(db):
        db.execute('UPDATE search_profiles SET id=id WHERE id=?',(profile_id,))
        profile,prefs=get_profile(db,profile_id)
        matches=batch_match(db)
        ranked=[]
        for match in matches:
            job=db.execute('SELECT * FROM jobs WHERE id=?',(match['job_id'],)).fetchone()
            source=matching_source(db,job['id'])
            ranked.append({**match,'location':job['location'],**explain_preferences(dict(job),source['content'],prefs)})
        ranked.sort(key=lambda r:(bool(r['review_flags']),r['preference_coverage'] is None,-(r['preference_coverage'] or 0),r['coverage'] is None,-(r['coverage'] or 0),r['job_id']))
        report={'profile_id':profile_id,'profile_name':profile['name'],'preferences_hash':profile['preferences_hash'],
                'algorithm':'preference-keywords-v1','preferences':prefs,'jobs':ranked,
                'notice':'仅本地排序，无录用概率、资格判定、联网搜索或自动投递。需复核词命中的岗位后排展示，不自动删除。'}
        ident=db.execute('INSERT INTO ranking_runs(profile_id,context_hash,report_json,report_hash) VALUES(?,?,?,?)',
                         (profile_id,context_hash(db),json.dumps(report,ensure_ascii=False),digest(report))).lastrowid
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('jobs_ranked',ident))
    return ident,report


def get_ranking(db, ident):
    row=db.execute('SELECT * FROM ranking_runs WHERE id=?',(ident,)).fetchone()
    if row is None: raise ValueError('排序记录不存在')
    profile,prefs=get_profile(db,row['profile_id'])
    report=json.loads(row['report_json'])
    return {'id':ident,'created_at':row['created_at'],'report':report,
            'integrity_ok':digest(report)==row['report_hash'],
            'stale':row['context_hash']!=context_hash(db) or report['preferences_hash']!=profile['preferences_hash'] or digest(report)!=row['report_hash']}


def filter_ranked(report, *, min_coverage=0, hide_review_flags=False):
    if type(min_coverage) not in (int,float) or not 0<=min_coverage<=100: raise ValueError('覆盖率筛选范围为 0–100')
    if type(hide_review_flags) is not bool: raise ValueError('隐藏复核词选项必须是明确布尔值')
    shown=[]; hidden=[]
    for job in report['jobs']:
        reason=[]
        if hide_review_flags and job['review_flags']: reason.append('用户选择隐藏需复核词命中岗位')
        # Unknown coverage is retained, never interpreted as failure.
        if job['coverage'] is not None and job['coverage']<min_coverage: reason.append('已知事实词项覆盖低于用户设定阈值')
        if reason: hidden.append({'job_id':job['job_id'],'reasons':reason})
        else: shown.append(job)
    return {'shown':shown,'hidden':hidden,'unknown_retained':sum(j['coverage'] is None for j in shown)}
