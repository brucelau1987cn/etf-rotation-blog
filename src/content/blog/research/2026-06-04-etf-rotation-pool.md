---
title: 'ETF轮动池镜像：2026-06-04'
description: '轮动池取自 youth-online 原网页，实时行情由 stock-api 更新。'
pubDate: 2026-06-04
category: '研测'
---

## 结论

- 评估日期：2026-06-04
- 行情日期：2026-06-04
- 轮动池来源：https://etf.youth-online.site/3a3ff0cb1d02b1ac6bfcb87e59173b0f
- ETF名单同步：youth-online-page-js-etf-pool:https://etf.youth-online.site/3a3ff0cb1d02b1ac6bfcb87e59173b0f
- 实时行情：stock-api@2.7.2
- ETF池：22只
- 动量通过：3只
- Top 3：纳指 ETF 159501(持有), 标普 500ETF 513500(观察)
- 市场状态：极弱，权益仓 0%-10%，防御仓 80%-100%

## Top 候选

- 🔴 纳指 ETF `159501`：得分 65.7，动作 持有，20日 10.84%，5日 1.54%，斜率R² 3.5853，实时价 2.107，行情源 tencent
- 🔴 标普 500ETF `513500`：得分 61.44，动作 观察，20日 3.38%，5日 1.66%，斜率R² 0.6268，实时价 2.566，行情源 tencent
- 🔴 银华日利 ETF `511880`：得分 54.41，动作 减仓，20日 0.1%，5日 0.04%，斜率R² 0.009，实时价 100.512，行情源 tencent

## 执行口径

轮动池 ETF 名单从 youth-online 页面脚本同步，双动量参数跟随源页；本站负责展示、排序、移动端阅读体验，并用 stock-api 刷新当前价和涨跌幅。
