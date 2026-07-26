# ETF罗盘

ETF罗盘是一个面向 A 股与美股 ETF 的静态研究和决策支持站点，包含市场罗盘、动量观察、宏观风险、历史记录、模拟交易和研究实验室。项目强调可复现数据、正式信号与影子研究隔离、缺失值诚实披露，以及构建前的机器校验。

> 本项目提供研究与教育信息，不构成投资建议。影子模型仅用于研究和审计，不改变正式动作、权重、关键位或模拟执行规则。

## 主要页面

- `/a-compass/`：A 股 ETF 罗盘与正式动作摘要
- `/a-momentum/`：A 股 ETF 动量和全池浏览
- `/a-macro/`：A 股中观与风险约束
- `/a-rolling/`：A 股滚动多空能量传导
- `/us-compass/`、`/us-momentum/`、`/us-macro/`：美股 ETF 对应页面
- `/paper/`：公开模拟交易快照
- `/lab/`：只读研究与影子模型结果

现有路由由 `src/pages/` 定义；Phase 1 数据契约不调整页面路径。

## 公开数据契约

- 核心目录：`/data/catalog.json`
- A 股精简看板：`/data/a-compass-dashboard.json`
- JSON Schema：`/schemas/*.schema.json`
- 字段、时间、null/unknown、角色和兼容政策：[`docs/data-contracts.md`](docs/data-contracts.md)

目录记录核心公开文件的角色、市场、Schema 版本、观察日、生成时间、完整率、降级状态、通用来源类别、稳定语义摘要、原始 SHA-256、字节数和公开 URL。稳定 `batch_id` 由数据语义生成，不依赖文件修改时间。

## 实时行情契约

- Edge API：`GET /api/public/v1/quote?symbols=600021.SH,XLC`
- 响应：`{ status: "ok", source, count, quotes: { [code]: { price, change_percent, ... } } }`
- 客户端统一适配：`src/lib/normalizeQuotePayload.mjs`（ESM）与 `/js/normalize-quote-payload.js`（浏览器 IIFE，`window.EtfQuote`）
- 可见性轮询：`/js/etf-live-poll.js`（`window.EtfLivePoll`）
- 行情服务源仓：[`brucelau1987cn/edge-quote-api`](https://github.com/brucelau1987cn/edge-quote-api)
  - 修改源仓后执行 `npm run sync:quote` 拷贝到 `functions/api/public/v1/quote.js`
  - 改适配器后执行 `npm run sync:adapter` 做 ESM/IIFE 行为校验
- Edge 双层短缓存 + 静态资源长缓存 + 生产探针：详见 [`docs/deploy-cache-probe-contract.md`](docs/deploy-cache-probe-contract.md)

`git push` 只更新 GitHub；**生产站点以 Cloudflare Pages 直接部署为准**。

## 开发

要求 Node.js 22.12+ 与 Python 3.12（Python 3.11 亦可用于当前本地测试）。包管理使用 **npm**（`package-lock.json`）；`pnpm-lock.yaml` 仅为历史残留，请勿混用。

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

`npm run build` 的发布门禁顺序为：

1. 校验跨文件批次一致性；
2. 生成 A 股浏览器精简看板；
3. 生成公开数据目录；
4. 校验 Schema、目录完整性与公开字段安全；
5. 执行 Astro 静态构建。

契约验证会阻断哈希或字节数不一致、错误 URL/角色/日期、未披露的未知或降级状态、重复证券代码、批次漂移、敏感字段、私有路径、HTML 分隔符及非有限数值。

## 生产发布（Cloudflare Pages）

校验通过后，正式上线必须直接部署 `dist`：

```sh
# 1) 构建
npm run build

# 2) 加载 Cloudflare token（本机约定路径）
source ~/.hermes/credentials/cloudflare-pages.env

# 3) 部署
npx wrangler pages deploy dist --project-name etf-rotation-blog --commit-dirty=true

# 4) 探针：确认线上 HTML 已含新适配器、页面 app 脚本、缓存头与 quote 路径
npm run verify:pages
```

或合并构建+部署：

```sh
source ~/.hermes/credentials/cloudflare-pages.env
npm run deploy:pages
npm run verify:pages
```

探针会检查：

- 共享脚本与页面 app 资产内容标记
- `/js/*` 长缓存（`max-age=31536000, immutable`）与 HTML 短缓存
- 主交易页 / 工具页 / 文章页关键 DOM 与脚本引用
- Pages quote：`status=ok` + session/TTL + warm HIT/age
- Worker 次路径：`https://edge-quote-api.brucelau1987.workers.dev` status + HIT（可用 `SKIP_WORKER_PROBE=1` 跳过）

完整契约见 [`docs/deploy-cache-probe-contract.md`](docs/deploy-cache-probe-contract.md)。

## 项目结构

```text
public/data/       公开 JSON 快照与目录
public/js/         浏览器共享脚本与页面 app（行情适配器 / 轮询 / 各页客户端）
public/schemas/    版本化 JSON Schema
functions/         Cloudflare Pages Functions（quote / webhook / auth）
scripts/           数据生成、验证、同步、版本注入与静态审计
src/pages/         Astro 路由
src/lib/           前端共享库（含 normalizeQuotePayload）
src/content/       研究文章与历史内容
tests/             Python 单元和流水线测试 + Node quote 测试
docs/              方法与契约文档
```

## 数据原则

- 正式输出、影子研究、历史记录、运行快照和精简导出具有明确角色。
- `null` 和 `unknown` 不解释为零；未知必须披露原因。
- 页面使用的数据先生成、再编目、再校验；失败时构建关闭。
- 公开契约只使用通用来源类别，不发布凭据、私有文件位置或运行实现细节。
