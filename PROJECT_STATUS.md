# 项目状态 · v0.4.4

## 当前定位

**全球涉华早期信号 / Global China Early Signals**：广泛发现、严格涉华筛选、事件聚类、少量展示；社交苗头不以媒体核实为入口条件。

## 已完成

- GitHub Actions + GitHub Pages，无需自建服务器；
- 主巡检每天一次 + 随时手动运行；
- 每新闻源默认最多 5 条候选；
- 原有 311 个新闻/智库/官方来源继续保留，并新增 33 个中文/涉华重点来源，固定源总数 344；
- 新增 SCMP、联合早报、大纪元、DW中文、NYT中文、BBC中文、VOA中文、RFA、RFI、FT中文、日经中文、港澳台与大陆主流来源；
- GDELT 继续作为全球补充；
- direct / indirect / potential 严格筛选；
- 来源 Tier + publisher family 独立来源计算；
- 同事件聚类；
- WorldMonitor 风格 importance：严重性 / 来源 / 多源 / 新鲜度；
- 本项目 Priority：涉华关联 / 严重性 / 新鲜度 / 多源 / 跨平台；
- Confidence 与 Priority 分离；
- 首页只展示最近24小时约30个重点事件；
- 事件标题与中国关联说明中英对照（best-effort 翻译 + 缓存，失败不阻断）；
- 新增“来源库 / Sources”页面；
- 原始文章、社交苗头、事件、首页数据分开存储；
- X 官方 API / Twikit 实验接口；
- Telegram Telethon 接口；
- RSS/RSSHub 社交桥接；
- 小红书/抖音/微博 MediaCrawler 外部实验工作流；
- 社交工作流改为 Python 3.11，并对“程序绿但0条”做真实产出检测；
- CAPTCHA、-104账号无权限、IP/账号风控、空结果等会在网站和诊断 Artifact 中明确显示；
- 单平台失败不阻断主系统；
- 社交苗头无需媒体报道即可展示；
- v0.4.4：小红书 signal 从 20 条手工词升级为 taxonomy + 高价值模板自动生成；
- 小红书 signal 默认 query budget 36、broad 72，自动去重并按北京时间稳定轮换；
- 生成器按对象/动作多样性分配，避免单日查询被某一家公司或单一动作占满；
- 社交 Review 与 query generator 共用 `config/social_keywords.yaml` taxonomy；
- 抖音/微博当前仍使用静态词包，等小红书验证后再复制生成器。

## 下一步真正值得做的

1. 连续观察 2～3 次小红书 signal 运行的 Review 与 keyword plan，评估 36 条预算是否合适；
2. 根据真实命中率调整模板权重/配额，而不是继续手工追加几十条 query；
3. 小红书逻辑稳定后，把同一 taxonomy/模板机制复制到抖音、微博并做平台特定权重；
4. 对中国利益地图继续补项目级实体，并逐步建立高价值 X / Telegram / 国内社交账号白名单；
5. 给社交苗头增加图片/视频可定位信息；若 GitHub 云 IP 风控过强，再考虑自托管 runner。
