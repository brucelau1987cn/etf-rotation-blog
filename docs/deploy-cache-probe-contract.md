# 部署 / 缓存 / 探针契约

本文是 `etf-rotation-blog` 生产发布的操作契约。目标是避免「代码已 push，但线上仍是旧行情/旧脚本」这类漂移。

相关源：

- 站点：`etf-rotation-blog`
- 行情唯一源：`edge-quote-api`（同步到 `functions/api/public/v1/quote.js`）
- 生产域名：`https://etf.peekabo.cc`

---

## 1. 发布原则

1. **`git push` 只更新 GitHub**，生产以 Cloudflare Pages 直接部署 `dist` 为准。
2. **行情逻辑只改 `edge-quote-api`**，再 `npm run sync:quote` 同步到 blog。
3. **浏览器 live 逻辑走 `public/js/*` 外置脚本**，页面只挂 `src` 与稳定 DOM id。
4. **发布后必须跑探针** `npm run verify:pages`；探针通过才算上线完成。

---

## 2. 标准发布流程

```sh
# 0) 若改了 edge-quote-api
cd /path/to/edge-quote-api
OFFLINE=1 npm test
# 提交并 push 源仓后：
cd /path/to/etf-rotation-blog
npm run sync:quote

# 1) 站点测试 + 构建
cd /path/to/etf-rotation-blog
npm test
npm run build

# 2) 部署（token 本机约定路径）
source ~/.hermes/credentials/cloudflare-pages.env
npx wrangler pages deploy dist --project-name etf-rotation-blog --commit-dirty=true

# 3) 探针
npm run verify:pages
```

合并命令：

```sh
source ~/.hermes/credentials/cloudflare-pages.env
npm run deploy:pages
npm run verify:pages
```

### 构建门禁顺序（`npm run build`）

1. 校验跨文件批次一致性
2. 生成浏览器精简看板
3. 生成公开数据目录
4. 校验 Schema / 目录 / 公开字段安全
5. Astro 静态构建
6. **`node scripts/inject_public_js_version.mjs dist`**  
   给 HTML 里的 `/js/*.js` 注入统一内容 hash：`?v=<10位sha1>`

---

## 3. 实时行情契约

### 3.1 接口

```http
GET /api/public/v1/quote?symbols=600021.SH,XLC,nf_AU0
```

成功响应：

```json
{
  "status": "ok",
  "source": "tencent|sina|xueqiu",
  "count": 1,
  "quotes": {
    "600021": {
      "symbol": "600021",
      "price": 14.21,
      "change_percent": -7.37,
      "status": "ok"
    }
  }
}
```

失败：`status != "ok"` 或 HTTP 非 2xx。客户端只接受 **`price > 0`** 的有效报价。

### 3.2 客户端统一面

| 资源 | 角色 |
|---|---|
| `/js/normalize-quote-payload.js` | `window.EtfQuote` IIFE 适配器 |
| `/js/etf-live-poll.js` | `window.EtfLivePoll.startLivePoll` 可见性轮询 |
| `src/components/QuoteLiveScripts.astro` | 页面统一加载上述两脚本 |
| `src/lib/normalizeQuotePayload.mjs` | Node/ESM 同源适配器 |

页面 live 规则：

- 结构数据来自 `public/data/*.json`
- 实时价格叠加来自 Edge quote
- 隐藏页暂停轮询；恢复可见时立即刷新
- 禁止再走旧 host `etf-live.peekabo.cc`

### 3.3 Edge 双层短缓存

实现位置：`edge-quote-api/src/index.js`（经 `sync:quote` 进入 Pages Functions）。

| 层 | 介质 | TTL | 作用 |
|---|---|---:|---|
| L1 | isolate 内存 Map | 开市 4s / 休市 30s / 周末 60s | 同 isolate 超快 HIT |
| L2 | `caches.default` | 同上 | 跨 isolate HIT |
| 策略 | `resolveQuoteCacheTtlMs()` | 动态 | CN/US 常规时段判定 |

### 3.4 双活路径（Pages 主 / Worker 次）

| 路径 | 状态 | 说明 |
|---|---|---|
| Pages Functions `etf.peekabo.cc/api/public/v1/quote` | **生产主路径** | `npm run sync:quote` + Pages deploy |
| 独立 Worker `https://edge-quote-api.brucelau1987.workers.dev` | **已上线次路径** | `edge-quote-api` 仓库 `npm run deploy:dual` |

