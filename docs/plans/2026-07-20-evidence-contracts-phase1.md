# ETF罗盘证据系统 Phase 1：版本化公开数据契约

## 目标

为现有ETF罗盘建立可公开、可复现、可机器校验的数据契约基础，同时保持现有路由、正式信号、权重、关键位、模拟盘和影子模型语义不变。

## 范围

1. 新增公开JSON Schema：
   - `public/schemas/data-catalog.schema.json`
   - `public/schemas/a-compass-dashboard.schema.json`
   - `public/schemas/forward-evidence-ledger.schema.json`（阶段2预留契约）
   - `public/schemas/decision-thesis.schema.json`（阶段3预留契约）
   - `public/schemas/decision-drift.schema.json`（阶段3预留契约）
2. 新增数据目录生成器 `scripts/generate_data_catalog.py`：
   - 对核心公开数据集生成目录条目；
   - 记录角色、市场、Schema版本、观察日、生成时间、完整率、降级状态、来源类别、SHA-256、字节数和公开URL；
   - 使用稳定字段生成确定性`batch_id`，避免仅因文件mtime变化造成批次漂移；
   - 输出`public/data/catalog.json`，采用原子写入。
3. 扩展`a-compass-dashboard.json`：增加`schema_version`、`batch_id`和`contract_url`，保持现有字段兼容。
4. 新增契约验证器 `scripts/validate_public_data_contracts.py`：
   - 校验Schema文件自身结构；
   - 校验目录条目、文件哈希、字节数、URL、角色枚举、日期语义、完整率和未知/降级披露；
   - 校验A股精简看板的必需字段、唯一代码、行数和批次身份；
   - 禁止凭据、私有路径、HTML注入和非有限数值进入公开契约。
5. 调整构建顺序：
   - 现有跨批次验证；
   - 生成浏览器精简看板；
   - 生成公开数据目录；
   - 运行公开契约验证；
   - Astro构建。
6. 新增测试与文档：
   - 生成确定性、原子写、字段完整性、哈希篡改、敏感字段、非有限值、日期语义和兼容性测试；
   - `docs/data-contracts.md`记录字段词典、空值语义、时间语义、角色边界和兼容政策；
   - README更新为ETF罗盘真实项目说明。

## 核心约束

- 不修改现有公开路由。
- 不改变正式动作、仓位、关键位和模拟盘规则。
- 不把研究影子输出提升为正式信号。
- `unknown`保持未知；空数据不解释为零。
- 不公开内部工具名、凭据、私有文件路径、数据库位置或模型checkpoint。
- 构建产物必须确定性；`generated_at`可反映运行时间，`batch_id`必须由稳定数据语义生成。
- 现有未相关修改不得进入提交。

## Gate

- **Pre-flight**：隔离worktree、基于`origin/main`、目标工作区干净。
- **Revision**：规格审查通过后进入质量审查；问题最多循环3次。
- **Pre-release**：全量pytest、dashboard batch验证、契约验证、Astro build、静态审计、diff检查全部通过。
- **Release**：独立集成审查批准后才允许提交、推送和生产验证。

## 验收标准

- `python3 -m pytest -q`全绿。
- `python3 scripts/validate_dashboard_batches.py`通过。
- `python3 scripts/generate_public_dashboard_payloads.py`成功。
- `python3 scripts/generate_data_catalog.py`成功且重复执行的`batch_id`和目录语义稳定。
- `python3 scripts/validate_public_data_contracts.py`通过。
- `npm run build`与`npm run audit`通过。
- `catalog.json`中每个核心条目的SHA-256和字节数与实际文件一致。
- 生产`/data/catalog.json`、`/schemas/*.schema.json`和`/data/a-compass-dashboard.json`均HTTP 200并含本阶段标记。
- 原有首页、A股、美股、历史、模拟盘和实验室路由继续HTTP 200。
