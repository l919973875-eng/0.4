# 全球涉华早期信号 / Global China Early Signals · GitHub 云端版 v0.4

这个版本从“全球涉华新闻标题库”升级为**涉华早期信号系统**。目标不是收集越多越好，而是：

> **广泛采集 → 严格涉华筛选 → 同一事件聚类 → 多源印证 → 只展示少量值得看的事件。**

同时增加“苗头层”：X、Telegram、小红书、抖音、微博等社交来源即使没有媒体报道，也可以进入系统。**未核实不会被隐藏；Priority（值得不值得马上看）和 Confidence（目前证据强不强）分开。**

## 现在的结构

```text
新闻 / 官方 / 智库 / GDELT ─┐
                           ├─> 涉华筛选 ─> 事件聚类 ─> Priority / Confidence ─> GitHub Pages
X / Telegram / 社交桥接 ───┤
小红书 / 抖音 / 微博 ───────┘
```

数据文件：

```text
data/
├─ articles.json           # 入池新闻原始材料
├─ signals.json            # 入池社交苗头
├─ signals_external.json   # 小红书/抖音/微博外部采集结果
├─ events.json             # 聚类后的事件
├─ latest.json             # 最近24小时首页重点
└─ run_status.json         # 最近运行状态
```

---


## v0.4 新增：中英对照 + 中文/涉华来源补强

- 首页事件标题按“中文 + English”两行呈现；中国关联说明也同时给出中英文。
- 翻译是**展示层**，原始标题始终保留，不参与事实判断或事件聚类。
- 默认使用免 Key 的 best-effort 在线翻译并做 `data/translation_cache.json` 缓存；翻译失败不会阻断采集。
- 新增“来源库 / Sources”页面，可以查看全部媒体、国家/地区、语言、Tier、采集方式和入口。
- 中文/涉华来源新增一批重点渠道，包括：SCMP、联合早报、大纪元、德国之声中文、纽约时报中文网、BBC中文、VOA中文、RFA普通话、RFI中文、FT中文、日经中文、新唐人、中国数字时代、港澳台媒体，以及新华社/人民网/中新社/环球网/中国日报/财新等大陆来源。
- 来源多样性用于发现不同视角的信号；**是否收录不等于背书**，Tier 只影响排序和多源印证权重。

## v0.4 新增：社交采集“绿色但0条”诊断

旧版外部 MediaCrawler 即使实际报错也会被包装成成功，所以 Action 可能全绿但 `0` 条。v0.4 改为：

- 社交专用工作流固定 Python 3.11；
- 每个平台真正检测是否产生内容文件；
- 识别 `CAPTCHA`、`-104 没有权限访问`、云 IP 风控、Cookie 失效、超时等常见原因；
- 平台无数据时对应 step 会显示黄标/失败，但不会阻断其他平台；
- 每次运行自动上传 `social-crawl-diagnostics-*` Artifact，可下载日志；
- 网站“系统状态 / Status”直接显示 `ok / empty / blocked / not_configured` 和原因。

如果三个平台都长期显示 `blocked/empty`，说明问题通常在平台登录/风控或 GitHub 云 IP，而不是网站生成器。

## 一、运行频率

主巡检：**每天北京时间 08:17 一次**。

你也可以随时：

`Actions → 全球涉华早期信号 · 每日主巡检 → Run workflow`

手动运行时可以选择：

- `all`：新闻 + X + Telegram + RSS 社交桥接；
- `news`：只跑新闻；
- `social`：只跑社交；
- `rebuild`：不联网抓取，只用已有数据重建事件和网站；
- 临时追加一个关键词；
- `deep_scan`：把每个新闻源候选从默认 5 条提高到 12 条。

默认每个新闻源只看最新 **5 条**，不再像 v0.2 一样每源最多 80 条。

---

## 二、首页怎么看

首页默认只显示最近 24 小时最重要约 30 个事件，而不是几千篇文章。

每个事件有两个独立分数：

### Priority

回答：**“这件事如果是真的，我现在值不值得马上看？”**

主要考虑：

- 涉华直接程度；
- 潜在影响/严重程度；
- 新鲜度；
- 独立来源数量；
- 是否跨平台出现。

### Confidence

回答：**“目前证据有多强？”**

一条社交苗头完全可能是：

`Priority 92 / Confidence 28`

这类信息不会因为没有 Reuters 或官方确认就被隐藏。

---

## 三、事件聚类逻辑

Reuters、BBC、AP、X 上当地记者如果在讲同一件事，系统尽量形成**一个事件卡**，原始材料放在“展开原始证据”里。

