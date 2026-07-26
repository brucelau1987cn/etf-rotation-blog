# ETF罗盘投资研究层

投资研究层用于补充行情、趋势和交易信号，保持 `research_sidecar` 模式，不直接改写正式动作、关键位、仓位或模拟盘成交。

## 决策链

```text
市场状态与最热主线
→ 产业链瓶颈扫描
→ 公司多空对抗研究
→ 证据与财务校验
→ 趋势及实时信号确认
→ 投资论文与异动跟踪
```

## 五个模块

### 风口瓶颈扫描

从ETF罗盘当前主线向产业链二、三层拆解。评估供给集中度、扩产周期、替代难度、产能利用率、需求增速和客户认证周期。

准入条件：

- 至少两项独立供需证据；
- 受益业务收入占比原则上不低于30%；
- 市值、收入、PS和PE必须完成核验；
- 高估值候选保留为观察，不进入正式动作。

### 公司多空对抗研究

四个独立角色：

1. 看多研究：商业模式、护城河、增长驱动；
2. 看空研究：竞争、替代、治理、监管和周期风险；
3. 数据审计：一手披露、股本、币种、现金流和来源差异；
4. 估值情景：悲观、中性、乐观三种路径。

四个角色分别给出结论后再汇总。关键反证缺失时维持灰色地带。

### 证据与财务校验

公共工具：`scripts/investment_research_rigor.py`。

```bash
python3 scripts/investment_research_rigor.py market-cap \
  --price 510 --shares 9.11e9 --reported 4.65e12

python3 scripts/investment_research_rigor.py cross-validate \
  --values '{"年报":7518,"交易所":7510,"独立数据源":7520}'

python3 scripts/investment_research_rigor.py scenario \
  --price 100 --eps 5 --growth 0.15 0.08 0 --pe 25 20 12
```

关键数字优先采用交易所、监管披露和公司财报，再用独立第三方交叉验证。偏差超过1%需要解释，超过5%回到原始披露核查。

### 中线投资论文追踪

适配1至3个月持仓周期。每份论文包含：

- 五句话核心逻辑；
- 3至7个可验证假设；
- 明确红线与退出条件；
- 估值锚点和时间窗口；
- 催化剂、减仓信号及下次检查时间。

状态使用 `active / confirmed / weakened / invalidated / expired / unknown`，契约见 `public/schemas/decision-thesis.schema.json`。

### 异动新闻脉搏

触发参考：单日涨跌超过5%、一周涨跌超过10%或重大公告。分别检查公司公告、监管政策、行业对手和资金情绪，输出事件时间线、主因、次因、性质判断及是否重审论文。

找不到足以解释异动的证据时，状态为“真因不明”，研究风险等级上调。

## 公开契约

- 框架数据：`/data/research/investment-research-layer.json`
- JSON Schema：`/schemas/investment-research-layer.schema.json`
- 论文契约：`/schemas/decision-thesis.schema.json`
- 证据账本：`/schemas/forward-evidence-ledger.schema.json`

正式研究数据进入公共目录前，必须通过Schema、安全字段、数据目录和构建门禁校验。

## 授权与写入边界

研究页面和旁路数据可以随构建发布。新增公司研究、修改正式动作、写入持仓论文、调整仓位或触发通知，需要单独明确授权。测试使用样例数据或临时目录，避免写入生产数据。

## 许可说明

研究方法参考了公开的 AI Berkshire 项目理念，并按ETF罗盘的中线风口、趋势风控和数据契约体系重新实现。代码与文档为本项目独立实现。若后续直接复制上游MIT代码片段，需要保留对应版权与许可声明。
