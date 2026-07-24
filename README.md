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
cd SKILLL
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入以下所有变量：

```env
# DeepSeek 大模型 API
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx

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

# 百度翻译 API（可选，英文摘要兜底翻译）
BAIDU_TRANSLATE_APPID=your_appid
BAIDU_TRANSLATE_KEY=your_key
```

### 3. 运行命令

```bash
# 手动触发全量数据采集（含 Write-Guard 去重）
python scripts/fetch_data.py

# 仅测试前 5 个信息点（快速验证）
python scripts/fetch_data.py --test5

# 手动触发简报推送（读取今日已落库数据）
python scripts/reporter.py

# 启动定时调度（常驻进程，每日 05:00 自动运行）
python scripts/scheduler.py
```

---

## 📁 项目结构

```
SKILLL/
├── scripts/
│   ├── fetch_data.py      # 主 ETL 管道（RSS 采集 → LLM 提炼 → 飞书落库）
│   ├── reporter.py        # 简报引擎（读取 Bitable → AI 看点 → Webhook 推送）
│   ├── feishu.py          # 飞书 API 基础层（鉴权、读写 Bitable）
│   ├── scheduler.py       # 定时调度（每日 05:00 触发全管道）
│   └── update_sources.py  # 数据源维护工具（批量更新 RSS Feed 配置）
├── prompts/
│   └── system_prompt.md   # DeepSeek 系统提示词模板
├── requirements.txt
├── .env.example
├── README.md
└── SKILL.md
```

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
