# 大宗商品与宏观经济高纯度数据采集管道

**Commodity Intel Pipeline** — 企业级 ETL 数据提纯与智能推送引擎

---

## 🌐 项目简介 (Overview)

本项目是一套基于**文章中心化（Article-Centric）架构**的全自动大宗商品与宏观经济数据采集、清洗与推送引擎。

**核心价值**：突破传统采集脚本的两大顽症——

- **数据笛卡尔积膨胀**：同一事件被多个 RSS 源重复报道，导致面板数据库中出现大量冗余记录。
- **泛新闻噪音穿透**：体育赛事、个人理财、娱乐八卦等无关内容混入宏观研究数据集。

通过五重物理漏斗、LLM 二分类相关性网关与写入前双重指纹去重，系统确保落库数据集满足以下约束：**防脏数据穿透、来源链接唯一性（Primary Key）、24h 时区感知滑动窗口、100% 中文化摘要**，并在每日清晨自动向飞书机器人推送带 AI 提炼看点的交互卡片。

---

## 🏛️ 系统架构与五重提纯漏斗 (System Architecture)

```
[源头 RSS Feeds (FT, EIA, Mining.com, Hellenic, gCaptain, SCMP...)]
│
▼  【漏斗 1：内存级全局请求缓存 (Global Feed Cache)】
   同一进程内相同 Feed URL 只请求一次网络，后续信息点直接复用缓存，
   规避重复抓取与限流。同步执行 URL/标题历史年份硬拦截（regex drop）。
│
▼  【漏斗 2：物理年份与 24h 时区窗口 (_CUTOFF = TODAY - 1 day)】
   pubDate 已知时严格 >= CUTOFF；pubDate 缺失时检查 URL/标题含当年年份
   字符串，否则保守丢弃——绝不触发后续正文抓取与大模型调用。
│
▼  [正文完整补全 (Trafilatura 三层策略)]
   precision → recall → <p> 正则，确保传入 LLM 的上下文足够完整。
│
▼  【漏斗 3：大模型二分类相关性网关 (DeepSeek Relevance Gateway)】
   严格黑名单（体育/娱乐/个人理财/纯科技发布/地方民生）× 正向白名单
   （大宗价格/宏观数据/地缘政治/航运运价/监管政策/交易所数据）双重判定。
   is_target_related=false → 物理阻断，打印拦截原因，不进入任何后续步骤。
   所有输出字段强制中文；异常或 insight 缺失时调用 _deepseek_fallback_zh
   兜底，绝不将英文原文写入摘要。
│
▼  【漏斗 4：LLM 提取事件日期二次熔断 (Event Date Gate)】
   大模型提取的 date_of_event 若早于 CUTOFF（官方月报宽窗口 30 天），
   则在内存中直接丢弃，打印 [非当天数据拦截]，阻断入库。
│
▼  【漏斗 5：写入前双重指纹校验 (Pre-Write Duplicate Guard)】
   启动时拉取飞书表格当日已落库记录，建立：
     • existing_urls         来源链接精确集合（Primary Key 去重）
     • existing_fingerprints 摘要前 50 字集合（内容高度雷同去重）
   两道校验均通过方可发起 HTTP POST；写入成功后立即更新进程内指纹集，
   覆盖同批次跨信息点重复。系统实现绝对幂等性。
│
▼  [飞书多维表格 (Feishu Bitable Fact Table) 唯一落库]
```

---

## 📊 飞书多维表格业务数据字典 (Bitable Business Data Dictionary)

落库事实表（Fact Table）字段定义：

| 字段名称 | 字段类型 | 约束条件 | 业务生成逻辑与学术/商业价值 |
|---|---|---|---|
| 采集时间 | 毫秒时间戳 | 必填 | 系统运行时间戳（`TODAY_MS`），用于面板数据（Panel Data）时间序列对齐与分组查询。 |
| 一级分类 | 单选/文本 | 必填 | 继承自课题配置表的宏观领域分类，如：市场基本面、宏观政策、航运物流、港口与航道。 |
| 二级信息点 | 文本 | 必填 | 匹配到的研究课题名称（如：LME铜供需平衡、欧盟CBAM合规规制）。 |
| 研究核心 | 文本 | 可空 | 对应课题的研究核心定义前 300 字上下文，保留原始配置语义。 |
| 标题 | 文本 | 必填 | LLM 提取的"商品品类 + 最新动态"，严格控制在 40 字以内。 |
| 摘要 | 多行文本 | 必填 | 格式化高密度数据洞察：`【报告时间】` + 商品品类 + 关键量化指标（数值+单位+方向）+ 期限结构/政策阶段 + 100% 中文传导逻辑与可操作判断结论，≤350 字。 |
| 来源链接 | 链接 (URL) | **主键 (PK)** | 全局绝对唯一，触发 Write-Guard 指纹去重第一道校验，保障系统绝对幂等性。 |
| 来源网站名称 | 文本 | 必填 | 域名自动映射显示名称，如：英国金融时报（FT）、Hellenic Shipping News、美国能源信息署（EIA）。 |

