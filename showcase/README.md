# SHU SIGNAL 网页作品

该目录只保存网页作品的可维护源码。Skill业务运行位于上级目录的 `scripts/runtime/`，监控位于 `scripts/monitoring/`。

## 目录职责

- 根目录的6个HTML文件、`shu-signal-pages.css`、`analytics.js`：静态页面唯一源码与统一匿名埋点。
- `shu-signal-data.js`：由Skill每日运行生成并提交的公开数据快照。
- `app/`：Sites/Next运行入口；数据路由可从飞书读取当日记录。
- `app/api/feedback/`、`app/api/events/`：接收价值反馈和用户行为事件并写入D1。
- `app/api/analytics/`、`app/api/quality-reviews/`：提供聚合看板、保留策略预检和Bad Case状态流转。
- `db/`、`drizzle/`：反馈/事件数据结构与不可变数据库迁移。
- `scripts/sync-static-site.mjs`：构建前把静态源码复制到 `public/`。
- `public/`：只保留长期静态资源；HTML/CSS复制件属于构建产物，不入库。
- `dist/`、`.wrangler/`、`node_modules/`：可重新生成，不入库。

## 本地运行

```bash
npm ci
npm run dev
```

## 验证

```bash
# 无需安装依赖，只检查网页源码及路由关系
npm run test:source

# 安装依赖后执行完整构建与源码检查
npm test
```

Node.js 要求 `>=22.13.0`。业务数据和飞书密钥不得直接写进HTML或提交到仓库。

## 情报反馈

详情页支持“有用/暂时没用”。选择“暂时没用”后还会记录主要原因。同一匿名用户对同一条情报重复反馈时更新原记录，不增加重复行。

反馈依赖服务端API和D1持久化，因此应使用当前Sites/Worker版本发布。纯静态GitHub Pages可以展示页面，但不能单独承载反馈接口。

## 用户行为监控

5个业务页面覆盖访问、分类筛选、搜索/零结果、情报点击、详情、原文、时间线和反馈提交。浏览器仅使用匿名ID与会话ID；本地流量标记为测试环境，正式指标口径见 `../references/monitoring/phase3/README.md`。

第四阶段新增 `monitoring-dashboard.html`：展示用户价值指标、渠道、趋势、异常和Bad Case。公开视图只返回聚合信息；配置 `MONITORING_ADMIN_KEY` 并在页面临时输入后，才可查看零结果词和更新处理状态。告警阈值、周报和保留策略见 `../references/monitoring/phase4/README.md`。

第五阶段在D1新增按日网页快照。正常访问优先读取当日缓存；实时飞书读取失败时返回最近一次成功快照并标记为过期。页面不再包含固定演示日期，具体口径见 `../references/monitoring/phase5/README.md`。
