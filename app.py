import streamlit as st
from job_agent.core import connect, import_job, add_fact, match_job, preferred, possible_duplicates, batch_match, matching_source
st.set_page_config(page_title='求职工作台', layout='wide')
st.title('求职工作台 · Phase 2')
st.info('默认本地运行；官方 API 读取需独立授权后手动触发。不生成简历、不填写、不投递。LinkedIn/OpenWork 仅手动导入。')
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
    jobs=[dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id DESC')]
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
    if st.button('采集已授权来源并匹配'):
        from job_agent.pipeline import collect_and_match
        with st.spinner('采集与匹配中…'):
            st.json(collect_and_match(db))
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
db.close()
