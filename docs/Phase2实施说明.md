# Phase 2：官方 API 采集与批量匹配

2026-10-29。用户决定暂不接入具体公司；未配置真实 board、未创建真实授权记录、未采集真实岗位。此版本提供通用能力，不声称已接通招聘来源。

## 更新后的来源规则
匹配 JD：LinkedIn = OpenWork > 官方；前两者只能手动导入。相同岗位有多个平台 JD 时，使用最近一次导入/观察的版本；报告包含 source、source_url、source_link_id、source_policy 和 JD 摘要。
官方核验与申请链接：仍优先企业官方。匹配来源和官方参考来源在界面分别标示。
跨来源合并仍需要同公司加企业岗位编号或相同申请链接等强证据。没有证据的 LinkedIn、OpenWork、官方岗位仍可能作为不同记录，不能保证自动对应。

## 新增能力
- 用户点击“采集已授权来源并匹配”：依次读取有效授权的 board，规范化、去重、保存快照，然后匹配全部本地有效岗位。单个来源失败不阻止其他来源，结果分别显示。
- 单独“匹配全部有效岗位”按钮保持完全离线。没有任何授权的采集按钮也只处理本地数据。
- Greenhouse：固定 GET board jobs，content=true；响应如含 meta.total 则校验完整性。
- Lever：global/EU 固定 GET postings，skip/limit 分页；读取描述、列表章节及补充描述。最多 100 页，每页 100 条，页间等待一秒。失败或达到上限时不做整批变更。
- Ashby：固定 GET job-board，使用 descriptionPlain/descriptionHtml，忽略明确 isListed=false 的岗位。
- 授权登记要求：provider、board、公司、官方关联链接、读取依据、带时区的未来到期时间、用户明确确认；支持撤销。登记本身不触发联网，也不代表系统验证了授权法律效力。
- 网络模块仅允许精确的固定列表路径/参数；请求还要匹配当前权限的 provider/board。仅 GET；不支持代理、重定向、登录、Cookie、任意 URL 或申请接口。
- DNS 结果全部必须是公网地址，实际 TLS 连接固定到已校验 IP，同时使用原域名做证书校验，避免二次 DNS 解析。
- 15 秒连接/读超时、8 MiB 响应上限；最多三次尝试，429/5xx 退避；过长或 HTTP 日期形式 Retry-After 直接停止供用户稍后重试。
- Greenhouse/Ashby 5 分钟本地缓存；Lever 每次读取完整分页，避免混用不同时间的缓存分页。每 board 两次启动至少间隔 5 秒。
- 整批岗位更新使用 SQLite savepoint；先完成读取与验证，再更新岗位和 source_postings。失败不执行整批导入或缺席标记。
- source_postings.active=0 仅代表该来源本次完整列表未出现，不将 jobs 全局设为 expired，保留历史 JD 和溯源。
- 修复同一来源 JD 从 A→B→A 时仍误选 B 的问题：来源保存 latest_snapshot_id 和 last_observed_at。

## 数据迁移
002_sources.sql 在原数据库上升级，保留原岗位、来源、快照、事实及报告，增加：

| 表/字段 | 用途 |
|---|---|
| job_source_links.method | manual 或 api |
| latest_snapshot_id / last_observed_at | 最后观察的内容与时间 |
| source_permissions | 逐 provider/board 授权、证据、官方关联和期限 |
| sync_runs | 每次刷新结果、条数和通用失败说明 |
| source_postings | API 外部 ID、来源链接、最后观察批次与可用状态 |
| api_cache | 授权范围内的列表响应与缓存时间 |

迁移会短暂关闭外键来替换来源表，在事务完成后恢复；测试包含 Phase 1 数据升级和 foreign_key_check。首次启动升级前建议备份现有 data 文件夹，数据库不进入 Git。

## 当前不包含
不支持 LinkedIn/OpenWork 自动抓取、登录浏览器自动化、任意官网 HTML 爬虫、互联网全站职位发现或持续后台定时任务。没有实际公司清单时不会主动发现新公司。
当前“自动”指用户点击后整条管线自动执行；并非定时持续运行。后续定时功能需要另行明确配置。
匹配仍是已确认事实的有限词项覆盖，不能判断否定、年限、等级、签证或录用概率。来源下架提示不自动排除历史岗位，用户需核实。
不实现模型简历改写、申请表填写和提交。公司别名、多语言模糊去重、人工合并/拆分、下架复核和候选重复关系持久化仍待开发，Phase 2 全部路线图尚未完成。
标题/公司/地点是岗位首次导入信息，后续 API 快照反映 JD 变化；不自动覆盖岗位身份。每次观察通过批次和来源状态溯源，正文快照按内容去重，不保存每次完整响应为永久历史。
仍为单用户、本地界面，没有应用级加密、认证、后台并发队列及生产依赖锁文件。

## 官方接口依据
2026-10-29 重新核对文档；实际启用时仍需核对具体用途和权限。
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board)
- [Lever 官方 Postings API](https://github.com/lever/postings-api)
- [Ashby Public Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)

## 验收
69 项离线测试通过。覆盖三类解析、Lever EU/分页、缓存、重试/限流、权限缺失/过期/撤销、越 board 请求拒绝、DNS 私网拒绝、固定 IP/TLS 调用路径、响应不完整与事务回滚、来源缺席状态、旧库升级、JD 回退版本、平台 JD 优先、批量匹配和界面采集按钮。
接口测试使用合成数据及模拟连接，没有真实招聘网站请求。不代表已经验证真实企业 board 的运行兼容性。

## 2026-11-04 后续更新
已加入人工核对、合并、撤销和持久化的不同岗位判断，见 [岗位核对说明](岗位核对说明.md)。本说明前文关于这些能力未实现的描述为 2026-10-29 的历史状态；公司别名库、语义召回和任意自动拆分仍未实现。
