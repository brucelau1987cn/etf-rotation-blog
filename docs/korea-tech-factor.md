# Korea Tech Factor（影子研究）

## 定位

韩国科技因子用于检验韩国半导体与全球科技周期对 A 股科技 ETF 的领先性。当前仅做历史回测与每日前向冻结：

```text
mode = shadow_research_only
production_weights_changed = false
```

它不会修改正式罗盘的排序、关键位、仓位、候场/伏击资格或模拟盘。

## 第一版组件

| 组件 | 权重 | 代理含义 |
|---|---:|---|
| SK海力士相对 KOSPI 20日强弱 | 30% | 存储/HBM 周期 |
| 三星电子相对 KOSPI 20日强弱 | 20% | 韩国综合半导体景气 |
| SOXX 20日趋势 | 25% | 全球半导体风险偏好 |
| KOSPI 20日趋势 | 15% | 韩国科技/出口市场环境 |
| 韩元 20日强弱 | 10% | 外资与风险压力 |

每个组件用过去 252 个观测的百分位标准化。因子分层：

```text
强：score >= 70
中性：35 < score < 70
弱：score <= 35
```

## 无前视规则

对 A 股交易日 D，每个外部组件仅使用 **严格早于 D** 的最近完整观测。

这意味着：

- A 股盘前不会使用韩国当日尚未收盘的数据；
- A 股盘前不会使用美国当日尚未发生的数据；
- 不用当前 A 股未来收益参与因子生成。

## 回测目标

- 512480 半导体ETF
- 561980 半导体设备ETF
- 515880 通信ETF
- 515000 科技ETF
- 513310 中韩半导体ETF
- 159995 芯片ETF
- 588000 科创50ETF

统计 T+1 / T+5 / T+20：

- 等权科技篮子时间序列 RankIC / Pearson IC；
- pooled RankIC；
- 强因子与弱因子的平均收益差；
- 分层均值、中位数、胜率；
- 分标的强弱表现。

韩国因子对所有科技 ETF 是共同环境因子，因此主指标是 **科技篮子时间序列 IC**；它不是同日横截面选股因子。

## 数据与产物

```text
scripts/korea_tech_factor.py
public/data/korea-tech-factor-shadow.json
data/local/korea-tech-factor-history.json      # gitignore
data/local/korea-tech-factor-forward.json      # gitignore，每日只追加一次
```

运行：

```bash
python3 scripts/korea_tech_factor.py --refresh
```

每日 08:05 的脚本任务冻结一次影子快照；成功静默，失败报警。

## 下一步

累计至少 20 个 A 股交易日的前向样本。官方韩国半导体月度出口数据需要先按实际发布日期做 point-in-time 对齐，再作为慢因子加入；避免把月末统计值提前泄露到月内交易日。

任何生产权重、仓位上限或正式伏击资格的修改，都需要 Bruce 确认。
