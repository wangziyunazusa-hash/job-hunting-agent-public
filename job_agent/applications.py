"""Local application packets and user-reported tracking. No browser/network writes."""
import json
from datetime import datetime, timezone
from .core import savepoint, safe_url
from .resumes import digest, validate_version, context, export_version

FIELDS={'full_name':'姓名','email':'电子邮箱','phone':'联系电话','experience':'经历/自我介绍',
        'education':'学历','language':'语言能力'}
CHECKS=('entry_verified','resume_reviewed','fields_reviewed','manual_questions_reviewed')


def application_entry(db, job_id):
    row=db.execute("""SELECT l.id,l.source,l.application_url FROM job_source_links l
        WHERE l.job_id=? AND l.application_url IS NOT NULL AND l.application_url<>''
        AND (NOT EXISTS(SELECT 1 FROM source_postings p WHERE p.source_link_id=l.id)
             OR EXISTS(SELECT 1 FROM source_postings p WHERE p.source_link_id=l.id AND p.active=1))
        ORDER BY CASE l.source WHEN 'official' THEN 0 ELSE 1 END,l.last_observed_at DESC,l.id DESC LIMIT 1""",(job_id,)).fetchone()
    if row is None: return None
    return {'source_link_id':row['id'],'source':row['source'],'url':safe_url(row['application_url'])}


def approved(db, ident):
    resume=validate_version(db,ident)
    if resume['status']!='approved': raise ValueError('需要当前有效且已批准的简历版本')
    return resume


def build_fields(db, language, specs):
    if not isinstance(specs,list) or len(specs)>len(FIELDS): raise ValueError('字段格式错误')
    fields=[]; keys=set()
    for spec in specs:
        if not isinstance(spec,dict) or set(spec)!={'field','fact_ids'}: raise ValueError('字段只能包含类型和事实引用')
        key=spec['field']; ids=spec['fact_ids']
        if not isinstance(key,str) or key not in FIELDS or key in keys or not isinstance(ids,list) or not ids or any(type(i) is not int or i<=0 for i in ids) or len(ids)!=len(set(ids)):
            raise ValueError('未知、重复或缺少事实引用的字段')
        keys.add(key); quotes=[]
        for ident in ids:
            fact=db.execute('SELECT * FROM facts WHERE id=?',(ident,)).fetchone()
            if fact is None or fact['confirmed']!=1 or not fact['text_'+language].strip():
                raise ValueError('字段仅能使用该语言的已确认事实原文')
            quotes.append({'fact_id':ident,'revision':fact['revision'],'text':fact['text_'+language],'evidence':fact['evidence']})
        fields.append({'field':key,'label':FIELDS[key],'value':'\n\n'.join(q['text'] for q in quotes),'facts':quotes})
    return fields


def event(db, ident, name, actor, note=''):
    db.execute('INSERT INTO application_events(application_id,event,actor,note) VALUES(?,?,?,?)',(ident,name,actor,note))
    db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',(name,ident))


def create_application(db, resume_id, specs, *, questions=None):
    resume=approved(db,resume_id)
    questions=[] if questions is None else questions
    if not isinstance(questions,list) or len(questions)>50 or any(not isinstance(q,str) or not q.strip() or len(q)>2000 for q in questions):
        raise ValueError('待回答问题应为非空文本列表')
    with savepoint(db):
        db.execute('UPDATE resume_versions SET id=id WHERE id=?',(resume_id,))
        if digest(context(db,resume['job_id']))!=resume['context_hash']: raise ValueError('上下文已变化')
        payload={'job_id':resume['job_id'],'resume_id':resume_id,'language':resume['language'],
                 'resume_approval_hash':resume['approval_hash'],'resume_content_hash':resume['content_hash'],
                 'context_hash':resume['context_hash'],'entry':application_entry(db,resume['job_id']),
                 'fields':build_fields(db,resume['language'],specs),'questions':questions,
                 'notice':'仅本地准备，未填写或提交任何网站；字段与实际表单是否对应须用户核对。'}
        ident=db.execute('INSERT INTO applications(job_id,resume_id,payload_json,payload_hash) VALUES(?,?,?,?)',
                         (resume['job_id'],resume_id,json.dumps(payload,ensure_ascii=False),digest(payload))).lastrowid
        event(db,ident,'application_prepared','user')
    return ident


def get_application(db, ident):
    row=db.execute('SELECT * FROM applications WHERE id=?',(ident,)).fetchone()
    if row is None: raise ValueError('申请准备记录不存在')
    return row


