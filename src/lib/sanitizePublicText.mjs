/**
 * Public-facing article text sanitizer.
 * Strips internal tool / vendor pipeline names from blog and page copy.
 * Keeps generic market terms (行情/资金/资讯/前复权).
 */

/** @param {string} text */
export function renameCompassTerms(text) {
  if (!text) return text;
  return String(text)
    .replaceAll('观察不种', '仅观察不升级')
    .replaceAll('准备种花', '候场')
    .replaceAll('准备摘花', '止盈观察')
    .replaceAll('失效退出', '破位撤退')
    .replaceAll('07:30早盘版', '08:30盘前版')
    .replaceAll('07:30 早盘预测', '08:30 盘前预测')
    .replaceAll('07:30早盘预测', '08:30盘前预测')
    .replaceAll('07:30准备信号', '08:30准备信号')
    .replaceAll('ETF花园', 'ETF罗盘')
    .replaceAll('花园信号', '罗盘信号')
    .replaceAll('回踩位', '伏击位')
    .replaceAll('目标位', '兑现位')
    .replaceAll('失效线', '防守线')
    .replaceAll('种花', '伏击')
    .replaceAll('摘花', '兑现');
}

/** @param {string} text */
export function stripToolNames(text) {
  if (!text) return text;
  let s = String(text);

  // Protect real URLs so link hrefs / visible links stay valid.
  /** @type {string[]} */
  const urls = [];
  s = s.replace(/https?:\/\/[^\s)）\]"'<>]+/g, (m) => {
    urls.push(m);
    return `__URL_TOKEN_${urls.length - 1}__`;
  });

  /** @type {Array<[RegExp, string]>} */
  const pairs = [
    // MX family
    [/MX-Skills\s*mx-data/gi, '资金数据'],
    [/MX-Skills\s*f\d+/gi, '资金复核'],
    [/MX-Skills/gi, '资金数据'],
    [/MX-Search/gi, '资讯检索'],
    [/MX资金终检/g, '资金终检'],
    [/MX资金/g, '资金'],
    [/\bmx-data\b/gi, '资金数据'],
    [/\bmx_data\b/gi, '资金数据'],
    [/\bmx-skills\b/gi, '资金数据'],
    [/\bmx_skills\b/gi, '资金数据'],
    [/\bmx-search\b/gi, '资讯检索'],
    [/\bMX\b(?=\s*(资金|行情|表|交叉))/g, ''],

    // iWenCai / hithink
    [/iWenCai\s*hithink-market-query/gi, '指数交叉'],
    [/iWenCai\s*`?news-search`?/gi, '新闻检索'],
    [/同花顺问财/g, '公开资讯'],
    [/问财近1日/g, '近1日资讯'],
    [/问财结果/g, '资讯结果'],
    [/问财/g, '公开资讯'],
    [/iWenCai/gi, '公开资讯'],
    [/\biwencai\b/gi, '公开资讯'],
    [/hithink-market-query/gi, '指数交叉'],
    [/hithink-usstock-selector/gi, '候选筛选'],
    [/\bhithink\b/gi, '公开资讯'],

    // stock-api / price query stack
    [/stock-api(?:@[\d.]+)?(?:\s*package)?(?:\s*v?[\d.]+)?/gi, '行情接口'],
    [/stock-price-query/gi, '行情查询'],
    [/stock_api/gi, '行情接口'],
    [/stock-api\s*MCP/gi, '行情接口'],
    [/MCP\s*\/\s*Tencent/gi, '公开行情'],
    [/package\s*v?\d+\.\d+\.\d+/gi, ''],

    // Tencent endpoints / raw hosts (URLs already tokenized)
    [/web\.ifzq\.gtimg\.cn/gi, '公开日K'],
    [/qt\.gtimg\.cn/gi, '公开行情'],
    [/gtimg\.cn/gi, '公开行情'],
    [/腾讯\s*stock-price-query/g, '公开行情'],
    [/腾讯行情快照/g, '公开行情快照'],
    [/腾讯日K接口/g, '公开日K'],
    [/腾讯日K/g, '公开日K'],
    [/腾讯\/东方财富\/新浪/g, '多源公开行情'],
    [/腾讯\/东方财富/g, '公开行情'],
    [/stock-api\s*与腾讯/gi, '公开行情'],
    [/stock-api与腾讯/gi, '公开行情'],
    [/Tencent\s*行情/gi, '公开行情'],
    [/\bTencent\b/g, '公开行情'],
    [/腾讯行情/g, '公开行情'],
    [/腾讯交叉/g, '多源交叉'],
    [/腾讯/g, '公开行情'],

    // Futu / moomoo / Yahoo — names only; URLs protected
    [/moomoo\/Futu\s*OpenD/gi, '港股行情通道'],
    [/Futu\s*OpenD/gi, '港股行情通道'],
    [/OpenD/g, '港股行情通道'],
    [/moomoo/gi, '港股行情'],
    [/##\s*Futu\/牛牛新闻复查/gi, '## 资讯复查'],
    [/Futu\/牛牛/gi, '资讯'],
    [/富途新闻检索/g, '资讯检索'],
    [/富途新闻/g, '资讯'],
    [/富途/g, '资讯'],
    [/\bFutu\b/gi, '资讯通道'],
    [/futunn\.com/gi, '公开资讯站'],
    [/yfinance/gi, '美股行情'],
    [/Yahoo\s*Finance/gi, '美股行情'],
    [/\bYahoo\b/g, '美股行情'],

    // Internal paths / skill names
    [/public\/data\/etf-garden-pool\.json/g, '本地A股池快照'],
    [/etf-garden-pool\.json/g, '本地A股池快照'],
    [/us-etf-garden\.json/g, '本地美股池快照'],
    [/us-etf-compass\.db/g, '本地美股日K库'],
    [/named-key\s*表/g, '资金表'],
    [/rawTable/g, '资金表'],
    [/shadow_research_only/g, '影子研究层'],
    [/production_weights_changed\s*=\s*false/g, '生产权重未变'],
    [/Bruce ETF Trend Radar v3/g, 'ETF趋势雷达 v3'],
    [/A ETF Garden Levels v\d+/gi, 'A股罗盘关键位模型'],
    [/US ETF Garden/gi, 'US ETF Compass'],
    [/Flower Signals/gi, 'Compass Signals'],

    // Soften leftovers
    [/底层(?:公开)?行情\/?日K/g, '公开行情与日K'],
    [/底层腾讯公开行情/g, '公开行情'],
    [/底层腾讯/g, '公开'],
    [/底层公开行情\s*公开行情/g, '公开行情'],
    [/底层公开行情/g, '公开行情'],
    [/公开行情\s*公开行情/g, '公开行情'],
    [/行情接口\s*\/\s*行情查询/g, '公开行情'],
    [/行情接口\s*与\s*公开行情/g, '公开行情'],
    [/行情接口\s*公开日K/g, '公开日K'],
    [/公开 公开行情/g, '公开行情'],
    [/公开公开/g, '公开'],
    [/轮询池/g, '数据池'],
    [/A股港股行情通道/g, 'A股行情通道'],
  ];

  for (const [re, rep] of pairs) {
    s = s.replace(re, rep);
  }

  // Restore URLs
  s = s.replace(/__URL_TOKEN_(\d+)__/g, (_, i) => urls[Number(i)] || '');

  s = s
    .replace(/\(\s*\)/g, '')
    .replace(/（\s*）/g, '')
    .replace(/\s{2,}/g, ' ')
    .replace(/[，,]\s*[，,]/g, '，')
    .replace(/；\s*；/g, '；')
    .replace(/\s+([，。；：、])/g, '$1')
    .trim();

  return s;
}

/** Full public-facing normalize for articles and UI copy. */
export function sanitizePublicText(text) {
  return stripToolNames(renameCompassTerms(text));
}
