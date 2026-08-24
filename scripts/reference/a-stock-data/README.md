# a-stock-data 参考抽取

从 [simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)（Apache 2.0）抽取的参考实现，
整合为自包含可运行脚本。**仅供参考/对比，未接入本项目生产流水线、cron 或页面。**

原项目是给 AI 编程助手用的单文件 skill（SKILL.md，208KB，11 层 / 54 端点 / 19 数据源，除 iwencai 外全零鉴权）。
抽取了其中两个对本项目最有补充价值的层：

| 文件 | 内容 | 依赖 |
|------|------|------|
| `chip_distribution.py` | 筹码分布 CYQ（本地三角分布推演：获利比例/平均成本/90-70 成本区间/筹码峰） | numpy + pandas（算法）；baostock（取 OHLC+换手率示例） |
| `limit_up.py` | 打板层（涨停/炸板/跌停/昨涨停四池 + 同花顺涨停揭秘 + 连板梯队 + 重点监控池 + 日内异动） | requests |
| `tdx_client.py` | mootdx 通达信 TCP 行情（K线/盘口/逐笔/报价，规避 BESTIP 空串 bug + 坏服务器验活） | mootdx |

## 用法

```bash
# 筹码分布 CYQ（茅台，窗口 2026-02-01 ~ 2026-08-18）
python3 scripts/reference/a-stock-data/chip_distribution.py 600519

# 打板层（指定交易日 / 最近交易日 / 只要情绪温度计）
python3 scripts/reference/a-stock-data/limit_up.py 20260821
python3 scripts/reference/a-stock-data/limit_up.py --sentiment-only
```

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
