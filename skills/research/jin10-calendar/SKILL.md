---
name: jin10-calendar
description: "Use when querying or deploying Jin10 calendar data."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [jin10, calendar, macro, cloudflare, etf]
    related_skills: [etf-site-ops]
---

# 金十财经日历数据接入

## Overview

通过金十 App 使用的公开只读接口查询宏观数据、重要事件和指标历史，并通过 ETF 罗盘的 Cloudflare Pages Function 做同源代理、字段归一化和短时缓存。

仓库实现：

- Pages Function：`functions/api/public/v1/jin10-calendar.js`
- 网站页面：`src/pages/calendar.astro`
- 浏览器逻辑：`public/js/jin10-calendar-app.js`
- 线上路由：`/calendar/`
- 同源接口：`/api/public/v1/jin10-calendar`
- D1迁移：`migrations/0005_jin10_calendar.sql`
- D1同步映射：`functions/_lib/jin10-calendar-d1.js`

## When to Use

- 查询指定日期或日期范围的全球财经日历
- 获取某项宏观指标的详情或历史序列
- 将重要数据和重要事件接入期货、美股或A股研究页面
- 在 Cloudflare Pages 部署金十数据的只读代理

## Required Request Headers

金十接口需要 App 标识请求头。详情和列表实测无需 Cookie、设备ID及用户 Token。

```text
x-app-id: fiXF2nOnDycGutVA
x-version: 2.0
User-Agent: Mozilla/5.0 ETF-Compass/1.0
```

禁止将抓包中的 `x-token`、Cookie、`did`、`UM_distinctid` 提交到 GitHub。

## API Reference

### 1. 按日期范围获取全部日历

```http
GET https://rili-open-api.jin10.com/data/week_info?start_date=2026-07-31&end_date=2026-07-31
```

返回数组包含：

- `type=data`：宏观数据，包含 `data_id`、`indicator_id`、`indicator_name`、`pub_time`、`previous`、`consensus`、`actual`、`star`
- `type=event`：重要事件，包含 `id`、`event_time`、`event_content`、`country`、`star`
- 可能出现 `holiday` 类型

日期范围建议限制在31天以内，降低上游负载与边缘响应体积。

### 2. 单次数据详情

```http
GET https://rili-open-api.jin10.com/getDataById?id=1181605&category=cj&indicator_id=
```

提供前值、预期、公布、修正、机构、官网、频率、指标定义、影响说明及 Bloomberg/Reuters 代码。

### 3. 单指标历史分页

```http
GET https://rili-open-api.jin10.com/getDataListByIndId?category=cj&id=511&limit=10&pagingdate=
```

`pagingdate` 接受 `YYYY-MM-DD`，用于获取该日期之前的数据。

### 4. 单指标日期范围历史

```http
GET https://rili-open-api.jin10.com/getDataByIndIdAndDateRange?category=cj&id=511&dateRange=2025-08-01%2C2026-08-01
```

返回前值、预期值、公布值、公布时间和时间区间。

## ETF 罗盘分类口径

页面提供四个视图：

1. **重要总览**：`type` 为 `data` 或 `event` 且 `star >= 3`
2. **重要数据**：`type=data` 且 `star >= 3`
3. **重要事件**：`type=event` 且 `star >= 3`
4. **全部**：显示当日所有返回项目

时间统一按北京时间展示。页面默认打开“重要总览”，同时覆盖重要数据和重要事件。

## D1归档与影响方向

MCP 日历代理（直连 JSON-RPC + SSE，无需 MCP SDK）：

```text
GET /api/public/v1/jin10-mcp-calendar
```

- 返回当前自然周全部日历（实测 269 条）
- 每条含 `affect_txt`（利空/利多/影响较小），覆盖全部指标
- 计数 `counts.bullish/bearish/neutral`
- 需要 Pages secret `JIN10_MCP_TOKEN`（MCP Bearer Token）
- 必须带 `Accept: application/json, text/event-stream` 头
- 每次请求先 `initialize` 拿 session-id，再 `tools/call list_calendar`

受保护同步请求：

```text
GET /api/public/v1/jin10-calendar?date=YYYY-MM-DD&sync=1
Authorization: Bearer <JIN10_SYNC_TOKEN>
```

同步写入：

- `jin10_calendar_items`：原始日历归一化记录（含 `affect`/`show_affect` 数值方向）

影响方向（纯方向，无品种映射）：

- 直连 API：`type=data + show_affect=1 + actual已公布` 时，`affect=1` → `impact=利空`，`affect=2` → `impact=利多`
- MCP：`affect_txt` 直接给出 利空/利多/影响较小
- 只有 `actual` 已公布时生成影响方向，未来待公布记录仅归档
- 前端按 `impact || affect_txt` 渲染标签：利空绿 / 利多红 / 影响较小灰

## Cloudflare Deployment

Pages Function 对外暴露：

```text
/api/public/v1/jin10-calendar?date=YYYY-MM-DD
/api/public/v1/jin10-calendar?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
```

代理要求：

- 严格校验日期
- 最大31天范围
- 归一化 `data_id` 与 `id`
- 兼容 `indicator_name`、`name`、`event_content`
- 上游异常统一返回可控的 `502` JSON
- Cloudflare 边缘短时缓存5分钟
- 响应中保留数据来源说明

部署命令：

```bash
npm run build
set -a; source ~/.hermes/credentials/cloudflare-pages.env; set +a
npx wrangler pages deploy dist --project-name etf-rotation-blog
```

## Verification

```bash
node --test tests/jin10_calendar_api.test.mjs
python3 -m pytest -q tests/test_jin10_calendar_page.py
curl -fsS 'https://etf.peekabo.cc/api/public/v1/jin10-calendar?date=2026-07-31'
curl -fsS 'https://etf.peekabo.cc/calendar/?date=2026-07-31'
```

完成标准：

- API 返回 `status=ok`
- `counts.data` 与 `counts.event` 存在
- 页面包含“重要数据”“重要事件”“全部”三个视图
- 期货罗盘和页脚均有财经日历入口
- 仓库中无 Token、Cookie、设备标识

## Common Pitfalls

1. `week_info` 数据项名称字段常为 `indicator_name`，详情接口则使用 `name`。
2. 日历数据记录ID常为 `data_id`，事件使用 `id`。
3. `pub_time` 和 `event_time` 均按北京时间处理。
4. `affect` 的语义随指标和展示口径变化，页面优先展示原始数值与星级，避免自行扩大为交易结论。
5. 金十数据属于第三方来源，页面需显示来源、原始链接和数据风险说明。
6. 抓包文件含用户 Token 时不得进入版本库。
