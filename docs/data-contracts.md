# ETF罗盘公开数据契约

本项目在 `public/schemas/` 发布机器可读的 JSON Schema，并在 `/data/catalog.json` 发布核心数据集目录。Phase 1 只建立契约和可验证目录，不创建尚无真实记录的前瞻证据账本、决策论点或漂移数据文件。

## 核心字段

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 数据结构版本；同一主版本内只做向后兼容扩展。 |
| `batch_id` | 由稳定语义字段的规范 JSON 计算 SHA-256；生成时间或文件修改时间变化不会改变它。 |
| `contract_url` | 对应公开 Schema 的站内绝对路径。 |
| `observation_date` | 数据实际观察日，不等同于计划适用日或文件生成日。 |
| `generated_at` | 产物生成时间；它可随重跑变化，不参与稳定批次身份。 |
| `completeness` | `known` 时给出 `observed / expected` 和 `[0,1]` 比率；无法可靠比较时为 `unknown`。 |
| `degradation` | `normal`、`degraded` 或 `unknown`；后两者必须给出原因。 |
| `source_categories` | 通用来源类别，不暴露具体内部采集或运行实现。 |
| `semantic_sha256` | 排除生成时间字段后的稳定内容摘要；同一观察日内的实质数据变化会更新该值。 |
| `sha256` / `bytes` | 对公开文件原始字节计算的完整性信息。 |

## 时间语义

- **计划日 / 适用日**：策略准备应用的交易日，常见字段为 `run_date`、`evaluation_date`、`applies_to`。
- **观察日**：行情、宏观或结果实际对应的日期，目录统一映射为 `observation_date`。
- **基准日**：关键位、历史收盘或模型输入所依赖的最终数据日，常见字段为 `latest_trade_date`、`level_data_as_of`。
- **生成时间**：计算或导出发生的时间，即 `generated_at` / `updated_at`。生成时间不能替代观察日。

所有日期使用 `YYYY-MM-DD`；带时间字段应包含明确时区。目录不因为时间戳更新而改变稳定 `batch_id`。

## null 与 unknown

- `null` 表示该字段在当前记录中没有已知数值，不得按 `0` 解释。
- `unknown` 是显式状态，必须同时提供原因或上下文。
- `not_applicable` 表示字段按契约不适用，与未知不同。
- 完整率未知时，`ratio`、`observed`、`expected` 必须为 `null`，且 `reason` 非空。

## 角色边界

- `production`：正式公开结论或正式约束。
- `shadow`：研究、展示或审计用途；不得改变正式动作、权重和执行规则。
- `history`：历史结果、模拟执行或发布回执。
- `runtime`：页面或流水线消费的运行快照。
- `export`：从较大快照导出的精简公开载荷。

## Schema 与未来契约

- `data-catalog.schema.json`：核心公开数据目录。
- `a-compass-dashboard.schema.json`：A股浏览器精简看板。
- `forward-evidence-ledger.schema.json`：预留的前瞻信号到结果证据链。
- `decision-thesis.schema.json`：预留的决策论点、证据与反证。
- `decision-drift.schema.json`：预留的决策漂移与变化记录。

未来契约要求稳定 ID、明确时间、生产/影子角色、状态枚举、证据与反证，以及显式 unknown 语义。在真实记录产生前不发布空数据文件。

## 兼容政策

1. 同一 Schema 主版本允许增加可选字段，不删除或重解释已有字段。
2. 删除字段、改变类型、枚举语义或批次计算规则需要新主版本和新契约 URL。
3. `a-compass-dashboard-v1` 保留既有看板字段，并只在顶层增加版本、批次与契约链接。
4. 公开目录的哈希、字节数、URL、角色和日期由构建门禁校验；篡改或不一致会阻断构建。
5. 公开 JSON 递归拒绝凭据形状字段、私有路径、HTML 分隔符和非有限数值。

## 本地校验

```sh
python3 scripts/validate_dashboard_batches.py
python3 scripts/generate_public_dashboard_payloads.py
python3 scripts/generate_data_catalog.py
python3 scripts/validate_public_data_contracts.py
```

`npm run build` 先安装 `requirements-build.txt` 中锁定的构建期依赖，再按以上顺序运行并执行 Astro 构建。