edge 仓库命令：

```sh
cd edge-quote-api
npm run verify:dual          # 验证 Pages + Worker
npm run deploy:dual          # 更新 Worker 并验证
```

前端继续优先使用同源 Pages quote，避免跨域与自定义域名切换成本。  
Worker 作为备用/独立观测入口；两端共享同一 `src/index.js` 源码与缓存策略。

查询参数：

- 默认：走缓存
- `?nocache=1` 或 `?refresh=1`：绕过缓存

响应头（可观测）：

| 头 | 含义 |
|---|---|
| `x-quote-cache` | `HIT` / `MISS` / `BYPASS` / `ERROR` |
| `x-quote-cache-layer` | `memory` / `edge` / `none` |
| `x-quote-cache-age-ms` | 缓存年龄 |
| `x-quote-source` | 上游源 |
| `x-quote-cache-ttl-ms` | 当前策略 TTL |
| `x-quote-cache-session` | `open_cn` / `open_us` / `open_overlap` / `closed` / `weekend` |

日志事件：`event=quote_cache`（结构化 JSON）。

HTTP body 的 CDN 缓存：

```http
cache-control: public, max-age=5, s-maxage=5, stale-while-revalidate=15
```

---

## 4. 静态资源缓存契约

配置：`public/_headers`（构建进入 `dist/_headers`）。

| 路径 | Cache-Control | 说明 |
|---|---|---|
| `/js/*` | `public, max-age=31536000, immutable` | 版本化 public JS |
| `/_astro/*` | `public, max-age=31536000, immutable` | Astro 哈希资源 |
| `/`、`/*.html` | `public, max-age=0, must-revalidate` | HTML 快刷新，带新 `?v=` |
| `/data/*` | `public, max-age=60, stale-while-revalidate=300` | 快照短缓存 |

版本注入：

- 构建后扫描 `dist/js/*.js` 内容生成统一 hash
- HTML 中所有 `src="/js/....js"` 改写为 `src="/js/....js?v=<hash>"`
- 任意 public JS 变更 → hash 变 → 新 URL → 旧 immutable 缓存可自然失效

---

## 5. 浏览器脚本清单（`public/js/`）

### 共享

| 文件 | 职责 |
|---|---|
| `normalize-quote-payload.js` | 行情 payload 归一化 |
| `etf-live-poll.js` | 可见性感知轮询 |
| `momentum-shared.js` | 动量页公共工具 |
| `market-clock.js` | 市场时钟 |
| `site-a11y.js` | skip-link / main id |

### 页面 app

| 文件 | 页面 |
|---|---|
| `home-live-app.js` | `/` |
| `a-compass-app.js` | `/a-compass/` |
| `us-compass-app.js` | `/us-compass/` |
| `a-rolling-app.js` | `/a-rolling/` |
| `a-momentum-app.js` | `/a-momentum/` |
| `us-momentum-app.js` | `/us-momentum/` |
| `futures-compass-app.js` | `/futures-compass/` |
| `token-app.js` | `/token/` |
| `login-app.js` | `/login/` |
| `lab-app.js` | `/lab/` |
| `blog-post-app.js` | BlogPost 文章布局（`/blog/*`） |

约定：

- 页面 shell 保持稳定 DOM id / `data-*` hook
- 业务逻辑优先放 `public/js/*-app.js`
- 页面内联大段 live 脚本视为回归

---

## 6. 生产探针契约

命令：

```sh
npm run verify:pages
# 或
BASE_URL=https://etf.peekabo.cc bash scripts/verify_pages_deploy.sh
```

行为：

- 默认 `BASE_URL=https://etf.peekabo.cc`
- 关键请求最多重试 3 次，降低 CDN 抖动误报
- 失败时 exit code 非 0

### 6.1 资产检查（内容标记）

至少包括：

- `/js/normalize-quote-payload.js` → `EtfQuote`
- `/js/etf-live-poll.js` → `startLivePoll`
- `/js/market-clock.js` → `data-market-clock`
- `/js/site-a11y.js` → `main-content`
- 各页面 app 脚本的关键字符串（如 `EDGE_QUOTE_URL` / `SNAPSHOT_URL` / `US_LIVE_URL`）

### 6.2 缓存头检查

- `/js/*`：`max-age=31536000` + `immutable`
- HTML：`max-age=0` / `must-revalidate` 类短缓存

