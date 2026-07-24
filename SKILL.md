# SKILL: commodity_intel_collector

```yaml
name: commodity_intel_collector
version: "1.0"
description: >
  大宗商品与宏观经济高纯度数据采集与推送 Skill。
  当需要抓取最新大宗商品供需、宏观政策、航运运价数据，
  或向飞书机器人推送每日快报卡片时触发。
triggers:
  - 执行今日大宗商品与宏观增量数据搜集与清洗
  - 检查飞书多维表格当日落库数据与指纹去重状态
  - 向飞书 Webhook 推送带 AI 核心看点的互动简报卡片
```

---

## 1. 触发场景 (Trigger Intent)

| 场景 | 触发描述 |
|---|---|
| 定时全量采集 | 每日 05:00，`scheduler.py` 自动触发 `fetch_and_write()` |
| 手动增量采集 | 直接运行 `python scripts/fetch_data.py` |
| 测试模式 | `python scripts/fetch_data.py --test5`（仅处理前 5 个信息点） |
| 简报推送 | 采集完成后自动或手动运行 `python scripts/reporter.py` |
| 数据源更新 | 运行 `python scripts/update_sources.py` 批量刷新 RSS 配置 |

---

## 2. 输出数据 Schema 契约 (Output Data Schema)

当 Agent 调用 `append_daily_record(fields)` 或处理落库字典对象时，`fields` 必须且仅可包含以下精确的 Key 组合（**严格区分大小写与空格**）：

```json
{
  "采集时间":     1774137600000,
  "一级分类":     "宏观政策",
  "二级信息点":   "跨国绿色关税、环境合规与能源转型双轨规制",
  "研究核心":     "提取 CBAM 扣减规则与配额核算机制对碳密集型大宗商品进口价格的传导系数...",
  "标题":        "欧盟CBAM最新动态",
  "摘要":        "【报告时间：2026年7月22日】\n商品品类：欧盟CBAM\n关键指标：配额价格上涨 3.2%，吨均 €68.4\n市场/政策状态：正式生效期\n核心洞察：CBAM 第二期核查窗口开启，铝、钢铁进口商合规成本上升约 12%，预计推动国内替代需求季度环比增长 4~6%。",
  "来源链接":    "https://example.com/news/cbam-2026-q3",
  "来源网站名称": "英国金融时报（FT）"
}
```

### 字段校验规则 (Validation Rules)

| 字段 | 类型 | 约束 | 校验逻辑 |
|---|---|---|---|
| `采集时间` | `int` | 必填，13 位 | 毫秒时间戳，由 `TODAY_MS` 自动生成，不得手动填入历史日期。 |
| `一级分类` | `str` | 必填 | 枚举值：`市场基本面` / `航运物流` / `港口与航道` / `宏观政策`（来自信息点配置表）。 |
| `二级信息点` | `str` | 必填 | 与信息点配置表 `level2` 字段严格对应，不得自行创造新课题名。 |
| `研究核心` | `str` | 可空 | 截断至 300 字；若原始配置为空则存空字符串。 |
| `标题` | `str` | 必填，≤40字 | 由 LLM 从 `commodity_type` 拼接"最新动态"生成，禁止包含英文。 |
| `摘要` | `str` | 必填，100% 中文 | 必须经过 `summarize_v2` 或 `_deepseek_fallback_zh` 处理；**绝对禁止**包含任何英文长句原文。格式：`【报告时间：YYYY年M月D日】\n商品品类：...\n关键指标：...\n核心洞察：...` |
| `来源链接` | `str` (URL) | **Primary Key** | 合法 URL 字符串，参与 Write-Guard 第一道去重校验；重复则物理拦截，不发起写入请求。 |
| `来源网站名称` | `str` | 必填 | 由 `_site_name(url)` 域名映射生成；未知域名直接返回裸域名，不得填入中文描述性文字。 |

---

## 3. 执行入口与运行约束 (Execution Entrypoints & Constraints)

### 3.1 主 ETL 管道 `fetch_data.py`

```
fetch_and_write()
  │
  ├─ _load_existing_fingerprints()   拉取当日飞书落库指纹（URL + 摘要前50字）
  │
  ├─ get_info_points()               读取飞书信息点配置表（课题 + RSS 源列表）
  │
  └─ for point in info_points:
       _collect_one(point)
         └─ _parse_rss_feed(feed_url, cutoff)
              ├─ 全局缓存命中检查
              ├─ 历史年份硬拦截（_HIST_YEAR_RE）
              ├─ pubDate >= CUTOFF 或无时间戳含当年年份字符串
              └─ _fetch_full_text()  Trafilatura 三层正文补全
         └─ _build_record(...)
              ├─ summarize_v2()      DeepSeek 结构化提炼 + 相关性网关
              └─ event_date 精确熔断（严格模式 d >= CUTOFF）
       Write-Guard 双重碰撞检查
       append_daily_record(fields)   飞书 Bitable HTTP POST
```

