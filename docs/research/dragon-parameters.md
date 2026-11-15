# 金银龙与结构底背离：离线影子参数诊断

## 运行

```sh
.build-venv/bin/python scripts/research_dragon_parameters.py \
  --db data/local/jinlong-bars.db \
  --history-dir public/data/low-chip-history \
  --split-date 2026-10-01 \
  --min-samples 5 \
  --output /root/.hermes/cache/scratch/dragon-parameters-run-001.json

.build-venv/bin/python -m pytest tests/test_research_dragon_parameters.py -q
```

`--output` 为必填参数，目标须位于 `/root/.hermes/cache/scratch`，且位于仓库目录之外；已存在目标会被拒绝。路径先做 resolve，写入使用 exclusive create。SQLite 以 URI `mode=ro` 打开；输入只来自本地缓存与历史快照。CLI 的网络调用数为零，正式文件保持原样。stdout 提供包含报告路径、方案数与 diagnostic 状态的 JSON，完整报告保存在指定路径。

## 输入与候选

历史目录仅接受完整匹配 `YYYY-MM-DD.json` 的文件，文件名日期须与 `data_as_of` 一致。每个日期使用其自身 `intersection` 字符串数组（例如 `['600000.SH']`） 成员；股票代码的交易所后缀用于匹配缓存时去除。缓存需有 `bars(code, trade_date, open, high, low, close)`。缺少当日 bar 或当日历史长度小于信号模块 `MIN_BARS` 的股票日从可评估池排除。

共 21 个独立方案：

- 金龙 `RATIO_DEEP_DROP`：0.74、0.80、0.85，各配 5/10/20 根观察窗。
- 银龙 `(RSI_PERIOD, RSI_LEVEL)`：`(9,20)`、`(9,25)`、`(14,20)`，各配 5/10/20 根观察窗。
- 结构底背离 `beili_signal`：距最近成立 <=5/10/20 根；逐日期使用 `signal_at` v2 的 active 状态，冻结分支 DIF 参考低点并持续保留首次失效。

观察窗采用 `bars_since_hit <= W`，包含当前根，共 W+1 根；20 窗覆盖距离 0 至 20。历史信号自身与观察当日均须通过 `MIN_BARS`。金银龙以股票+参数为键缓存一次因果 `evaluate`，计算截至最后观察日期的序列，按目标日期索引截断查询；底背离逐日期调用 `signal_at` 并复用窗口间结果。指标计算与未来收益数据分离。参数覆盖只存在于顺序研究进程中，以 `finally` 恢复原值，异常路径也会恢复。生产金龙 0.74、银龙 RSI9/20 与 MACD12/26/9 的配置文件保持原样。研究函数采用顺序执行；调用方应保持同进程指标访问串行。

## 统计口径

每方案包含 `all/train/validation`：

- `stock_days`：观察窗命中的股票日；`unique_stocks/unique_dates` 为其去重数量；`eligible_stock_days` 为历史当日成员且满足当日 `MIN_BARS` 的可评估股票日。
- `current_hits`：最新正式快照日期的窗口命中数，保留原始股票日口径。
- `deduplicated_events`：同方案同股票按日期顺序接受观察日，接受的 bar 索引间隔至少 20 根。此去重状态连续跨越训练与验证边界。
- `forward[5/10/20/60]`：对去重观察日计算前瞻收益。入场价取下一根可用 bar 的 open，离场价取观察日后第 h 根可用 bar 的 close，即 `close[t+h]/open[t+1]-1`。收益为小数，成交约束和费用忽略。
- `mean_excess`：每个信号事件收益减去其同一观察日期、同一信号族 MIN_BARS 且该收益期限可评估的历史成员收益均值，然后对事件取平均；基准包含信号股票自身。

日期固定划分：训练信号日期 `< --split-date`，验证信号日期 `>= --split-date`。训练收益终点也须 `< --split-date`，跨界样本计入 `purged_at_split`。`all` 提供全期描述统计，训练/验证结果独立呈现。每个期限有效事件数 `< --min-samples` 时输出 `status=UNAVAILABLE`、`reason=insufficient_samples`、空收益指标，并保留有效 n 与缺失原因计数。

## 解释边界

报告用途是 diagnostic。候选参数的最优性、生产适用性与统计显著性仍需独立研究确认。多重比较、稀疏事件、相关样本、缓存缺口、下一根可用 bar 的停牌间隔都会影响解释。当前前复权缓存具有缓存时点的复权基准，历史逐时点复权版本尚缺，绝对 MACD 门槛对此敏感；报告明确记录此限制。该 CLI 提供诊断统计与时间隔离，生产参数推广需单独审批。

## 复核验证

离线测试使用临时 SQLite/JSON，拦截 socket 连接、DNS 与 subprocess 外部调用。正式字符串成员测试先复现 TypeError；<=5/20 边界与 v2 持续失效测试再复现 3 项失败。修复后 7 项测试通过，覆盖同股票参数只计算一次、目标日预热、历史成员、训练清洗、只读连接及覆盖保护。

真实 CLI 使用本地缓存及 67 个正式历史快照，输出 `/root/.hermes/cache/scratch/dragon-parameters-review-001.json`，包含 21 个方案。最新日期 `2026-11-13`，成员 257 只；逐股票独立调用当前 `signal_at` 程序核对 <=20 基线：金龙 1、银龙 89、结构底背离 87，均与报告一致。

真实快照的 <=20 当前命中：金龙比率 0.74/0.80/0.85 分别为 1/2/5；银龙 RSI9/20、RSI9/25、RSI14/20 分别为 89/151/14。金龙对应训练去重事件仅 1/3/3 个，银龙 RSI14/20 为 0 个。当前证据支持保留生产基线并继续影子观察，扩大命中数量的候选仍需积累独立训练与验证样本。

金龙候选仅覆盖 `RATIO_DEEP_DROP`，`rsi` 默认周期在函数定义时绑定为 7，当前网格保持该周期。银龙 `evaluate` 显式传递 `RSI_PERIOD`，9/14 周期候选均实际参与计算。
