import streamlit as st
from job_agent.core import connect, import_job, add_fact, match_job, preferred, possible_duplicates
st.set_page_config(page_title='求职工作台', layout='wide')
st.title('求职工作台 · Phase 1')
st.info('本地手动导入；不联网、不生成简历、不填写、不投递。事实库初始为空。')
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
        st.write('优先 JD 来源：',chosen['source']); st.code(chosen['url'])
        st.write('该来源申请链接：')
        st.code(chosen['application_url'] or '未提供；请人工核对官方申请入口')
        st.text(chosen['content'])
        st.write('来源溯源'); st.dataframe([dict(r) for r in db.execute('SELECT * FROM job_source_links WHERE job_id=?',(ident,))])
        candidates=possible_duplicates(db,ident)
        if candidates: st.warning('存在名称和地点相同的岗位，请人工核对，未自动合并。'); st.dataframe(candidates)
        if st.button('计算事实词项覆盖'):
            st.json(match_job(db,ident))
db.close()
