# Codex Job Hunting Agent

模式 B 的本地求职工作台：手动导入 JD、真实事实库、来源溯源、批量词项匹配，以及经明确授权的官方 ATS API 采集。不会改写简历、填写或投递。

**当前没有配置具体公司或招聘源。** LinkedIn/OpenWork 只允许手动导入。
- 匹配 JD：LinkedIn = OpenWork > 官方，报告显示实际来源。
- 官方核验及申请入口：官方优先。
- “采集已授权来源并匹配”仅在用户点击后运行，没有持续后台任务。

本次变更、权限边界及未实现项：[Phase 2 实施说明](docs/Phase2实施说明.md)。

完整中文方案：[docs/技术方案.md](docs/技术方案.md)。六个后续逻辑 Agent Prompt 在 prompts/，当前并未调用模型。原始方案中的 Phase 1 描述为历史版本，当前能力以 Phase 2 说明为准。

## 运行
在本项目目录内执行：
```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m streamlit run app.py --server.address 127.0.0.1
```
浏览器打开本地显示的地址，通常为 http://127.0.0.1:8501。数据首次运行时自动迁移到 data/jobs.sqlite3，不进入 Git。不要公开此服务。

## 使用
1. 手动粘贴 JD，填写来源链接、公司、岗位、地点。只有核对过的企业岗位编号才填写。
2. 在事实库添加真实日语或英语原文和证据位置；明确核实后勾选确认。
3. 在岗位页面查看优先 JD、全部来源及可能重复项，计算词项覆盖。

事实库初始为空，没有读取父目录简历。事实证据路径只作为文本保存。测试数据只存在临时目录，不代表用户经历。
相同公司加企业岗位编号或申请链接才跨来源自动合并；名称相似只提示。当前不支持手动拆分/合并、事实修改/撤销或数据导出。API 记录包含读取授权与刷新批次。
来源官方归属须用户核对；URL 校验不等于归属验证。匹配忽略未确认事实，只覆盖少量明确词项，不评估语言等级、年限或录用概率。

## 测试
```sh
python3 -m pytest -q
```
标准库核心、来源适配器、采集管线测试和 Streamlit AppTest 均不访问真实网站。依赖版本范围在 pyproject.toml；未建立生产锁文件。

## 本机已验证环境
当前机器可使用 `/opt/anaconda3/bin/python3`；部分目录默认的系统 Python 没有 pytest。
```sh
/opt/anaconda3/bin/python3 -m pytest -q
/opt/anaconda3/bin/python3 -m streamlit run app.py --server.address 127.0.0.1
```
验收记录见 docs/验收记录.md。
