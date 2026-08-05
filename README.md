# ETF罗盘

[![在线网站](https://img.shields.io/badge/在线网站-etf.peekabo.cc-111827?style=flat-square)](https://etf.peekabo.cc/)
[![Astro](https://img.shields.io/badge/Astro-7-BC52EE?style=flat-square&logo=astro&logoColor=white)](https://astro.build/)
[![Cloudflare Pages](https://img.shields.io/badge/Cloudflare-Pages%20%2B%20D1-F38020?style=flat-square&logo=cloudflare&logoColor=white)](https://pages.cloudflare.com/)
[![Build](https://img.shields.io/github/actions/workflow/status/brucelau1987cn/etf-rotation-blog/validate.yml?branch=main&style=flat-square&label=build)](https://github.com/brucelau1987cn/etf-rotation-blog/actions/workflows/validate.yml)

面向 A股、港股、美股与商品期货的研究/交易决策仪表盘：实时行情、滚动多空信号、ETF 轮动、宏观约束、历史验证与模拟交易。前端 Astro，生产环境 Cloudflare Pages + Functions + D1。

**在线访问：[https://etf.peekabo.cc/](https://etf.peekabo.cc/)**

快速入口：
[A股罗盘](https://etf.peekabo.cc/a-compass/) ·
[滚动罗盘](https://etf.peekabo.cc/rolling/) ·
[低筹码股](https://etf.peekabo.cc/rolling/low-chip/) ·
[期货滚动](https://etf.peekabo.cc/rolling/futures/) ·
[美股罗盘](https://etf.peekabo.cc/us-compass/) ·
[期货罗盘](https://etf.peekabo.cc/futures-compass/) ·
[宏观数据](https://etf.peekabo.cc/futures-compass/jin10/) ·
[金银持仓](https://etf.peekabo.cc/futures-compass/holdings/) ·
[模拟盘](https://etf.peekabo.cc/paper/)

> 本项目提供研究与教育信息，不构成投资建议。影子模型仅用于研究和审计，不改变正式动作、权重、关键位或模拟执行规则。

## 主要页面

### A股 / 美股罗盘
- `/a-compass/`：A 股 ETF 罗盘与正式动作摘要（08:30 / 11:30 / 14:30 / 22:00 四窗）
- `/a-momentum/`：A 股 ETF 动量与全池
- `/a-macro/`：A 股中观与风险约束
- `/us-compass/`、`/us-momentum/`、`/us-macro/`：美股 ETF 对应页面

### 滚动罗盘（一级导航）
二级固定顺序：**A股 → 期货 → 港股 → 美股**
- `/rolling/`：A 股滚动多空能量传导
- `/rolling/futures/`：期货/现货滚动（当前白银现货 `SI=F` / `hf_XAG`）
- `/rolling/hk/`、`/rolling/us/`：港股 / 美股滚动
- `/rolling/low-chip/`：低筹码股（周/月/季三周期交集，横条双行）
- `/rolling/insights/`：滚动详细解读（盘后日更）

### 期货罗盘
- `/futures-compass/`：期货看板与简报
- `/futures-compass/jin10/`：宏观数据（金十日历；原 `/calendar/` 301 到此）
- `/futures-compass/holdings/`：金银 ETF 日频持仓（含 `change=0` 日）

### 其他
- `/paper/`：公开模拟交易快照
- `/lab/`：只读研究与影子模型
- `/research-framework/`：研究框架与证据层

## 核心能力

- **多市场决策罗盘**：A股 / 港股 / 美股 / 商品期货统一导航与状态呈现。
- **实时行情**：Edge Quote（腾讯 → 新浪 → 雪球等降级），批量报价 + 短缓存。
- **滚动多空信号**：
  - TradingView Webhook → D1 日锁（first-write-wins）
  - 信号点股价服务端 1m close 入库，前端不扇出 kline
  - 能量矩阵列按 `triggered_at` **时间序**排列；后出现的多方落在更早空方右侧
  - 多方/空方各最多展示最新 4 个正式窗口
- **金银持仓**：金十 ETF 报告代理，`attr_id=1|2&all=1` 保留 0 变动日；D1 缓存 ≤2 天热读
- **低筹码股**：iWenCai 周/月/季筛选 + 日归档历史查询
- **交易时段控制**：D1 多市场日历；休市停轮询、连续品种（金银油美元）24H 独立刷新
- **风险与研究隔离**：正式动作、影子模型、历史审计、模拟交易边界清晰
- **数据契约与构建门禁**：Schema、批次一致性、敏感字段与静态产物校验

## 技术架构

```text
Astro 静态页面 (dist/)
  ├─ Cloudflare Pages（主站 etf.peekabo.cc）
  ├─ Pages Functions
  │    quote / kline / market-calendar
  │    rolling-signals（D1 日板 + LKG）
  │    jin10-calendar / jin10-mcp-calendar
  │    jin10-etf-reports / jin10-indicator-history
  │    TradingView webhook / auth / upload
  ├─ Cloudflare D1 (etf-compass-auth)
  │    rolling_signals · jin10_calendar_items · jin10_etf_holdings
  │    market_calendar · auth/session
  ├─ Edge Quote：腾讯 → 新浪 → 雪球
  └─ JSON 契约：public/data + public/schemas + catalog
```

生产以 **`wrangler pages deploy dist`** 为准；`git push` 只更新 GitHub。

## 滚动信号（D1 日锁）

```text
TradingView POST /api/v1/tradingview
  → 解析 trigger 分钟价（payload 或 Edge kline?at=）
  → D1 INSERT OR IGNORE
       PK (trade_date, symbol, cycle_code, signal)
  → 当日同节点只锁首次；可选 WxPusher / Telegram

GET /api/public/v1/rolling-signals?symbol=
  → 静态 LKG + 当日 D1 合并
  → storage=d1 表示走了日板
```

前端：`public/js/a-rolling-app.js` + `ARollingEnergyMatrix.astro`  
正式多方窗：`2h…8h`（观察 `1.75h/105m`）  
正式空方窗：`15m…240m`（观察 `10m`；`240m` 展示「停止验证 240m」）

## 金银持仓

- 页面：`/futures-compass/holdings/`
- API：`GET /api/public/v1/jin10-etf-reports?attr_id=1|2&limit=15`
  - `attr_id=1` 黄金 ETF，`attr_id=2` 白银 ETF
  - 日频默认；`unit=week` 为周聚合
  - 上游日频必须带 **`all=1`**，否则 `change=0` 日被过滤
- 缓存：D1 `jin10_etf_holdings` 优先（行数够且最新 ≤2 天）；否则拉上游并 `waitUntil` 落库
- 浏览器：`public/js/jin10-holdings-app.js`（打开即拉，无独立 cron）

## 金十宏观数据

- 页面：`/futures-compass/jin10/`（`/calendar/` → 301）
- 直连：`GET /api/public/v1/jin10-calendar?date=YYYY-MM-DD`
- MCP：`GET /api/public/v1/jin10-mcp-calendar`（当前自然周 + `affect_txt`）
- 指标历史：`GET /api/public/v1/jin10-indicator-history?id=…`
- 独立可复用代理：[`brucelau1987cn/jin10-mcp-proxy`](https://github.com/brucelau1987cn/jin10-mcp-proxy)

## 实时行情契约

- Edge API：`GET /api/public/v1/quote?symbols=600021.SH,hf_XAG,SI=F`
- 响应：`{ status: "ok", source, count, quotes: { [code]: { price, change_percent, ... } } }`
- 客户端适配：`src/lib/normalizeQuotePayload.mjs` + `/js/normalize-quote-payload.js`（`window.EtfQuote`）
- 可见性轮询：`/js/etf-live-poll.js`（`window.EtfLivePoll`）
- 源仓：[`brucelau1987cn/edge-quote-api`](https://github.com/brucelau1987cn/edge-quote-api)  
  - `npm run sync:quote` / `npm run sync:adapter`
- 部署/缓存探针：[`docs/deploy-cache-probe-contract.md`](docs/deploy-cache-probe-contract.md)

## 定时链路（摘要）

| 时段 | 作用 |
|------|------|
| 08:30 / 11:40 / 14:30 | A 股罗盘阶段内容 + 原子发布 |
| 17:00 | 低筹码股日更 |
| 18:00 | 滚动详细解读日更 |
| 21:00 / 21:50 / 22:00 / 22:30 | A 股 qfq 缓存 → prepare 门禁 → 夜间内容 → 确定性发布 |
| 美股 06:30 等 | 美股收盘罗盘 / paper / 影子扫描 |
| 期货 08:30 / 15:20 / 23:10 | 期货罗盘 preopen / day-close / night |

多 publisher 共享 worktree：外脏路径会硬拦（`korea-tech-factor-shadow.json` / `us-selector-shadow.json` 豁免）。夜间 `base_commit` 漂移需重跑 prepare。

## 开发

要求 Node.js 22.12+ 与 Python 3.12（3.11 亦可）。包管理 **npm**（`package-lock.json`）。

```sh
npm ci
python3 -m pip install -r requirements-ci.txt
npm run dev
```

## 验证与构建

```sh
npm run test
npm run build
npm run audit
git diff --check
```

`npm run build` 门禁顺序：

1. 启动 build Python / 期货快照新鲜度
2. 跨文件批次一致性（A 股 + 美股）
3. 生成浏览器精简看板与公开目录
4. Schema / 目录 / 字段安全
5. Astro 静态构建 + JS 版本注入

**页面-only 改动**（`.astro` / CSS / JS，数据 JSON 未变）可：

```sh
rm -rf dist && npx astro build
node scripts/inject_public_js_version.mjs dist
```

## 生产发布（Cloudflare Pages）

```sh
# 1) 构建
npm run build   # 或页面-only: npx astro build + inject

# 2) 凭证
set -a; . ~/.hermes/credentials/cloudflare-pages.env; set +a

# 3) 部署（shadow 脏树需 --commit-dirty=true）
npx wrangler pages deploy dist --project-name etf-rotation-blog --commit-dirty=true

# 4) 探针
npm run verify:pages
```

或：

```sh
npm run release:pages
npm run release:dual    # Pages + edge Worker
```

凭证（本机，不入库）：

- Pages：`~/.hermes/credentials/cloudflare-pages.env`
- D1/Workers 全局：`~/.hermes/credentials/cloudflare-global.env`

## 项目结构

```text
public/data/          公开 JSON 快照与目录
public/js/            浏览器脚本（行情适配 / 轮询 / 滚动 / 持仓 / 罗盘）
public/schemas/       JSON Schema
functions/            Pages Functions
  api/public/v1/      quote · kline · rolling-signals · jin10-*
  _lib/               D1 helpers
migrations/           D1 迁移（auth / calendar / rolling / jin10 holdings）
scripts/              数据生成、门禁、发布器、Pages 探针
src/pages/            路由
  rolling/            滚动子页（futures / hk / us / low-chip / insights）
  futures-compass/    期货罗盘 / 宏观数据 / 金银持仓
src/components/       布局与看板组件（能量矩阵、摘要条等）
src/content/blog/     A 股日更与研究文章
tests/                Python + Node 回归
docs/                 契约与运维文档
```

## 数据原则

- 正式输出、影子研究、历史记录、运行快照角色分离。
- `null` / 缺数 **不等于零**；未知必须披露。
- 页面数据先生成、再编目、再校验；失败时构建关闭。
- 公开契约不发布凭据、私有路径或内部实现细节。

## 近期维护要点（2026-08）

| 项 | 说明 |
|----|------|
| 滚动能量矩阵时间序 | 买卖共享列按触发时间左→右；后多方接在前空方后（`84e8a6e`） |
| 金银持仓 `all=1` | 日频保留 `change=0`（如白银 08-03 +0.00t）（`5811ef8`） |
| 低筹码股 | 仅股票页 `/rolling/low-chip/`，三周期交集 + 日归档 |
| 滚动解读 | `/rolling/insights/` 全市场日更，禁单股页 |
| 期货二级导航 | 期货罗盘 · 宏观数据 · 金银持仓 |
| Footer | Veilx CDN 推广条 |
| 发布耦合 | 多 cron 共享 worktree；dirty / `base_commit` / 批次校验会互锁 |

## 相关文档

- [`docs/data-contracts.md`](docs/data-contracts.md) — 公开数据契约
- [`docs/deploy-cache-probe-contract.md`](docs/deploy-cache-probe-contract.md) — 部署与缓存探针
- [`docs/investment-research-layer.md`](docs/investment-research-layer.md) — 研究层与写入边界
- [`docs/nightly-github-trigger.md`](docs/nightly-github-trigger.md) — 夜间 GH 触发
