# 第三方组件说明

## WorldMonitor

本项目参考其公开的情报产品设计思想，例如：事件聚类、来源等级、独立来源印证、新鲜度、重要度、信息缺口。v0.3 没有直接复制 WorldMonitor 源文件。

如果未来直接复制 WorldMonitor 的 AGPL-3.0 代码，应按 AGPL-3.0 保留版权/许可证、标注修改，并满足网络服务对应源码提供要求。

## MediaCrawler

仓库：NanmiCoder/MediaCrawler。

本项目**不把 MediaCrawler 源码打包进仓库**。可选的 `social-platforms.yml` 会在 GitHub Actions 运行时临时下载它，用于个人学习/研究条件下的小红书、抖音、微博低频关键词采集，再把输出转换成统一苗头格式。

MediaCrawler 当前许可证为 `NON-COMMERCIAL LEARNING LICENSE 1.1`，明确限制非商业学习/研究，并要求避免大规模抓取或影响平台运营。使用该可选工作流前应自行阅读并遵守其许可证以及目标平台规则。

## Twikit

MIT License。它使用 X 的非官方接口，稳定性和账号风险都低于官方 X API，因此本项目只把它作为实验 fallback。

## Telethon

用于 Telegram MTProto 客户端连接。请使用自己的 Telegram API 凭据和合法可访问频道。
