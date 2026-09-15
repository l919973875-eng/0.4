## 来源与范围

- `config/sources.yaml` 保留 **344 个**全球媒体、政府、行业与智库来源；
- 国内公开材料、全球新闻和 RSS/GDELT 由主程序汇集；
- 本机一键扫描 X、微博、抖音、小红书、YouTube 的公开搜索结果；
- 公众号使用公开文章索引或已获授权的账号来源，明确标注其覆盖边界。

## 本机一键社交扫描

每天上午、下午各运行一次：

```powershell
.\tools\run-manual-social-scan.ps1 -Slot morning
.\tools\run-manual-social-scan.ps1 -Slot afternoon
```

首次运行会打开一个专用 Chrome 配置目录。请在该窗口完成平台登录，随后回到 PowerShell 按回车；以后运行会复用该会话。

采集器只读取公开搜索页中可见的卡片，写入 `data/signals_external.json`，再由 `cloud_runner.py --mode rebuild` 去重、分级、聚类并重建站点。浏览器会话只保存在本机 `data/local-chrome-profile/`，不会提交进仓库。

## 研判原则

- 社交内容一律先标为待核，单一贴文/视频不作为事实结论；
- 同一链接、同一平台同一作者同日相同文本优先合并；跨平台独立来源保留为证据；
- “已发生事件”与“经营、服务压力等结构性风险”分开标注；
- 涉渝关联必须说明本地发生、长江航运、产业链、物流链或无直接关联；
- 不以个别创作者或评论推断群体立场、价值观或政治倾向。

## 主要配置

- `config/sources.yaml`：全球来源与智库；
- `config/social_sources.yaml`：X、Telegram、RSS 等云端公开源；
- `config/manual_social_queries.json`：上午/下午本机平台查询计划；
- `config/signal_taxonomy.yaml`：事件分类与核验框架。

旧的 GitHub 云端 MediaCrawler/Cookie 工作流已移除，避免重复文件、云端登录态维护和过期说明造成混乱。
