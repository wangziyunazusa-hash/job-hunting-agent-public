"""Immutable, extractive bilingual resume drafts. No model or automatic translation."""
import hashlib
import json
from datetime import datetime, timezone
from .core import savepoint, matching_source
from .reviews import active_job


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def context(db, job_id):
    job=active_job(db,job_id)
    if job['status'] in ('expired','rejected'): raise ValueError('岗位已失效或被拒绝，不能批准简历')
    source=matching_source(db,job_id)
    if source is None: raise ValueError('岗位缺少 JD')
    # Conservative invalidation: any fact-bank edit or source observation requires fresh review.
    facts=[dict(r) for r in db.execute('SELECT * FROM facts ORDER BY id')]
    links=[dict(r) for r in db.execute('SELECT * FROM job_source_links WHERE job_id=? ORDER BY id',(job_id,))]
    availability=[dict(r) for r in db.execute('SELECT p.* FROM source_postings p JOIN job_source_links l ON l.id=p.source_link_id WHERE l.job_id=? ORDER BY p.permission_id,p.external_id',(job_id,))]
    return {'job':{k:job[k] for k in ('id','company','title','location','requisition_id')},
            'source_policy':'linkedin_openwork_first','source_link_id':source['id'],
            'source_url':source['url'],'jd_hash':hashlib.sha256(source['content'].encode()).hexdigest(),
            'facts':facts,'links':links,'availability':availability}


def render(language, job, claims):
    title='職務経歴・実績（確認済み事実）' if language=='ja' else 'Experience and achievements (verified facts)'
    return title+'\n\n'+'\n\n'.join(c['original_text'] for c in claims)+'\n'


def create_draft(db, job_id, language, fact_ids):
    if language not in ('ja','en'): raise ValueError('只支持日语和英语')
    if not fact_ids or len(fact_ids)!=len(set(fact_ids)): raise ValueError('至少选择一条事实，不可重复')
    with savepoint(db):
        snapshot=context(db,job_id)
        facts={r['id']:r for r in snapshot['facts']}
        claims=[]
        for ident in fact_ids:
            fact=facts.get(ident)
            if fact is None or fact['confirmed']!=1: raise ValueError('仅可引用已确认事实')
            text=fact['text_'+language]
            if not text.strip(): raise ValueError('所选事实缺少该语言原文，请先补充并确认；不会自动翻译')
            claims.append({'fact_id':ident,'original_text':text,'evidence':fact['evidence']})
        body=render(language,snapshot['job'],claims)
        ident=db.execute('INSERT INTO resume_versions(job_id,language,content,content_hash,context_hash,context_json) VALUES(?,?,?,?,?,?)',
                         (job_id,language,body,digest(body),digest(snapshot),json.dumps(snapshot,ensure_ascii=False))).lastrowid
        for index,claim in enumerate(claims):
            db.execute('INSERT INTO resume_claims VALUES(?,?,?,?,?)',(ident,index,claim['fact_id'],claim['original_text'],claim['evidence']))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('resume_drafted',ident))
    return ident


def get_version(db, ident):
    version=db.execute('SELECT * FROM resume_versions WHERE id=?',(ident,)).fetchone()
    if version is None: raise ValueError('简历版本不存在')
    return version


def validate_version(db, ident):
    row=get_version(db,ident)
    claims=[dict(r) for r in db.execute('SELECT * FROM resume_claims WHERE resume_id=? ORDER BY position',(ident,))]
    try:
        snapshot=context(db,row['job_id'])
        fresh=digest(snapshot)==row['context_hash']
        original=json.loads(row['context_json'])
        fresh=fresh and digest(original)==row['context_hash']
        fresh=fresh and digest(row['content'])==row['content_hash']
        fresh=fresh and row['content']==render(row['language'],original['job'],claims)
        facts={f['id']:f for f in original['facts']}
        fresh=fresh and bool(claims) and all(c['fact_id'] in facts and facts[c['fact_id']]['confirmed']==1 and c['original_text']==facts[c['fact_id']]['text_'+row['language']] and c['evidence']==facts[c['fact_id']]['evidence'] for c in claims)
        binding=digest({'version_id':ident,'content':row['content_hash'],'context':row['context_hash']})
        if row['status']=='approved': fresh=fresh and row['approval_hash']==binding
    except (ValueError,KeyError,TypeError): fresh=False
    if not fresh and row['status']!='obsolete':
        with savepoint(db):
            db.execute("UPDATE resume_versions SET status='obsolete',approval_hash=NULL WHERE id=?",(ident,))
            db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('resume_obsolete',ident))
    row=dict(get_version(db,ident)); row['claims']=claims
    return row


def approve_version(db, ident, *, expected_hash, confirmed=False):
    # Validate first so invalidation persists even when approval is refused.
    row=validate_version(db,ident)
    if not confirmed: raise ValueError('需要用户确认已审阅全文及事实依据')
    if row['status']=='obsolete': raise ValueError('JD、事实或来源已变化，请重新生成并审阅')
    if expected_hash!=row['content_hash']: raise ValueError('批准内容与预览不一致')
    with savepoint(db):
        # Recheck under the same write transaction as approval.
        db.execute('UPDATE resume_versions SET id=id WHERE id=?',(ident,))
        if digest(context(db,row['job_id']))!=row['context_hash']: raise ValueError('上下文已变化，请重新审阅')
        binding=digest({'version_id':ident,'content':row['content_hash'],'context':row['context_hash']})
        db.execute("UPDATE resume_versions SET status='approved',approval_hash=?,approved_at=? WHERE id=?",(binding,datetime.now(timezone.utc).isoformat(),ident))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('resume_approved',ident))
    return binding


def export_version(db, ident):
    row=validate_version(db,ident)
    if row['status']!='approved': raise ValueError('只有当前有效且已审阅批准的版本可以导出')
    trace={'version_id':ident,'job_id':row['job_id'],'language':row['language'],
           'content_hash':row['content_hash'],'approval_hash':row['approval_hash'],
           'context_hash':row['context_hash'],'source':json.loads(row['context_json'])['source_url'],
           'claims':row['claims']}
    return row['content'],json.dumps(trace,ensure_ascii=False,indent=2)


def recommend_facts(db, job_id, language):
    """Rank confirmed facts by explicit JD term overlap; never manufacture text."""
    from .core import TERMS, contains, norm
    if language not in ('ja','en'): raise ValueError('只支持日语和英语')
    snapshot=context(db,job_id)
    jd=norm(matching_source(db,job_id)['content'])
    required={label:aliases for label,aliases in TERMS.items() if any(contains(jd,a) for a in aliases)}
    result=[]
    for fact in snapshot['facts']:
        if fact['confirmed']!=1 or not fact['text_'+language].strip(): continue
        text=norm(fact['text_ja']+' '+fact['text_en'])
        terms=[label for label,aliases in required.items() if any(contains(text,a) for a in aliases)]
        if terms: result.append({'fact_id':fact['id'],'matched_terms':terms,'score':len(terms),'text':fact['text_'+language]})
    return sorted(result,key=lambda f:(-f['score'],f['fact_id']))


def create_matched_draft(db, job_id, language):
    ranked=recommend_facts(db,job_id,language)
    if not ranked: raise ValueError('没有可用的已确认词项匹配事实，请补充事实或手动选择；不会生成经历')
    return create_draft(db,job_id,language,[r['fact_id'] for r in ranked])
