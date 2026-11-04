import json
import streamlit as st
from job_agent.core import connect, import_job, add_fact, match_job, preferred, possible_duplicates, batch_match, matching_source
st.set_page_config(page_title='求职工作台', layout='wide')
st.title('求职工作台 · 匹配与申请准备')
st.info('默认本地运行；官方 API 读取需独立授权后手动触发。可生成已确认事实原文草稿；不调用模型、不填写、不投递。LinkedIn/OpenWork 仅手动导入。')
db = connect()
a,b,c = st.tabs(['导入岗位','事实库','岗位与匹配'])
with a:
    with st.form('import'):
        company=st.text_input('公司'); title=st.text_input('岗位名称'); location=st.text_input('地点')
        source=st.selectbox('来源',['official','linkedin','openwork'])
        url=st.text_input('来源 HTTPS 链接'); application=st.text_input('申请链接（可选）')
        req=st.text_input('企业岗位编号（已核对才填写）')
        jd=st.text_area('手动粘贴 JD',height=240)
        if st.form_submit_button('保存岗位'):
            try:
                ident=import_job(db,company=company,title=title,location=location,source=source,url=url,jd=jd,application_url=application,requisition_id=req)
                st.success(f'已保存岗位 #{ident}')
            except ValueError as e: st.error(str(e))
with b:
    st.caption('证据可填用户原始材料的本地路径和页码；程序不会读取该路径。勾选确认代表你已核实。')
    with st.form('fact'):
        category=st.text_input('类别（技能/经历/学历等）')
        ja=st.text_area('日语事实原文'); en=st.text_area('英语事实原文')
        evidence=st.text_input('证据位置或来源说明'); confirmed=st.checkbox('我已核实此事实')
        if st.form_submit_button('保存事实'):
            try: add_fact(db,category,ja,en,evidence,confirmed); st.success('事实已保存')
            except ValueError as e: st.error(str(e))
    st.dataframe([dict(r) for r in db.execute('SELECT * FROM facts')])
with c:
    jobs=[dict(r) for r in db.execute('SELECT * FROM jobs WHERE merged_into IS NULL ORDER BY id DESC')]
    if not jobs: st.write('尚无岗位，请先导入。')
    else:
        labels={f"#{j['id']} {j['company']} · {j['title']}":j['id'] for j in jobs}
        selected=st.selectbox('选择岗位',list(labels))
        ident=labels[selected]; chosen=preferred(db,ident)
        st.write('官方核验优先来源：',chosen['source']); st.code(chosen['url'])
        match_source=matching_source(db,ident)
        st.write('本次匹配 JD 来源（LinkedIn/OpenWork 优先）：',match_source['source'])
        st.code(match_source['url'])
        st.write('该来源申请链接：')
        st.code(chosen['application_url'] or '未提供；请人工核对官方申请入口')
        st.text(match_source['content'])
        st.write('来源溯源'); st.dataframe([dict(r) for r in db.execute('SELECT * FROM job_source_links WHERE job_id=?',(ident,))])
        candidates=possible_duplicates(db,ident)
        if candidates: st.warning('存在名称和地点相同的岗位，请人工核对，未自动合并。'); st.dataframe(candidates)
        if st.button('计算事实词项覆盖'):
            st.json(match_job(db,ident))
with st.expander('自动采集与匹配（仅已授权官方 API）'):
    st.caption('点击后依次读取已授权 board、去重、保存快照并匹配；没有授权时只匹配本地数据。不会抓取 LinkedIn/OpenWork，不会在后台定时执行。')
    pipeline_profiles={'仅事实词项覆盖（不使用偏好）':None,**{f"#{r['id']} {r['name']}":r['id'] for r in db.execute('SELECT * FROM search_profiles ORDER BY id DESC')}}
    pipeline_profile=st.selectbox('采集后使用的排序偏好',list(pipeline_profiles))
    if st.button('采集已授权来源并匹配'):
        from job_agent.pipeline import collect_and_match
        with st.spinner('采集与匹配中…'):
            st.json(collect_and_match(db,profile_id=pipeline_profiles[pipeline_profile]))
with st.expander('本地批量匹配'):

    st.caption('只使用已导入 JD 和已确认事实；不访问网站，不进行定时后台运行。')
    if st.button('匹配全部有效岗位'):
        st.json(batch_match(db))