def validate_application(db, ident):
    row=get_application(db,ident)
    # Completed tracking is historical; never rewrite a past user-reported submission.
    terminal=row['state'] in ('user_reported_submitted','abandoned')
    valid=True
    try:
        payload=json.loads(row['payload_json'])
        valid=digest(payload)==row['payload_hash'] and payload['job_id']==row['job_id'] and payload['resume_id']==row['resume_id']
        resume=approved(db,row['resume_id'])
        specs=[{'field':f['field'],'fact_ids':[q['fact_id'] for q in f['facts']]} for f in payload['fields']]
        valid=valid and payload['fields']==build_fields(db,resume['language'],specs)
        valid=valid and payload['language']==resume['language'] and payload['context_hash']==resume['context_hash']
        valid=valid and payload['resume_approval_hash']==resume['approval_hash'] and payload['resume_content_hash']==resume['content_hash']
        valid=valid and payload['entry']==application_entry(db,row['job_id'])
        if row['state']=='awaiting_user_submit':
            valid=valid and row['readiness_hash']==digest({'application_id':ident,'payload_hash':row['payload_hash'],'checks':list(CHECKS)})
    except (ValueError,TypeError,KeyError):
        valid=False
        try: payload=json.loads(row['payload_json'])
        except (ValueError,TypeError): payload={}
    if not valid and not terminal and row['state']!='needs_review':
        with savepoint(db):
            db.execute("UPDATE applications SET state='needs_review',readiness_hash=NULL WHERE id=?",(ident,))
            event(db,ident,'application_needs_review','system')
    result=dict(get_application(db,ident)); result['payload']=payload; result['currently_valid']=valid
    return result


def mark_ready(db, ident, *, expected_hash, checks):
    row=validate_application(db,ident)
    if row['state']!='prepared' or not row['currently_valid']: raise ValueError('只能核对当前有效的准备记录；已失效需重新创建')
    if not isinstance(checks,dict) or set(checks)!=set(CHECKS) or any(checks[k] is not True for k in CHECKS):
        raise ValueError('请完成全部人工核对项')
    if expected_hash!=row['payload_hash']: raise ValueError('预览内容不一致')
    if not row['payload'].get('entry'): raise ValueError('没有已登记申请链接，请先补充来源信息并重新批准简历')
    if row['payload']['questions']: raise ValueError('仍有待回答问题，请核实后创建新的准备记录')
    with savepoint(db):
        db.execute('UPDATE applications SET id=id WHERE id=?',(ident,))
        current=validate_application(db,ident)
        if current['state']!='prepared' or not current['currently_valid']: raise ValueError('上下文或状态发生变化')
        binding=digest({'application_id':ident,'payload_hash':row['payload_hash'],'checks':list(CHECKS)})
        db.execute("UPDATE applications SET state='awaiting_user_submit',readiness_hash=? WHERE id=?",(binding,ident))
        event(db,ident,'application_ready','user','仅完成本地核对；未操作网站')
    return binding


def report_submitted(db, ident, *, confirmed=False, note=''):
    row=validate_application(db,ident)
    if row['state']!='awaiting_user_submit' or not row['currently_valid'] or not confirmed:
        raise ValueError('需要有效的待用户提交记录，并明确确认你已自行在网站提交')
    if not isinstance(note,str) or len(note)>2000: raise ValueError('备注过长')
    with savepoint(db):
        db.execute('UPDATE applications SET id=id WHERE id=?',(ident,))
        current=validate_application(db,ident)
        if current['state']!='awaiting_user_submit' or not current['currently_valid']: raise ValueError('上下文或状态发生变化')
        db.execute("UPDATE applications SET state='user_reported_submitted',user_reported_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),ident))
        event(db,ident,'application_user_reported_submitted','user',note or '用户自行报告，系统未独立验证')


def abandon_application(db, ident, *, confirmed=False):
    row=get_application(db,ident)
    if not confirmed or row['state'] in ('user_reported_submitted','abandoned'): raise ValueError('需要明确确认，历史提交记录不能变更为放弃')
    with savepoint(db):
        db.execute('UPDATE applications SET id=id WHERE id=?',(ident,))
        if get_application(db,ident)['state'] in ('user_reported_submitted','abandoned'): raise ValueError('该记录已结束')
        db.execute("UPDATE applications SET state='abandoned',readiness_hash=NULL WHERE id=?",(ident,))
        event(db,ident,'application_abandoned','user')


def export_application(db, ident):
    row=validate_application(db,ident)
    if row['state']!='awaiting_user_submit' or not row['currently_valid']:
        raise ValueError('只可导出当前有效且已完成本地核对的申请准备清单')
    resume_text,resume_trace=export_version(db,row['resume_id'])
    return json.dumps({'application_id':ident,'payload':row['payload'],'resume_text':resume_text,
                       'resume_evidence':json.loads(resume_trace),'readiness_hash':row['readiness_hash'],
                       'state':row['state'],'submitted_by_system':False},ensure_ascii=False,indent=2)
