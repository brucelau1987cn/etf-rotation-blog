export type DailySignalInsight = {
  name: string;
  symbol: string;
  market: string;
  direction: 'BUY' | 'SELL' | 'MIXED';
  nodes: string;
  signalPrices: string;
  close: string;
  change: string;
  validation: 'confirmed' | 'reclaimed' | 'mixed' | 'watch';
  validationLabel: string;
  verdict: string;
  support: string;
  pressure: string;
  buyPlan: string;
  sellPlan: string;
  evidence: string[];
};

export type DailyInsightReport = {
  tradeDate: string;
  shortDate: string;
  title: string;
  subtitle: string;
  cutoff: string;
  summary: string;
  buyRule: string;
  sellRule: string;
  discipline: string;
  signals: DailySignalInsight[];
  sources: string;
};

export const rollingDailyReports: Record<string, DailyInsightReport> = {
  '2026-08-03': {
    tradeDate: '2026-08-03',
    shortDate: '08/03',
    title: '8月3日滚动信号收盘复盘',
    subtitle: 'A股与港股共9只标的：6只空方收盘确认，2只完成信号价反收复，1只多方处于确认观察。',
    cutoff: '2026-08-03 收市',
    summary: '执行重点是区分“下跌确认”和“超跌末端”。深科技、华天科技、德福科技、海光信息及两只港股收盘仍压在信号价下方，防守优先；三安光电、长鑫科技已经收复空方信号价，转入反弹验证；东方明珠多方节点接近收盘价，等待放量确认。',
    buyRule: '优先观察已经收复信号价的三安光电、长鑫科技，以及站稳¥8.31并放量的东方明珠。',
    sellRule: '空方确认股反弹至信号价或MA5附近减仓；跌破当日低点继续执行风控。',
    discipline: '空方确认股先看反抽减仓；信号价被收复后再转观察；超卖只代表跌幅充分，趋势修复仍需价格与成交量确认。',
    sources: '滚动罗盘D1首次入库信号；iWenCai 2026-08-03收盘行情、均线、MACD、RSI、筹码与资金；腾讯港股日线与HKEX证券身份。',
    signals: [
      {
        name: '深科技', symbol: '000021', market: 'A股', direction: 'SELL', nodes: '15m / 90m', signalPrices: '¥35.49 / ¥34.62', close: '¥34.51', change: '-7.31%', validation: 'confirmed', validationLabel: '空方确认',
        verdict: '收盘跌破两个空方信号，90m节点在尾盘继续确认弱势。RSI6已降至29.38，处于超卖区，交易动作以反抽减仓为主。',
        support: '¥34.40当日低点', pressure: '¥34.62—35.49信号区；MA5 ¥37.57', buyPlan: '收复¥35.49后观察，站稳MA5 ¥37.57再确认修复。', sellPlan: '反弹¥34.62—35.49减仓；跌破¥34.40继续降仓。',
        evidence: ['收盘低于90m信号0.32%', 'MA20 ¥44.03，价格偏离较大', '主力净流出3.71亿元', '获利盘1.3%']
      },
      {
        name: '华天科技', symbol: '002185', market: 'A股', direction: 'SELL', nodes: '10m / 15m', signalPrices: '¥15.03 / ¥14.94', close: '¥14.77', change: '-4.52%', validation: 'confirmed', validationLabel: '空方确认',
        verdict: '两个早盘空方节点形成同一信号簇，收盘继续位于信号区下方。RSI6 25.52与获利盘接近0%，短线超跌反抽概率升高，趋势仍弱。',
        support: '¥14.70当日低点', pressure: '¥14.94—15.03信号区；MA5 ¥15.84', buyPlan: '收复¥15.03后观察，放量站稳¥15.84再建立修复仓。', sellPlan: '反弹信号区减仓；跌破¥14.70继续防守。',
        evidence: ['收盘低于平均信号价1.43%', '量比0.67，缩量下跌', '主力净流出3.86亿元', 'MA20 ¥19.63']
      },
      {
        name: '德福科技', symbol: '301511', market: 'A股', direction: 'SELL', nodes: '10m', signalPrices: '¥63.70', close: '¥63.33', change: '-4.48%', validation: 'confirmed', validationLabel: '空方确认',
        verdict: '尾盘收在10m信号价下方，空方获得收盘确认。RSI6 24.17、获利盘0.9%，已经进入深度超跌，适合等待止跌结构。',
        support: '¥62.81当日低点', pressure: '¥63.70信号价；MA5 ¥67.26', buyPlan: '收复¥63.70后观察，站稳¥67.26并伴随资金回流再确认。', sellPlan: '反弹¥63.70—67.26分档减仓；跌破¥62.81继续降仓。',
        evidence: ['收盘低于信号价0.58%', 'MA20 ¥91.46', '主力净流出7426万元', '量比0.96']
      },
      {
        name: '东方明珠', symbol: '600637', market: 'A股', direction: 'BUY', nodes: '2.5h', signalPrices: '¥8.31', close: '¥8.30', change: '+0.97%', validation: 'watch', validationLabel: '多方观察',
        verdict: '多方信号与收盘价几乎重合，尚缺收盘突破幅度。价格已站在MA5、MA10和MA20上方，RSI6升至76.09，追高空间需要成交量配合。',
        support: '¥8.16当日低点；MA5 ¥8.10', pressure: '¥8.31信号价；¥8.35当日高点', buyPlan: '放量站稳¥8.35后确认；回踩¥8.16—8.10缩量企稳可观察。', sellPlan: '跌回MA5 ¥8.10下方减仓；失守¥8.16取消突破预期。',
        evidence: ['收盘较信号价-0.12%', '量比0.65，确认量不足', 'MACD柱为正', '主力小幅净流出766万元']
      },
      {
        name: '三安光电', symbol: '600703', market: 'A股', direction: 'SELL', nodes: '15m', signalPrices: '¥11.93', close: '¥12.22', change: '-0.16%', validation: 'reclaimed', validationLabel: '信号价已收复',
        verdict: '早盘空方信号随后被收复，收盘高于信号价2.43%。价格重回MA5与MA10上方，形成日内弱转强，但MA20 ¥13.67仍是中期压力。',
        support: '¥11.93信号价；¥11.83当日低点', pressure: '¥12.28当日高点；MA20 ¥13.67', buyPlan: '守住¥11.93并突破¥12.28可建立观察仓，站稳MA20再提高仓位。', sellPlan: '跌回¥11.93下方减仓；失守¥11.83退出短线修复。',
        evidence: ['收盘高于信号价2.43%', 'MA5 ¥12.13、MA10 ¥12.16', 'RSI6 40.18', '主力净流出4767万元']
      },
      {
        name: '海光信息', symbol: '688041', market: 'A股', direction: 'SELL', nodes: '10m / 30m', signalPrices: '¥267.94 / ¥263.20', close: '¥261.00', change: '-5.78%', validation: 'confirmed', validationLabel: '空方确认',
        verdict: '收盘跌破两个空方节点，30m节点继续确认下行。RSI6仅19.55，已处于极度超卖，短线容易出现剧烈反抽，反抽仍先看减仓。',
        support: '¥259.40当日低点', pressure: '¥263.20—267.94信号区；MA5 ¥278.74', buyPlan: '收复¥267.94后观察，站稳MA5 ¥278.74再确认趋势修复。', sellPlan: '反弹信号区减仓；跌破¥259.40继续降仓。',
        evidence: ['收盘低于30m信号0.84%', 'MA20 ¥318.36', 'MACD负柱继续扩张', '平均成本¥313.60']
      },
      {
        name: '长鑫科技', symbol: '688825', market: 'A股', direction: 'SELL', nodes: '10m', signalPrices: '¥51.46', close: '¥54.99', change: '+1.89%', validation: 'reclaimed', validationLabel: '强力反收复',
        verdict: '早盘空方信号被强势反收复，收盘高于信号价6.86%，并站上MA5。RSI6 84.06显示短线过热，适合持仓观察与回踩确认。',
        support: '¥51.46信号价；平均成本¥51.12', pressure: '¥55.98当日高点', buyPlan: '回踩¥51.46—51.12企稳可观察，放量突破¥55.98确认延续。', sellPlan: '冲高¥55.98附近滞涨可兑现；跌回¥51.46下方执行止损。',
        evidence: ['收盘高于信号价6.86%', '主力净流入6.37亿元', 'RSI6 84.06', '量比0.43，追涨需谨慎']
      },
      {
        name: '中国宏桥', symbol: '01378', market: '港股', direction: 'SELL', nodes: '10m / 15m / 30m / 60m', signalPrices: 'HK$23.16 / 23.38 / 23.10 / 22.86', close: 'HK$22.96', change: '-6.44%', validation: 'confirmed', validationLabel: '空方簇确认',
        verdict: '四个空方节点连续点亮，收盘低于平均信号价0.71%，成交量较前一交易日放大约63%。价格贴近MA20，短线进入支撑测试。',
        support: 'HK$22.66当日低点；MA20 HK$22.63', pressure: 'HK$23.10—23.38信号密集区；MA5 HK$23.67', buyPlan: '守住HK$22.63并收复HK$23.38后观察，站稳MA5再确认。', sellPlan: '反弹信号密集区减仓；跌破HK$22.63继续防守。',
        evidence: ['收盘低于平均信号价0.71%', '日内低点HK$22.66', '成交量放大1.63倍', '07/31高点HK$24.76形成上方压力']
      },
      {
        name: '澜起科技H股', symbol: '06809', market: '港股', direction: 'SELL', nodes: '10m', signalPrices: 'HK$253.80', close: 'HK$248.80', change: '-7.51%', validation: 'confirmed', validationLabel: '空方确认',
        verdict: '10m空方信号获得收盘确认，收盘低于信号价1.97%。HKEX证券身份为MONTAGE TECHNOLOGY CO., LTD. H SHARES（6809），与页面“澜起科技H股”一致。',
        support: 'HK$245.00当日低点', pressure: 'HK$253.80信号价；HK$263.40当日高点', buyPlan: '收复HK$253.80后观察，站稳HK$263.40确认修复。', sellPlan: '反弹HK$253.80—263.40减仓；跌破HK$245继续风控。',
        evidence: ['收盘低于信号价1.97%', '收盘HK$248.80', '日内高低HK$263.40 / 245.00', 'HKEX已核实证券身份']
      }
    ]
  },
  '2026-07-31': {
    tradeDate: '2026-07-31',
    shortDate: '07/31',
    title: '7月31日滚动信号收盘复盘',
    subtitle: '白银出现先多后空的方向切换，特斯拉空方观察点被收盘反收复。',
    cutoff: '2026-07-31 全球市场收市',
    summary: '白银现货10m空方与白银期货中段空方节点获得当日确认，晚间30m空方点位较低，收盘已回到其上方；特斯拉10m空方触发在日内低位附近，随后反弹并收在信号价上方。执行上，白银当日适合顺空保护利润，特斯拉适合把信号视为盘中风险提醒。',
    buyRule: '白银重新站稳$58.75后观察修复；特斯拉守住$303.62并突破$315.50后确认延续。',
    sellRule: '白银跌回$57.19下方恢复空方；特斯拉反弹受阻于$315.50—317.15可兑现。',
    discipline: '同一品种方向快速切换时，以后触发节点和收盘位置确定次日基准；观察节点用于风险提示，收盘反收复则降低空方权重。',
    sources: '滚动罗盘D1首次入库信号；Yahoo Finance SI=F与TSLA分钟/日线；新浪国际白银日线；NASDAQ历史行情。',
    signals: [
      {
        name: '白银现货', symbol: 'HF_XAG', market: '24H', direction: 'SELL', nodes: '10m', signalPrices: '$58.72', close: '$57.63', change: '-1.85%（相对信号价）', validation: 'confirmed', validationLabel: '空方确认',
        verdict: '10:50触发10m空方后，价格最低下探$57.01，收盘仍低于信号价1.85%。该节点完整捕捉了当日白银由强转弱。',
        support: '$57.01当日低点；$56.89枢轴支撑', pressure: '$58.72信号价；$59.14当日高点', buyPlan: '收复$58.72并站稳$59.14后确认修复。', sellPlan: '反弹$58.72附近受阻可减仓；跌破$57.01延续空方。',
        evidence: ['北京时间10:50触发', '当日高低$59.14 / 57.01', '收盘$57.63', 'MA20约$58.29']
      },
      {
        name: '白银期货', symbol: 'SI=F', market: 'COMEX', direction: 'MIXED', nodes: '1m多方 → 1m/15m/30m空方', signalPrices: '$58.6005 → 58.4525 / 58.1266 / 57.1890', close: '$57.59', change: '-1.72%（相对首个多方）', validation: 'mixed', validationLabel: '先多后空',
        verdict: '12:55多方节点很快被13:45空方反转，15m空方继续确认；22:00的30m空方触发价低于收盘，属于低位追空观察。收盘基准偏空，节点质量呈前强后弱。',
        support: '$57.22当日低点；$56.89枢轴支撑', pressure: '$58.13—58.60反转区；$58.75枢轴压力', buyPlan: '收复$58.60并站稳$58.75后恢复多方观察。', sellPlan: '跌破$57.19再确认空方；反弹$58.13—58.60受阻可减仓。',
        evidence: ['12:55 BUY $58.6005', '13:45 SELL $58.4525', '15:15 SELL $58.1266', '22:00 SELL $57.1890']
      },
      {
        name: '特斯拉', symbol: 'TSLA', market: '美股', direction: 'SELL', nodes: '10m观察', signalPrices: '$303.43', close: '$311.21', change: '+2.56%（相对信号价）', validation: 'reclaimed', validationLabel: '空方已收复',
        verdict: '10m空方触发接近日内低点$301.97，随后价格反弹并收于$311.21，较信号价高2.56%。该信号更适合作为盘中洗盘风险提醒。',
        support: '$303.62枢轴支撑；$301.97当日低点', pressure: '$315.50当日高点；$317.15枢轴压力', buyPlan: '守住$303.62并突破$315.50后确认多方延续。', sellPlan: '跌回$303.43下方转防守；$315.50—317.15受阻可兑现。',
        evidence: ['日内开盘$309.69', '高低$315.50 / 301.97', '收盘$311.21', '成交量约3653万股']
      }
    ]
  }
};

export const rollingDailyArticleCatalog = [
  { symbol: 'ROLLING', name: '滚动全市场', initials: 'gdqsc', tradeDate: '2026-08-03', href: '/rolling/insights/' },
  { symbol: 'ROLLING', name: '滚动全市场', initials: 'gdqsc', tradeDate: '2026-07-31', href: '/rolling/insights/2026-07-31/' },
  { symbol: '002173', name: '创新医疗', initials: 'cxyl', tradeDate: '2026-07-30', href: '/rolling/insights/2026-07-30/' },
];
