# ETF罗盘

ETF罗盘是一个面向 A 股与美股 ETF 的静态研究和决策支持站点，包含市场罗盘、动量观察、宏观风险、历史记录、模拟交易和研究实验室。项目强调可复现数据、正式信号与影子研究隔离、缺失值诚实披露，以及构建前的机器校验。

> 本项目提供研究与教育信息，不构成投资建议。影子模型仅用于研究和审计，不改变正式动作、权重、关键位或模拟执行规则。

## 主要页面

- `/a-compass/`：A 股 ETF 罗盘与正式动作摘要
- `/a-momentum/`：A 股 ETF 动量和全池浏览
- `/a-macro/`：A 股中观与风险约束
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

## 开发

要求 Node.js 22.12+ 与 Python 3.12（Python 3.11 亦可用于当前本地测试）。

```sh
npm ci
python3 -m pip install -r requirements-ci.txt
npm run dev
```

## 验证与构建

```sh
python3 -m pytest -q
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

## 项目结构

```text
public/data/       公开 JSON 快照与目录
public/schemas/    版本化 JSON Schema
scripts/           数据生成、验证与静态审计
src/pages/         Astro 路由
src/content/       研究文章与历史内容
tests/             Python 单元和流水线测试
docs/              方法与契约文档
```

## 数据原则

- 正式输出、影子研究、历史记录、运行快照和精简导出具有明确角色。
- `null` 和 `unknown` 不解释为零；未知必须披露原因。
- 页面使用的数据先生成、再编目、再校验；失败时构建关闭。
- 公开契约只使用通用来源类别，不发布凭据、私有文件位置或运行实现细节。
