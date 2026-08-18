# US ETF 1月/3月动量轮动影子研究

该研究层借鉴 Fincept Terminal 的 ETF Global Rotation 思路，并按本项目的数据与生产隔离要求重新实现。

## 研究口径

- 数据：本地 US ETF SQLite 缓存中 `source='yahoo'`、`is_final=1` 的调整收盘价。
- 短周期：21个交易日收益。
- 长周期：63个交易日收益。
- 综合分：`(100 × 21日收益 + 75 × 63日收益) / 175`。
- 绝对动量门禁：综合分大于0。
- 集中度控制：同主题仅保留综合分最高的ETF，最多10只。
- `SGOV`作为现金代理，不进入风险资产候选排序。

## 生产隔离

输出写入 `public/data/us-compass-shadow.json`：

- `rotation_research.mode = shadow_research_only`
- `rotation_research.production_change_allowed = false`
- `rotation_research.production_weights_changed = false`
- `rotation_history`每天按 `model_date` 幂等追加，最多保留520个交易日。

该层不改变正式Top10、趋势分、风险分、动作、仓位、组合权重或模拟盘。

## 观察门禁

- 10个完成交易日：达到最低观察门槛。
- 20个完成交易日：达到首轮评估门槛。
- 门槛达成前保持 `ACCUMULATING`。

## 失败边界

- 单只ETF缺少64根完成日K、最新日K与 `model_date` 不一致、价格无效或计算溢出时，该标的返回 `UNAVAILABLE`。
- 日K数据库缺失、损坏或查询失败时，研究区返回 `UNAVAILABLE`，历史记录继续保留。
- 缺失数据不会替换为0，也不会触发生产规则变化。