事件状态包括：

- `苗头`：只有一个社交独立来源；
- `多源苗头`：多个独立社交来源/平台；
- `报道中`：已有新闻来源；
- `持续发展`：新闻与社交信号同时出现；
- `官方信号`：事件证据里包含官方来源。

状态只是信息结构，不代表“已证明为真”。

---

## 四、新闻来源等级

`config/source_tiers.yaml` 用于排序和独立来源计算：

- Tier 1：通讯社、官方机构、国际组织；
- Tier 2：成熟大型媒体；
- Tier 3：地区媒体、专业媒体、智库；
- Tier 4：聚合、社交或其他来源。

同一个出版机构的多个 feed 不会被故意当成很多家媒体。

---

## 五、社交平台

### X

支持两种模式：

1. `X_BEARER_TOKEN`：官方 X API，稳定性更高；
2. `X_COOKIES_JSON`：Twikit 实验模式，不需要官方 API Key，但 X 网页接口变化时可能失效，账号也可能遇到风控。

### Telegram

需要三个 GitHub Secrets：

- `TG_API_ID`
- `TG_API_HASH`
- `TG_SESSION_STRING`

然后在 `config/social_sources.yaml` 填公开频道 username。

### 小红书 / 抖音 / 微博

单独工作流：

`Actions → 社交苗头实验采集 · 小红书抖音微博`

它会在 GitHub Actions 运行时临时下载 **MediaCrawler**，使用你放在 GitHub Secrets 的 Cookie 搜索少量关键词，然后把结果转换成项目统一的 `signals_external.json`。

Secrets：

- `XHS_COOKIE`
- `DOUYIN_COOKIE`
- `WEIBO_COOKIE`

没有配置某个平台 Cookie，就自动跳过；单个平台失败也不会让其他平台/新闻系统一起失败。

**重要：MediaCrawler 使用 NON-COMMERCIAL LEARNING LICENSE 1.1。当前集成设计仅适合个人、学习、研究、低频小规模采集。不要把该外部工具直接用于商业化或大规模抓取。**

另外，GitHub 云服务器 IP 可能触发小红书/抖音/微博验证码或风控，所以该层属于“实验连接器”，不能承诺每天都成功。失败状态会被独立记录，不应理解为“当天没有苗头”。

---

## 六、你最常改的配置

### `config/china_interest_map.yaml`

最重要。继续补充：

- 中资企业；
- 矿山；
- 港口；
- 电站；
- 铁路；
- 工业园；
- 海外中国公民集中区；
- 关键供应链节点；
- 台海、南海、边境等战略热点。

### `config/social_sources.yaml`

控制：

- X 搜索主题；
- Telegram 频道；
- RSSHub / RSS 社交桥接；
- 可信账号白名单。

### `config/source_tiers.yaml`

控制新闻来源等级和 publisher family。

---

## 七、第一次部署/升级

如果你已经有旧仓库：

1. 把本版本文件全部上传覆盖；
2. 不用删除旧 `data/articles.json`，系统会继续使用；
3. `Settings → Pages → Source` 保持 `GitHub Actions`；
4. `Settings → Actions → General → Workflow permissions` 允许 Read and write；
5. 进入主 Action 手动运行一次 `all`。

新闻系统不配置任何社交 Secret 也可以正常工作。

OpenAI 仍然是可选：

`OPENAI_API_KEY`

不配置时使用规则分类；配置后用 AI 做更严格的“是否值得进入涉华池”判断。

---

## 八、项目设计原则

1. **发现优先，验证后置**：社交苗头不等媒体报道；
2. **广抓取、少展示**：后台可以多，首页必须少；
3. **文章不是事件**：同一件事聚成一张卡；
4. **Priority 与 Confidence 分离**：低可信不等于低价值；
5. **没有数据 ≠ 没有事情**：各平台连接状态单独展示；
6. **手动运行随时可用**：遇到特殊事件可以临时追加关键词；
7. **数据源逐步增加，不一次堆满**：先保证每条信息真正有用。

---

## 九、第三方与许可

本项目 v0.4 的事件聚类、评分和页面代码为本项目自己的实现，设计思想参考了 WorldMonitor 的“事件化、来源等级、多源印证、新鲜度、重要度”等公开方法，没有直接复制 WorldMonitor 源文件。

可选社交工作流运行时会下载第三方 MediaCrawler；其许可证与本项目不同，详见 `THIRD_PARTY_NOTICES.md`。