---

## 📲 飞书机器人简报交互规范 (Daily Report Card Schema)

**调度与触发**

- 每日清晨 **05:00** 自动触发全量 ETL 采集管道（`scheduler.py`）。
- 采集完成后立即发送飞书互动卡片简报；当日记录数为 0 时执行 `[Skip Push]` 静默守卫，不发送空卡片。

**卡片结构**

```
┌─────────────────────────────────────────────┐
│  🌐 大宗商品与宏观每日快报 · YYYY年MM月DD日   │  ← 蓝色 Header
├─────────────────────────────────────────────┤
│  📊 今日共采集 N 条高纯度洞察                │
│                                             │
│  💡 今日核心看点：                           │
│  {DeepSeek ≤100 字 AI 提炼摘要}             │  ← 取前 8 条洞察送 DeepSeek
├─────────────────────────────────────────────┤
│  📈 市场基本面（N 条）                       │
│  📌 [LME铜供需] 铜库存最新动态              │
│   • 摘要：...                               │
│   🔗 查看源网页                             │
│  ...（每组上限 3 条，超出显示折叠提示）       │
├─────────────────────────────────────────────┤
│  🚢 航运物流  /  🏛️ 宏观政策  /  ⚓ 港口...  │
├─────────────────────────────────────────────┤
│  [ 查看飞书多维完整面板 ]                    │  ← Primary 跳转 Button
└─────────────────────────────────────────────┘
```

---

## 🚀 快速上手 (Quick Start)

### 1. 克隆与安装依赖

```bash
git clone <repo-url>
cd SKILL1
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入以下所有变量：

```env
# DeepSeek 大模型 API
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
DEEPSEEK_MODEL=deepseek-chat

# 可选：按供应商当前价格填写，用于模型成本估算（美元/百万Token）
MODEL_INPUT_COST_PER_MILLION=
MODEL_OUTPUT_COST_PER_MILLION=

# 飞书开放平台应用凭证（https://open.feishu.cn/app）
FEISHU_APP_ID=cli_xxxxxxxxxx
FEISHU_APP_SECRET=your_app_secret

# 飞书多维表格：每日快报落库表
DAILY_BASE_TOKEN=KIC3b8SNba5pXZsudrQcfmxWnEe
DAILY_TABLE_ID=tblwtLlIic6uPDoj

# 飞书多维表格：信息点配置表（课题与 RSS 源）
INFO_BASE_TOKEN=MCl7bXz1Saw78MsUmT1cnmMUntb
INFO_TABLE_ID=tblkPWxHeAaShcuA

# 飞书机器人 Webhook（简报卡片推送）
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx

# Skill运行监控卡片专用 Webhook（可选；不填则只生成本地报告）
MONITORING_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx

# 监控存储（可选，以下为默认值）
MONITORING_DB_PATH=data/monitoring.sqlite3
MONITORING_REPORT_DIR=data/monitoring
SKILL_ENVIRONMENT=production
SKILL_VERSION=1.5

# 第四阶段用户价值看板与周期报告
SHU_SIGNAL_SITE_URL=https://你的站点地址
MONITORING_ADMIN_KEY=请替换为高强度随机密钥
USER_VALUE_REPORT_DIR=data/monitoring/user_value

# 百度翻译 API（可选，英文摘要兜底翻译）
BAIDU_TRANSLATE_APPID=your_appid
BAIDU_TRANSLATE_KEY=your_key
```

### 3. 运行命令

```bash
# 手动触发全量数据采集（含 Write-Guard 去重）
python scripts/runtime/fetch_data.py

# 仅测试前 5 个信息点（快速验证）
python scripts/runtime/fetch_data.py --test5

# 手动触发简报推送（读取今日已落库数据）
python scripts/runtime/reporter.py

# 推荐：一次运行完整自动链路（采集 → 飞书推送 → 网站更新）
python scripts/runtime/daily_pipeline.py

# 只用飞书当日数据重建网站，不重复采集和群推送
python scripts/runtime/daily_pipeline.py --skip-collect --skip-push

# 启动本地定时调度（常驻进程，每日北京时间 05:00 自动运行）
python scripts/runtime/scheduler.py

# 查看最近一次Skill运行漏斗
python scripts/monitoring/phase1/runtime_monitor.py

# 查看最近一次正式运行的模型、Prompt、Token与延迟指标
python scripts/monitoring/phase2/model_monitor.py

# 手动运行固定Prompt回归评测（会调用模型并产生Token消耗）
python scripts/monitoring/phase2/evaluate_prompts.py

# 查看最近一次运行的P0/P1告警与人工抽检队列
python scripts/monitoring/phase4/governance.py --json

