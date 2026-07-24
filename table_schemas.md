# 飞书多维表格结构分析

## 1. 每日快报表（Daily Report）

**Base Token**: `KIC3b8SNba5pXZsudrQcfmxWnEe`  
**Table ID**: `tblwtLlIic6uPDoj`  
**URL**: https://pcne0qm17gk8.feishu.cn/wiki/CyJ1wHRJ4igxoGkrYKucJgdFnKd?table=tblwtLlIic6uPDoj&view=vewHTWNb1M

### 字段结构：
| 字段ID | 字段名 | 类型 | 说明 |
|--------|--------|------|------|
| flduxjnrRY | 采集时间 | datetime | 格式: yyyy/MM/dd HH:mm |
| fld3fsMOsT | 典型指标 | text | |
| flddoFqo3f | 标题 | text | |
| fldeZu8mES | 一级分类 | text | |
| fldFrVguQP | 摘要 | text | |
| fldn2fFtj8 | 研究核心 | text | |
| fldOLKD4A8 | 来源链接 | text | |
| fldwoDOgP7 | 二级信息点 | text | |

### 用途：
存储每日采集的大宗商品相关新闻、报告、数据更新等信息点。

---

## 2. 大宗商品信息点表（Commodity Info Points）

**Base Token**: `MCl7bXz1Saw78MsUmT1cnmMUntb`  
**Table ID**: `tblkPWxHeAaShcuA`  
**URL**: https://pcne0qm17gk8.feishu.cn/base/MCl7bXz1Saw78MsUmT1cnmMUntb?table=tblkPWxHeAaShcuA&view=vewSl7J66G

### 字段结构：
| 字段ID | 字段名 | 类型 | 说明 |
|--------|--------|------|------|
| fldo1AI6o9 | level1 | text | 一级分类 |
| fldTIUVKeY | level2 | text | 二级分类/研究核心描述 |
| fld4qb5BLV | research_core | text | 研究核心的详细描述 |
| fldkCjv6na | typical_indicators | text | 典型指标举例 |
| fldzKuLWUe | source_urls | text | 数据源URL列表（JSON数组格式） |
| fldCqvbbXu | 父记录 | link | 关联字段，链接到同表的其他记录 |

### 示例数据：
```
level1: "大宗商品本身——市场基本面与多维价格监测 (Commodity Fundamentals & Price Systems)"
level2: "实体大宗商品供需平衡表"
research_core: "大宗商品的全球总产量、总消费量、进出口量、期末库存（Ending Stocks）以及库存消费比（Stocks-to-Use Ratio）。"
typical_indicators: "中国对大豆、玉米等饲用粮的产销平衡管理及饲料大豆替代比；中东冲突导致全球化肥与尿素生产原材料供应紧缺..."
source_urls: ["https://www.fao.org/markets-and-trade/", "https://www.fas.usda.gov/data/gain-report/", ...]
```

### 用途：
定义大宗商品研究的框架体系，包含研究维度、数据源、关键指标等元数据。作为信息采集的指导框架。

---

## 两表关系分析

1. **大宗商品信息点表** = 研究框架/知识图谱（静态）
   - 定义了需要关注的研究维度和数据源
   - 是信息采集的"地图"

2. **每日快报表** = 动态信息流（动态）
   - 每日采集的具体信息记录
   - 通过"一级分类"和"二级信息点"字段与信息点表关联
   - 记录了从哪个数据源采集到的什么信息

## 工作流设计思路

### 阶段一：信息采集（每日定时执行）
1. 从"大宗商品信息点表"读取所有记录，获取：
   - 各研究维度的 source_urls
   - 对应的 level1, level2, research_core, typical_indicators
   
2. 对每个 source_url 执行信息抓取：
   - 使用 WebFetch/WebSearch 获取最新信息
   - 识别与 typical_indicators 相关的内容
   - 提取标题、摘要、关键数据
   
3. 将采集结果写入"每日快报表"：
   - 采集时间：当前时间
   - 一级分类：对应的 level1
   - 二级信息点：对应的 level2
   - 研究核心：对应的 research_core
   - 典型指标：识别出的相关指标
   - 标题、摘要、来源链接：从网页提取

### 阶段二：信息处理与分析
1. 去重：检查是否已有相同链接的记录
2. 关联分析：识别不同信息点之间的关联
3. 趋势分析：对同类信息的时间序列分析
4. 生成日报/周报

### 阶段三：智能推送
1. 根据重要性评分筛选信息
2. 生成结构化报告
3. 推送到指定渠道（飞书群聊/邮件等）

## 技术栈建议

### 本地工作流工具选项：
1. **Python + Schedule** - 简单定时任务
2. **Airflow** - 复杂 DAG 工作流（可能过重）
3. **Prefect** - 现代化工作流引擎
4. **n8n** - 可视化工作流（类似 Coze）
5. **纯 Python 脚本 + Cron** - 最轻量

### 核心依赖：
- `lark-cli` - 飞书 API 交互
- `anthropic` SDK - Claude API 用于信息提取和分析
- `requests` / `httpx` - HTTP 请求
- `beautifulsoup4` / `trafilatura` - 网页解析
- `schedule` / `apscheduler` - 定时调度

### 数据流：
```
大宗商品信息点表（元数据）
    ↓ 读取
信息采集脚本（每日定时）
    ↓ 抓取各数据源
Claude API（内容提取与分析）
    ↓ 结构化
每日快报表（写入新记录）
    ↓ 聚合分析
报告生成与推送
```

## 下一步行动建议

1. **确认需求细节**：
   - 信息采集频率？（每日几次）
   - 需要采集的信息类型？（新闻/数据/报告）
   - 信息筛选标准？（关键词、时效性等）
   - 输出格式？（飞书消息/文档/邮件）

2. **技术选型**：
   - 选择工作流框架
   - 确定部署方式（本地/服务器/云）

3. **POC 开发**：
   - 先实现单个数据源的采集与写入
   - 验证 lark-cli + Claude API 的集成
   - 测试信息提取质量

4. **迭代优化**：
   - 添加更多数据源
   - 优化信息提取算法
   - 完善去重和关联分析
   - 实现智能推送