**关键常量**

| 常量 | 值 | 说明 |
|---|---|---|
| `_CUTOFF` | `TODAY - timedelta(days=1)` | 24h 滑动窗口，覆盖国际时差 |
| `_CUTOFF_30` | `TODAY - timedelta(days=30)` | 官方月报宽窗口例外 |
| `_OFFICIAL_REPORT_KEYWORDS` | `("供需平衡表","WASDE","库存消费比","宏观大宗商品综合价格指数")` | 命中则使用 30 天宽窗口 |

### 3.2 简报引擎 `reporter.py`

```
send_daily_report(date_str)
  │
  ├─ get_today_records(date_str)         读取当日飞书落库记录
  │
  ├─ [Skip Push 守卫] records 为空 → 打印 [Skip Push] 并 return
  │
  ├─ _deepseek_executive_summary()       取前 8 条洞察 → DeepSeek ≤100 字 AI 看点
  │
  ├─ build_report(records, date_str)     构建飞书卡片 JSON Payload
  │    ├─ 蓝色 Header + 今日总量 + AI 核心看点
  │    ├─ 按一级分类分组，每组上限 3 条
  │    └─ 底部跳转 Button（BITABLE_URL）
  │
  └─ _send_webhook(payload)              POST 飞书 Webhook
```

### 3.3 幂等性保障

多次重复运行 `fetch_data.py`，系统行为如下：

```
第一次运行：
  拉取指纹 → 0 条已落库
  采集 → 写入 N 条，指纹集更新至 N 个 URL

第二次运行（相同批次）：
  拉取指纹 → N 条已落库（URL 指纹集 + 摘要指纹集非空）
  采集 → 所有记录命中 [URL重复阻断] 或 [内容高度雷同阻断]
  新增写入：0 条  ← 绝对幂等
```

---

## 4. 相关性网关 LLM Schema (DeepSeek JSON Contract)

`summarize_v2` 要求 DeepSeek 严格按以下 JSON Schema 输出：

```json
{
  "is_target_related": true,
  "rejection_reason": null,
  "date_of_event": "2026-07-22",
  "commodity_type": "LME铜",
  "quantitative_metrics": "库存量 -12,400 吨，至 98,250 吨；3月期价 +1.2%，至 $9,842/吨",
  "market_or_policy_status": "Backwardation",
  "analytical_summary": "LME 铜库存连续第三周下降，现货升水扩至 $45/吨，Backwardation 结构强化。下游铜材加工订单回暖信号叠加供给收紧，短期价格支撑明确；建议关注 COMEX 未平仓合约变化作为趋势确认信号。"
}
```

**拦截类别（`is_target_related: false` 无条件触发）**

| 类别 | 示例 |
|---|---|
| 体育赛事 | 世界杯财务分析、奥运会赞助收入、NBA 转播权 |
| 个人理财与消费 | 信用卡推荐、抵押贷款利率、消费品零售 |
| 娱乐八卦 | 明星代言、影视发行、网红营销 |
| 纯科技产品发布 | 消费电子新品、操作系统更新、AI 工具发布（除非明确影响大宗需求） |
| 地方民生新闻 | 城市基础设施、地方政府人事 |
| 纯公司治理八卦 | CEO 任免（非大宗商品生产商/交易所） |
| 宗教/文化/旅游 | 节日报道、旅游目的地推荐 |

---

## 5. 飞书 API 接口规范 (Feishu API Reference)

### 认证

```python
POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
Body: {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
→ 返回 tenant_access_token，有效期 2 小时
```

### 写入记录

```python
POST https://open.feishu.cn/open-apis/bitable/v1/apps/{DAILY_BASE_TOKEN}/tables/{DAILY_TABLE_ID}/records
Headers: Authorization: Bearer {token}
Body: {"fields": {<上方 Schema 字典>}}
```

### 查询当日记录（用于 Write-Guard）

```python
GET  https://open.feishu.cn/open-apis/bitable/v1/apps/{DAILY_BASE_TOKEN}/tables/{DAILY_TABLE_ID}/records
Params: page_size=100, page_token=<分页>
→ 过滤 采集时间 in [TODAY_00:00_ms, TODAY_23:59_ms)
```

### Webhook 推送

```python
POST {FEISHU_WEBHOOK_URL}
Body: {
  "msg_type": "interactive",
  "card": { "header": {...}, "elements": [...] }
}
→ 正常返回 {"code": 0}
```
