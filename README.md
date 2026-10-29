# Codex Job Hunting Agent

模式 B 的本地 Phase 1：手动导入官方、LinkedIn、OpenWork JD，保存来源和历史版本，维护真实事实，查看保守词项覆盖。不会联网、改写简历、填写或投递。

完整中文方案：[docs/技术方案.md](docs/技术方案.md)。六个后续逻辑 Agent Prompt 在 prompts/，当前并未调用模型。

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
相同公司加企业岗位编号或申请链接才跨来源自动合并；名称相似只提示。当前不支持手动拆分/合并、事实修改/撤销或数据导出。
来源官方归属须用户核对；URL 校验不等于归属验证。匹配忽略未确认事实，只覆盖少量明确词项，不评估语言等级、年限或录用概率。

## 测试
```sh
python3 -m pytest -q
```
标准库核心测试和 Streamlit AppTest 均不访问真实网站。依赖版本范围在 pyproject.toml；未建立生产锁文件。

## 本机已验证环境
当前机器可使用 `/opt/anaconda3/bin/python3`；部分目录默认的系统 Python 没有 pytest。
```sh
/opt/anaconda3/bin/python3 -m pytest -q
/opt/anaconda3/bin/python3 -m streamlit run app.py --server.address 127.0.0.1
```
验收记录见 docs/验收记录.md。