# 从已发布网站生成用户价值周报（不发送通知）
python scripts/monitoring/phase4/user_value_monitor.py --site-url https://你的站点地址 --days 7 --notification-mode never

# 强制刷新网站当日快照，并检查日期与记录数
python scripts/monitoring/phase5/freshness_monitor.py --site-url https://你的站点地址 --refresh
```

### Skill运行监控

完整链路和单独采集命令都会生成唯一 `run_id`，并按稳定 `article_id` 记录候选文章经过时间、正文解析、相关性、事件日期、去重和写入关卡的结果。运行数据保存在 `data/monitoring.sqlite3`，最近一次可读报告保存在 `data/monitoring/latest_run.md` 和 `latest_run.json`。

第二阶段在同一数据库记录模型名称、Prompt版本、Token、调用延迟、JSON一次成功、修复、降级与错误类型，不保存生产文章正文或完整Prompt。固定评测集检查相关性判断、事件日期和关键词保留；评测批次单独标记为 `evaluation`，不会覆盖“最近一次正式运行”，默认也不混入7天正式运行对比。模型成本只有在配置两项Token单价后才估算，避免使用过期价格。

第三阶段在网页端以匿名用户ID和会话ID记录访问、筛选、搜索、情报点击、详情、原文、时间线与价值反馈，数据分别保存到Sites D1的 `user_events` 和 `intelligence_feedback`。本地流量标记为 `test`，正式指标只统计 `production`；完整事件字典和指标口径见 `references/monitoring/phase3/README.md`。

第四阶段新增用户价值看板、运行/模型/用户价值阈值告警、Bad Case自动回流、每周保留与拦截双向抽检、飞书周报/月报和数据保留预检。完整闭环与初始阈值见 `references/monitoring/phase4/README.md`。

第五阶段新增页面真实日期、D1每日快照、实时失败回退、每日缓存预热和数据新鲜度告警，避免“采集成功但网页仍显示旧日期”。实现与阈值见 `references/monitoring/phase5/README.md`。

本地报告始终生成；只有配置独立的 `MONITORING_FEISHU_WEBHOOK_URL` 时才发送运行监控卡片，不会复用业务简报群机器人。GitHub Actions 会将每次运行的监控数据库和报告保留为 30 天构件，失败任务也会保存。

### 网站自动发布

GitHub Actions 的 `daily_collect.yml` 每天北京时间 05:00 按以下顺序运行：

1. 完成采集并写入飞书每日简报表；
2. 回读飞书当日完整记录并发送群简报；
3. 生成 `showcase/shu-signal-data.js`；
4. 保存新的公开数据快照并发布到 GitHub Pages。

群推送失败时不会刷新网站，避免群内简报与网站版本不一致。任务重试时会依据网站是否已经存在当日快照决定是否补发群简报；已成功更新过的日期不会重复发送。

---

## 📁 项目结构

```
SKILL1/
├── scripts/
│   ├── runtime/           # Skill运行：采集、飞书、推送、网页快照与调度
│   └── monitoring/
│       ├── phase1/        # 任务、来源、漏斗与交付监控
│       ├── phase2/        # 模型调用、Prompt版本、质量与成本监控
│       ├── phase4/        # 告警、抽检、Bad Case与用户价值周报
│       └── phase5/        # 网页数据新鲜度、缓存预热与发布核对
├── references/
│   ├── runtime/           # 飞书表结构等运行参考
│   └── monitoring/        # phase1运行、phase2模型、phase3行为、phase4治理、phase5新鲜度
├── tests/skill_monitoring/ # 按phase1/phase2/phase4/phase5归档的监控测试
├── portfolio/             # PRD与求职/面试材料，不参与Skill运行
├── showcase/              # 网页作品源码
├── requirements.txt
├── .env.example
├── README.md
└── SKILL.md
```

目录职责：`scripts/runtime/` 只放业务运行代码；`scripts/monitoring/phase1/` 负责链路与漏斗；`phase2/` 负责模型与Prompt；`phase4/` 负责告警、抽检与周期报告；`phase5/` 负责网页数据新鲜度和发布核对；`portfolio/` 和 `showcase/` 分别保存求职材料与网页作品。

---

## 🔧 技术栈

| 层次 | 技术选型 |
|---|---|
| RSS 解析 | `feedparser` + `trafilatura`（三层正文补全策略） |
| 大模型提炼 | DeepSeek Chat API（相关性二分类 + 结构化 JSON 提取） |
| 兜底翻译 | 百度翻译 API / DeepSeek fallback（确保 100% 中文落库） |
| 飞书集成 | Feishu Open API v1（Bitable 读写 + Webhook 卡片推送） |
| 调度 | `schedule`（轻量级 Python 定时任务，无需 cron） |
| 去重 | 内存指纹集合（URL PK + 摘要前50字）× 飞书落库记录双向校验 |
