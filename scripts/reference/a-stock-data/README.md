# a-stock-data 参考抽取

从 [simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)（Apache 2.0）抽取的参考实现，
整合为自包含可运行脚本。**仅供参考/对比，未接入本项目生产流水线、cron 或页面。**

原项目是给 AI 编程助手用的单文件 skill。抽取时的基线是 **V3.6.0（打板层）/ V3.7.0（筹码 CYQ）**；
2026-11-02 已对上游 **V3.10.3**（SKILL.md 438KB，15 层 / 87 端点 / 34 数据源，除 iwencai 外全零鉴权）逐段复核，见下文「上游版本漂移」。
抽取了其中三个对本项目最有补充价值的层：

| 文件 | 内容 | 依赖 |
|------|------|------|
| `chip_distribution.py` | 筹码分布 CYQ（本地三角分布推演：获利比例/平均成本/90-70 成本区间/筹码峰） | numpy + pandas（算法）；baostock（取 OHLC+换手率示例） |
| `limit_up.py` | 打板层（涨停/炸板/跌停/昨涨停四池 + 同花顺涨停揭秘 + 连板梯队 + 重点监控池 + 日内异动） | requests |
| `tdx_client.py` | mootdx 通达信 TCP（`check='bars'` K线/盘口/逐笔、`check='finance'` 财务/F10），规避 BESTIP 空串 bug + 坏服务器验活 | mootdx |

## 用法

```bash
# 筹码分布 CYQ（茅台，窗口 2026-02-01 ~ 2026-08-18）
python3 scripts/reference/a-stock-data/chip_distribution.py 600519

# 打板层（指定交易日 / 最近交易日 / 只要情绪温度计）
python3 scripts/reference/a-stock-data/limit_up.py 20260821
python3 scripts/reference/a-stock-data/limit_up.py --sentiment-only

# mootdx（需在 /root/.cache/mootdx/venv 里跑；行情模式目前普遍取不到，见 #52）
/root/.cache/mootdx/venv/bin/python scripts/reference/a-stock-data/tdx_client.py 600519 --check finance
```

## 上游版本漂移（V3.6/V3.7 → V3.10.3，2026-11-02 复核）

逐行比对结论：

- `chip_distribution.py`、`limit_up.py` 的**算法与端点代码与上游 V3.10.3 一致**（差异只在本项目加的 docstring/CLI 包装），无需改动。
- `tdx_client.py` 有实质缺口并已补齐：上游从 V3.10.0 起把验活按用途拆成 `check='bars'` / `check='finance'`。
- 上游中间版本新增的能力（本项目**未抽取**，需要时再单独评估）：V3.9.0 期货与大宗商品 / 事件驱动 / 可转债三层 + 腾讯 K 线 / 通达信官网盘后包 / 新浪研报 / ETF 份额 / 利率曲线共 25 个入口；V3.10.0 `tencent_ticks()` 当日逐笔与 `futures_kline()` 新浪期货日 K（含大商所）；V3.10.3 `sse_e_interaction()` 相对时间换算。

### #52：通达信公开服务器行情类命令失效

上游 2026-09 实测（#52）与**本机 2026-11-02 复测一致**（3 台节点逐台）：

| 命令 | 结果 |
|------|------|
| `bars()` / `quotes()` | **0 行**（TCP 可达，服务端返回空） |
| `finance()` | 1 行（正常） |
| `F10C()` | 只剩「最新提示」一类（正常） |

后果：旧版 `_validate()` 用 K 线验活，会把所有正常节点判死并抛
`所有 mootdx 服务器均无法取到数据`——这正是 `scripts/mootdx_shadow.py` 影子快照长期 `error` 的原因。
财务 / F10 调用必须传 `check='finance'`；K 线、盘口、逐笔改走腾讯（`qt.gtimg.cn` / `fqkline`），本项目生产价格源本就是腾讯，无需切换。

## 关键算法/坑（原项目实测校准，保留）

- **筹码 CYQ**：东财无公开 CYQ 接口（`push2`/`push2his` 均 404），业界通行本地推演——历史筹码按换手率衰减，
  当日量按三角分布（峰值在均价 `(high+low+close)/3`）撒进 `[low, high]`。初始筹码必须**播种为首日全部流通盘**
  （从零起步会把窗口前存量持仓一笔勾销）；输入必须**前复权**；停牌日不参与换手衰减；`decay` 换手衰减系数
  同花顺口径常用 1.5~2.0。
- **打板四池**：东财 `push2ex` 价格字段原始值 ×1000（要 ÷1000）；`date` 必须传交易日（非交易日 `data` 返回 null）。
- **重点监控池**：`MARKET` 是**三值**（`"1"`=沪 / `"0"`=深 / `"B"`=北交所），写成 0/1 二值会把北交所整片错标成深市。
- **日内异动**：必须带 `team=h5` 固定参数（否则 `unknow team`）；`list` 与 `count` 两端点同名 `t` 字段含义不同。
- **东财防封**：东财系接口（push2/push2ex/datacenter 等）有风控（每秒 >5 次 / 并发 ≥10 / 分钟 ≥200 → 临时封 IP），
  所有请求统一走 `em_get()` 串行限流；403 不重试（风控信号），批量任务调大 `EM_MIN_INTERVAL`。

## 与项目现有实现的对照

- 项目已有 `scripts/chip_profit.py` / `chip_profit_v2.py`（获利比例）与同花顺美股筹码自算（三角分布峰值收盘价）。
  本 `chip_distribution` 用**峰值均价 + 换手衰减 + 初始播种 + 停牌过滤**，口径更接近标准 CYQ，可作为交叉校验。
- 项目已有东财 MX 数据 / 同花顺无鉴权接口（CF 代理），打板层（push2ex 四池 + 涨停揭秘题材归因）是**尚未覆盖**的维度。

## 验证结论（2026-08-24 交叉验证）

用同花顺官方 `chip_list`（茅台 600519，2026-08-21 收盘 1272.83）做标答，交叉验证 CYQ 三角分布推演：

- **平均成本**：`decay=1.0` 时 1372.24 vs 官方 1373.76（差 **0.11%**），高度一致。
- **获利比例**：`decay=1.5` 时 15.95% vs 官方 16.23%（差 **0.3pp**），几乎命中。
- **权衡**：`decay=1.0` 平均成本准但获利比例偏低 4.7pp；`decay=1.5` 获利比例准但平均成本偏 1.2%。
  两者不可兼得，与项目美股筹码「误差互补、真实算法是中间态」结论一致。

**定位**：本项目 A股筹码已有同花顺官方 `chip_list`（CF 代理 `/api/internal/v1/ths/chip-list`），
CYQ 本地推演保留为**参考/备用**（交叉校验 + 同花顺接口失效时降级），不进正式口径。
若将来需本地推演（如美股无 HTTP 接口的标的），按目标指标选 `decay`：算平均成本用 1.0，算获利比例用 1.5。

License：Apache 2.0，来源注明即可。原始项目见 https://github.com/simonlin1212/a-stock-data