with st.expander('官方 API 授权与刷新（未配置具体公司）'):
    from job_agent.sources import register_permission, revoke_permission, sync_board
    st.caption('仅 Greenhouse、Lever、Ashby 的固定只读接口。授权记录需说明合法读取依据及企业官网关联，勾选不代表系统验证法律授权。')
    with st.form('source_permission'):
        provider=st.selectbox('API 类型',['greenhouse','lever','lever_eu','ashby'])
        board=st.text_input('Board 标识'); employer=st.text_input('所属公司')
        official=st.text_input('企业官方招聘页 HTTPS 链接')
        evidence=st.text_area('读取权限及官方关联证据（请勿填写凭证）')
        expires=st.text_input('授权失效时间（含时区）',placeholder='2026-12-31T23:59:59+09:00')
        verified=st.checkbox('我已核实官方关联及该接口的读取权限')
        if st.form_submit_button('登记只读授权'):
            try:
                register_permission(db,provider=provider,board=board,company=employer,evidence=evidence,official_url=official,expires_at=expires,confirmed=verified)
                st.success('授权已登记，尚未访问任何接口')
            except (ValueError,PermissionError) as e: st.error(str(e))
    for permission in db.execute('SELECT * FROM source_permissions ORDER BY id DESC').fetchall():
        st.write(f"#{permission['id']} {permission['company']} / {permission['provider']} / {permission['board']}")
        st.text('失效时间：'+permission['expires_at'])
        if permission['revoked_at']: st.caption('已撤销'); continue
        if st.button('读取此授权范围的岗位',key=f"sync_{permission['id']}"):
            try:
                with st.spinner('读取官方 API…'):
                    st.json(sync_board(db,permission['id']))
                st.info('读取完成，重新打开岗位页可查看。')
            except Exception as e: st.error(f'刷新未完成：{type(e).__name__}。请检查权限、网络和接口格式；岗位整批更新已取消。')
        if st.button('撤销读取权限',key=f"revoke_{permission['id']}"):
            revoke_permission(db,permission['id']); st.rerun()
    st.write('刷新记录')
    st.dataframe([dict(r) for r in db.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 50')])
    st.write('API 来源可用状态（缺席仅代表该来源暂未列出，不代表岗位全局失效）')
    st.dataframe([dict(r) for r in db.execute('SELECT * FROM source_postings')])
with st.expander('人工核对跨来源岗位'):
    from job_agent.reviews import merge_jobs, mark_distinct, undo_merge
    st.caption('先比较 JD、岗位编号、地点和申请入口；跨语言标题或公司别名也可手动核对。合并只调整来源分组，保留原始岗位、快照和历史报告，可撤销。')
    options={f"#{r['id']} {r['company']} · {r['title']} · {r['location']}":r['id'] for r in db.execute('SELECT * FROM jobs WHERE merged_into IS NULL ORDER BY id')}
    if len(options)>=2:
        with st.container():
            left_label=st.selectbox('待核对岗位',list(options))
            right_label=st.selectbox('保留的主岗位',list(options),index=1)
            decision=st.radio('核对结论',['同一岗位：合并来源','不同岗位：不再提示重复'])
            note=st.text_area('核对依据')
            confirm=st.checkbox('我已核实这两个岗位的关系',key=f"confirm_pair_{options[left_label]}_{options[right_label]}")
            # Each link opens no website; display only stored JD text.
            for label in (left_label,right_label):
                st.write(label)
                for row in db.execute('SELECT l.url,s.content FROM job_source_links l JOIN jd_snapshots s ON s.id=l.latest_snapshot_id WHERE l.job_id=?',(options[label],)):
                    st.code(row['url']); st.text(row['content'])
            if st.button('保存核对结论'):
                try:
                    if decision.startswith('同一岗位'):
                        merge_jobs(db,options[left_label],options[right_label],note,confirmed=confirm)
                    else: mark_distinct(db,options[left_label],options[right_label],note,confirmed=confirm)
                    st.rerun()
                except ValueError as e: st.error(str(e))
    for row in db.execute('SELECT * FROM job_merges WHERE undone_at IS NULL ORDER BY id DESC').fetchall():
        st.write(f"合并 #{row['id']}：岗位 #{row['source_job_id']} → #{row['target_job_id']}")
        st.text(row['reason'])
        if st.button('撤销这次合并',key=f"undo_{row['id']}"):
            try: undo_merge(db,row['id']); st.rerun()
            except ValueError as e: st.error(str(e))
with st.expander('日英简历草稿与审阅'):
    from job_agent.resumes import create_draft, validate_version, approve_version, export_version, recommend_facts, create_matched_draft
    st.caption('只按你选择的顺序组装已确认的原文，不自动翻译或润色。每条内容保留事实 ID 和证据；不是完整的履歴書排版。')
    resume_jobs={f"#{r['id']} {r['company']} · {r['title']}":r['id'] for r in db.execute("SELECT * FROM jobs WHERE merged_into IS NULL AND status IN ('imported','matched') ORDER BY id")}
    if resume_jobs:
        resume_job=st.selectbox('简历目标岗位',list(resume_jobs))
        resume_lang=st.selectbox('简历语言',['ja','en'])
        available={f"#{r['id']} {r['category']} · {r['text_'+resume_lang][:60]}":r['id'] for r in db.execute('SELECT * FROM facts WHERE confirmed=1 ORDER BY id') if r['text_'+resume_lang].strip()}
        chosen_facts=st.multiselect('按希望呈现的顺序选择事实',list(available))
        if st.button('生成待审阅草稿'):
            try:
                create_draft(db,resume_jobs[resume_job],resume_lang,[available[x] for x in chosen_facts])
                st.success('草稿已生成，请审阅全文和每条证据')
            except ValueError as e: st.error(str(e))
        suggestions=recommend_facts(db,resume_jobs[resume_job],resume_lang)
        st.caption('规则推荐只按 JD 词项覆盖排列，不代表满足语言等级、年限或必选条件。')
        if suggestions: st.dataframe(suggestions)
        if st.button('按 JD 词项筛选事实并生成草稿'):
            try:
                create_matched_draft(db,resume_jobs[resume_job],resume_lang)
                st.success('已按词项关联组装草稿，尚未批准')
            except ValueError as e: st.error(str(e))
    else: st.write('请先导入岗位，再添加并确认事实。')
    versions=db.execute('SELECT id FROM resume_versions ORDER BY id DESC').fetchall()
    if versions:
        version_id=st.selectbox('查看草稿版本',[r['id'] for r in versions])
        version=validate_version(db,version_id)
        st.write(f"版本 #{version_id} · {version['language']} · {version['status']}")
        st.text(version['content']); st.dataframe(version['claims'])
        if version['status']=='obsolete': st.warning('该版本已失效，请按当前 JD 和事实重新生成。')
        else:
            reviewed=st.checkbox('我已逐条核实事实、语言和证据，并审阅此版本全文',key=f"review_{version_id}")
            if version['status']=='review_required' and st.button('批准此版本',key=f"approve_{version_id}"):
                try:
                    approve_version(db,version_id,expected_hash=version['content_hash'],confirmed=reviewed)
                    st.rerun()
                except ValueError as e: st.error(str(e))
            if version['status']=='approved':
                try:
                    body,trace=export_version(db,version_id)
                    st.download_button('下载已批准文本',body,file_name=f'resume_{version_id}_{version["language"]}.txt',mime='text/plain')
                    st.download_button('下载事实溯源清单',trace,file_name=f'resume_{version_id}_evidence.json',mime='application/json')
                except ValueError as e: st.error(str(e))
with st.expander('确认或撤销事实状态'):
    from job_agent.core import review_fact
    fact_options={f"#{r['id']} {r['category']} · {'已确认' if r['confirmed'] else '待确认'}":r['id'] for r in db.execute('SELECT * FROM facts ORDER BY id')}
    if fact_options:
        fact_label=st.selectbox('待核对事实',list(fact_options))
        fact_row=db.execute('SELECT * FROM facts WHERE id=?',(fact_options[fact_label],)).fetchone()
        st.text(fact_row['text_ja']); st.text(fact_row['text_en']); st.text(fact_row['evidence'])
        review_action=st.radio('事实状态操作',['确认事实','撤销确认'])
        fact_reviewed=st.checkbox('我已核对此条事实和证据',key=f"fact_review_{fact_row['id']}_{fact_row['revision']}")
        if bool(fact_row['confirmed'])!=(review_action=='确认事实') and st.button('保存事实状态'):
            try:
                review_fact(db,fact_row['id'],confirmed=review_action=='确认事实',reviewed=fact_reviewed)
                st.rerun()
            except ValueError as e: st.error(str(e))
    else: st.write('尚无事实。')
with st.expander('申请准备与手动提交记录'):
    from job_agent.applications import FIELDS, CHECKS, create_application, validate_application, mark_ready, report_submitted, abandon_application, export_application
    st.caption('本区只整理本地清单，不访问申请页面、不填写或提交。申请链接由已保存来源提供，官方优先；请自行核实其归属。')
    approved_versions={}
    for candidate in db.execute("SELECT id FROM resume_versions WHERE status='approved' ORDER BY id DESC").fetchall():
        candidate_version=validate_version(db,candidate['id'])
        if candidate_version['status']=='approved':
            approved_versions[f"版本 #{candidate_version['id']} · 岗位 #{candidate_version['job_id']} · {candidate_version['language']}"]=candidate_version
    if approved_versions:
        app_resume_label=st.selectbox('用于申请准备的已批准简历',list(approved_versions))
        app_resume=approved_versions[app_resume_label]
        st.write('本次准备引用的已批准简历原文')
        st.text(app_resume['content'])
        field_facts={f"#{r['id']} {r['text_'+app_resume['language']][:60]}":r['id'] for r in db.execute('SELECT * FROM facts WHERE confirmed=1 ORDER BY id') if r['text_'+app_resume['language']].strip()}
        specs=[]
        st.caption('以下字段可选，值为事实原文。选择前需确认原文适合对应表单字段；系统不推断姓名、联系方式、资格或声明。')
        for field_key,field_label in FIELDS.items():
            selection=st.multiselect(f'{field_label}：引用哪些事实',list(field_facts),key=f"app_field_{app_resume['id']}_{field_key}")
            if selection:
                specs.append({'field':field_key,'fact_ids':[field_facts[x] for x in selection]})
                for label in selection:
                    quoted=db.execute('SELECT * FROM facts WHERE id=?',(field_facts[label],)).fetchone()
                    st.text(quoted['text_'+app_resume['language']])
        questions=st.text_area('尚待你回答的问题（每行一项；核实前不能完成准备）',key=f"app_questions_{app_resume['id']}")
        if st.button('创建本地申请准备清单'):
            try:
                create_application(db,app_resume['id'],specs,questions=[q.strip() for q in questions.splitlines() if q.strip()])
                st.success('清单已创建，尚未操作任何网站')
            except ValueError as e: st.error(str(e))
    else: st.write('请先生成、审阅并批准一份当前有效的简历。')
    app_ids=[r['id'] for r in db.execute('SELECT id FROM applications ORDER BY id DESC')]
    if app_ids:
        app_id=st.selectbox('查看申请准备记录',app_ids)
        application=validate_application(db,app_id)
        state_labels={'prepared':'待核对','awaiting_user_submit':'本地核对完成，等待你自行提交','needs_review':'已失效，需重新准备','user_reported_submitted':'用户自报已提交（未独立验证）','abandoned':'已放弃'}
        app_status_display=st.empty(); app_status_display.write(state_labels[application['state']]); st.json(application['payload'])
        if application['payload'].get('entry'):
            st.write('已登记申请入口（由你自行打开核对）')
            st.code(application['payload']['entry']['url'])
        if not application['currently_valid']:
            st.warning('所关联的简历、事实或来源已变化；历史记录保留，但不能继续导出当前准备清单。')
        if application['state']=='prepared':
            check_labels={'entry_verified':'我已核实申请入口及岗位仍开放','resume_reviewed':'我已核对该简历适用于本次申请','fields_reviewed':'我已逐项核对字段原文，其他字段将自行填写','manual_questions_reviewed':'资格、声明、隐私条款及实际表单问题由我自行阅读和回答'}
            checks={key:st.checkbox(check_labels[key],key=f"app_check_{app_id}_{key}") for key in CHECKS}
            if st.button('完成本地核对，等待我自行提交',key=f"ready_{app_id}"):
                try:
                    mark_ready(db,app_id,expected_hash=application['payload_hash'],checks=checks)
                    application=validate_application(db,app_id)
                    st.success('本地核对完成，尚未提交任何网站')
                except ValueError as e: st.error(str(e))
        if application['state']=='awaiting_user_submit':
            try:
                packet=export_application(db,app_id)
                st.download_button('下载申请准备清单',packet,file_name=f'application_{app_id}.json',mime='application/json')
            except ValueError as e: st.error(str(e))
            reported=st.checkbox('我已自行在网站完成最终提交，仅在此记录结果',key=f"app_submitted_{app_id}")
            reported_note=st.text_input('提交记录备注（可选，请勿填写密码或凭证）',key=f"app_note_{app_id}")
            if st.button('记录我已自行提交',key=f"report_{app_id}"):
                try:
                    report_submitted(db,app_id,confirmed=reported,note=reported_note)
                    application=validate_application(db,app_id)
                    st.success('已记录为用户自报提交，系统未独立验证')
                except ValueError as e: st.error(str(e))
        if application['state'] not in ('user_reported_submitted','abandoned'):
            give_up=st.checkbox('确认放弃此准备记录',key=f"app_abandon_confirm_{app_id}")
            if st.button('放弃此记录',key=f"app_abandon_{app_id}"):
                try:
                    abandon_application(db,app_id,confirmed=give_up)
                    application=validate_application(db,app_id)
                    st.success('已放弃此准备记录')
                except ValueError as e: st.error(str(e))
        app_status_display.write(state_labels[application['state']])
        st.dataframe([dict(r) for r in db.execute('SELECT * FROM application_events WHERE application_id=? ORDER BY id',(app_id,))])
with st.expander('岗位偏好与可解释排序'):
    from job_agent.preferences import save_profile, rank_jobs, get_ranking, filter_ranked
    st.caption('偏好只是你的求职意向，不写入简历事实库。LinkedIn/OpenWork 手动导入 JD 优先；这里只排序本地岗位，不搜索网站。每次保存创建独立偏好版本。')
    with st.form('preferences'):
        profile_name=st.text_input('偏好名称')
        title_terms=st.text_area('目标职位关键词（每行一个，可填写日英别名）')
        location_terms=st.text_area('偏好地点关键词（每行一个，可填写城市别名）')
        desired_terms=st.text_area('希望 JD 出现的关键词（每行一个）')
        review_terms=st.text_area('需要人工复核的关键词（每行一个，不自动排除）')
        if st.form_submit_button('保存偏好版本'):
            try:
                preferences={key:[line.strip() for line in value.splitlines() if line.strip()] for key,value in [('titles',title_terms),('locations',location_terms),('keywords',desired_terms),('review_keywords',review_terms)]}
                save_profile(db,profile_name,preferences)
                st.success('偏好已保存，未修改任何事实或简历')
            except ValueError as e: st.error(str(e))
    profiles={f"#{r['id']} {r['name']}":r['id'] for r in db.execute('SELECT * FROM search_profiles ORDER BY id DESC')}
    if profiles:
        profile_label=st.selectbox('使用哪个偏好版本',list(profiles))
        if st.button('按偏好匹配并排序本地岗位'):
            try:
                rank_jobs(db,profiles[profile_label])
                st.success('已生成可追溯排序快照')
            except ValueError as e: st.error(str(e))
        run_ids=[r['id'] for r in db.execute('SELECT id FROM ranking_runs WHERE profile_id=? ORDER BY id DESC',(profiles[profile_label],))]
        if run_ids:
            rank_id=st.selectbox('查看排序记录',run_ids)
            ranking=get_ranking(db,rank_id)
            if not ranking['integrity_ok']: st.error('排序快照与保存的摘要不一致，请重新计算。')
            elif ranking['stale']: st.warning('岗位、事实或来源已变化；下面是历史排序，请重新计算后再决定。')
            minimum=st.slider('仅筛选已知的事实词项覆盖率下限（未知仍保留）',0,100,0)
            hide_review=st.checkbox('隐藏需人工复核词命中的岗位（默认保留）')
            filtered=filter_ranked(ranking['report'],min_coverage=minimum,hide_review_flags=hide_review)
            st.write(f"显示 {len(filtered['shown'])} 个，隐藏 {len(filtered['hidden'])} 个，保留未知覆盖 {filtered['unknown_retained']} 个")
            st.dataframe([{'岗位编号':j['job_id'],'公司':j['company'],'岗位':j['title'],'地点':j['location'],'偏好词项覆盖':j['preference_coverage'],'事实词项覆盖':j['coverage'],'匹配来源':j['source'],'需复核词命中':bool(j['review_flags'])} for j in filtered['shown']])
            st.json({'逐项依据':filtered['shown'],'隐藏原因':filtered['hidden']})
            st.download_button('下载排序快照与依据',json.dumps(ranking,ensure_ascii=False,indent=2),file_name=f'ranking_{rank_id}.json',mime='application/json')
with st.expander('语义分析准备（API 未启用）'):
    from job_agent.semantic import prepare_request, preview_request, import_result, get_report, review_report
    st.info('按你的选择暂不启用 API。这里只生成本地预览、校验手动导入的结果，不读取密钥，也不发送数据。')
    semantic_jobs={f"#{r['id']} {r['company']} · {r['title']}":r['id'] for r in db.execute("SELECT * FROM jobs WHERE merged_into IS NULL AND status IN ('imported','matched') ORDER BY id")}
    if semantic_jobs:
        semantic_job_label=st.selectbox('分析目标岗位',list(semantic_jobs))
        semantic_facts={f"#{r['id']} {r['category']} · {(r['text_ja'] or r['text_en'])[:60]}":r['id'] for r in db.execute('SELECT * FROM facts WHERE confirmed=1 ORDER BY id')}
        semantic_selected=st.multiselect('未来允许参与分析的事实（默认不选）',list(semantic_facts))
        st.caption('本地预览只加入 JD 和所选事实原文，不自动加入证据路径、申请链接或未选事实。原文中仍可能有个人信息，请自行检查。未选择事实时只能准备 JD 分析。')
        if st.button('生成离线分析预览'):
            try:
                prepare_request(db,semantic_jobs[semantic_job_label],[semantic_facts[x] for x in semantic_selected])
                st.success('预览已保存在本地，未调用 API')
            except ValueError as e: st.error(str(e))
    semantic_requests=[r['id'] for r in db.execute('SELECT id FROM semantic_requests ORDER BY id DESC')]
    if semantic_requests:
        semantic_request_id=st.selectbox('查看分析请求预览',semantic_requests)
        try:
            semantic_preview=preview_request(db,semantic_request_id)
            if semantic_preview['stale']: st.warning('JD 或事实等上下文已变化，请重新生成预览。')
            st.json(semantic_preview)
            st.download_button('下载离线分析预览',json.dumps(semantic_preview,ensure_ascii=False,indent=2),file_name=f'semantic_request_{semantic_request_id}.json',mime='application/json')
            imported_result=st.text_area('手动导入结构化分析结果（JSON，必须符合预览中的格式）',key=f"semantic_result_{semantic_request_id}")
            if st.button('校验并保存参考结果'):
                try:
                    import_result(db,semantic_request_id,imported_result)
                    st.success('引用校验通过，结果仍需人工审阅；未修改事实或简历')
                except ValueError as e: st.error(str(e))
        except ValueError as e: st.error(str(e))
    semantic_report_ids=[r['id'] for r in db.execute('SELECT id FROM semantic_reports ORDER BY id DESC')]
    if semantic_report_ids:
        semantic_report_id=st.selectbox('查看语义参考结果',semantic_report_ids)
        try:
            semantic_report=get_report(db,semantic_report_id)
            report_labels={'review_required':'待人工审阅','reviewed':'已核对的参考意见','obsolete':'输入或引用已变化，结果失效','rejected':'用户已拒绝此参考意见'}
            st.write(report_labels[semantic_report['status']]); st.caption(semantic_report['notice']); st.json(semantic_report['result'])
            if semantic_report['status']=='review_required':
                semantic_reviewed=st.checkbox('我已核对每条 JD、事实原文引用和语义，理解引用存在不等于满足要求',key=f"semantic_reviewed_{semantic_report_id}")
                semantic_reject=st.checkbox('拒绝采纳这份参考意见',key=f"semantic_reject_{semantic_report_id}")
                semantic_note=st.text_input('参考意见审阅备注',key=f"semantic_note_{semantic_report_id}")
                if st.button('保存参考意见审阅结论'):
                    try:
                        review_report(db,semantic_report_id,expected_hash=semantic_report['result_hash'],confirmed=semantic_reviewed,reject=semantic_reject,note=semantic_note)
                        st.success('已保存参考意见结论，不改变简历批准或任何资格判断')
                    except ValueError as e: st.error(str(e))
        except ValueError as e: st.error(str(e))
db.close()