### 6.3 页面标记检查

| 路径 | 关键标记示例 |
|---|---|
| `/` | `home-live-app.js`、`home-live-price`、`?v=` |
| `/a-compass/` | `a-compass-app.js`、`data-live-card` |
| `/a-rolling/` | `a-rolling-app.js`、`buy-cells-container` |
| `/a-momentum/` | `a-momentum-app.js`、`etf-body` |
| `/us-compass/` | `us-compass-app.js`、`us-live-status` |
| `/us-momentum/` | `us-momentum-app.js`、`hero-pool-size` |
| `/futures-compass/` | `futures-compass-app.js`、`data-code` |
| `/token/` | `token-app.js`、`data-token-dashboard` |
| `/login/` | `login-app.js`、`login-form` |
| `/lab/` | `lab-app.js`、`upload-form`、`audit-content` |
| `/blog/2026-05-31-etf-rotation-framework/` | `blog-post-app.js`、`article-toc`、`article-content` |

### 6.4 Quote API 抽样

探针会请求 `/api/public/v1/quote`，并校验：

1. body：`status=ok`
2. 响应头：
   - `x-quote-cache` ∈ `HIT|MISS|BYPASS`
   - `x-quote-cache-session` ∈ `open_cn|open_us|open_overlap|closed|weekend`
   - `x-quote-cache-ttl-ms` 与 session 策略一致：
     - open* = `4000`
     - closed = `30000`
     - weekend = `60000`
3. **HIT 复测**：对稳定 query（不加随机 `t=`）先 warm，再连打；在重试窗口内应出现：
   - `x-quote-cache: HIT`
   - `x-quote-cache-layer` ∈ `edge|memory`
   - `0 <= x-quote-cache-age-ms < x-quote-cache-ttl-ms`
4. **Worker 次路径**（默认开启，`SKIP_WORKER_PROBE=1` 可跳过）：
   - `WORKER_QUOTE_URL` 默认 `https://edge-quote-api.brucelau1987.workers.dev`
   - body：`status=ok`
   - warm 后出现 `x-quote-cache: HIT`

手工复核缓存时可看：

```sh
curl -sI -X GET 'https://etf.peekabo.cc/api/public/v1/quote?symbols=600021&exchange=SSE' \
  | rg -i 'x-quote-cache|x-quote-source|cache-control'
curl -sI -X GET 'https://edge-quote-api.brucelau1987.workers.dev?symbols=600021&exchange=SSE' \
  | rg -i 'x-quote-cache|x-quote-source|cache-control'
# 连打 2~3 次，期望出现 HIT + layer=edge|memory
```

---

## 7. 故障排查

| 现象 | 优先检查 |
|---|---|
| 行情一直加载中 | 页面是否引用 `QuoteLiveScripts` + 对应 `*-app.js`；quote API 是否 `status=ok`；`price>0` 过滤是否过严 |
| push 了但线上旧脚本 | 是否执行了 `wrangler pages deploy`；HTML 是否出现新的 `?v=` |
| 探针偶发失败 | 已有 3 次重试；直接 curl 单页确认 CDN 抖动 |
| 改了 quote 但 blog 未变 | 是否 `npm run sync:quote` 并重新 deploy Functions bundle |
| 跨请求总是 MISS | 正常可能落到不同 isolate；看 `x-quote-cache-layer=edge` 是否出现 |
| 独立 Worker 部署失败 | Pages token 可能无 Workers 写权限；生产当前以 Pages Functions 为准 |

---

## 8. 安全与 CSP

`public/_headers` 全局 CSP：

- `connect-src 'self' https://minimax.peekabo.cc`
- **不再**允许 `etf-live.peekabo.cc`
- 行情走同源 `/api/public/v1/quote`

Token 页通过 `data-api-base` 指向 MiniMax 代理，符合 CSP。

---

## 9. 完成定义（Definition of Done）

一次「行情/前端改造」完成，需同时满足：

1. 相关测试通过（blog `npm test`；若改 quote 则 edge `OFFLINE=1 npm test`）
2. `npm run build` 成功，且 `inject_public_js_version` 打印新 hash
3. `wrangler pages deploy` 成功
4. `npm run verify:pages` 输出 `All production markers OK`
5. 关键改动已 push（blog；以及 edge-quote-api 若有变更）

只 push 代码、未 deploy / 未探针，视为**未完成发布**。
