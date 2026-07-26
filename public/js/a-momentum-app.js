/**
 * A-share momentum radar client app.
 * Optional shared helpers from /js/momentum-shared.js.
 */
(function () {
  const shared = window.EtfMomentumShared || null;
const DATA_URL = '/data/a-compass-dashboard.json';
const EDGE_QUOTE_URL = '/api/public/v1/quote';
const $ = shared?.$ || ((id) => document.getElementById(id));
const adapter = window.EtfQuote || null;
let payload = null;
let rawRows = [];
let livePayload = null;
let liveTimer = null;
let liveLoading = false;
let currentPage = 1;
const fmt = shared?.fmt || ((v, digits = 2) => Number.isFinite(Number(v)) ? Number(v).toFixed(digits) : '—');
const cls = shared?.cls || ((v) => Number(v) >= 0 ? 'up' : 'down');
const stateClass = (state) => state === '极弱' || state === '防御' ? 'danger' : (state === '震荡' ? 'warn' : 'good');
const actionHint = (state) => state === '极弱' ? '控制仓位：Top5仅作观察，不代表立即加仓' : (state === '防御' ? '轻仓试探：先看承接，再分批' : (state === '震荡' ? '低吸高抛：只做强弱切换' : '趋势跟随：优先强势持有'));
const passTag = (ok) => `<span class="tag ${ok ? 'pass' : 'fail'}">${ok ? '🟢' : '🔴'}</span>`;
function applyLiveRows(rows) {
  const liveMap = new Map((livePayload?.items || []).map(x => [x.code, x]));
  return rows.map(row => {
    const live = liveMap.get(row.code);
    if (!live || !Number.isFinite(Number(live.price))) return row;
    const price = Number(live.price);
    const base3 = Number(row.ret3);
    const base10 = Number(row.ret10);
    const base20 = Number(row.ret20);
    const oldPrice = Number(row.price);
    const adjustReturn = (base) => Number.isFinite(base) && Number.isFinite(oldPrice) && oldPrice > 0
      ? (((1 + base / 100) * price / oldPrice) - 1) * 100
      : base;
    const ret3 = adjustReturn(base3);
    const ret10 = adjustReturn(base10);
    const ret20 = adjustReturn(base20);
    const ma20 = Number(row.ma20);
    const ma20Prev = Number(row.ma20_prev);
    const canRecomputeMomentum = Number.isFinite(ma20) && Number.isFinite(ma20Prev) && Number.isFinite(ret3);
    const priceAboveMa = canRecomputeMomentum ? price > ma20 : row.checks?.price_above_ma;
    const maRising = canRecomputeMomentum ? ma20 > ma20Prev : row.checks?.ma_rising;
    const shortOk = canRecomputeMomentum ? ret3 > -5 : row.checks?.short_ok;
    const momentum = canRecomputeMomentum
      ? Boolean(priceAboveMa && maRising && shortOk)
      : row.checks?.momentum;
    const status = canRecomputeMomentum
      ? (momentum ? 'core' : (row.status === 'cash' ? 'cash' : 'watch'))
      : row.status;
    return {
      ...row,
      price,
      prev_close: live.prev_close ?? row.prev_close,
      change_pct: live.change_pct ?? row.change_pct,
      high: live.high ?? row.high,
      low: live.low ?? row.low,
      quote_source: live.source || row.quote_source,
      ret3: Number.isFinite(ret3) ? Number(ret3.toFixed(2)) : row.ret3,
      ret10: Number.isFinite(ret10) ? Number(ret10.toFixed(2)) : row.ret10,
      ret20: Number.isFinite(ret20) ? Number(ret20.toFixed(2)) : row.ret20,
      status,
      checks: {
        ...(row.checks || {}),
        ...(canRecomputeMomentum ? {
          price_above_ma: priceAboveMa,
          ma_rising: maRising,
          short_ok: shortOk,
          momentum,
        } : {}),
      },
      live: true,
    };
  });
}
function buildLiveSummary(rows) {
  const core = rows.filter(r => r.status === 'core' && r.cooldown_state !== '止损观察');
  return {
    ...(payload?.summary || {}),
    universe_count: rows.length,
    valid_count: rows.filter(r => Number.isFinite(Number(r.price))).length,
    core_count: core.length,
    watch_count: rows.filter(r => r.status === 'watch').length,
    momentum_pass_count: core.length,
  };
}
const themeOf = (row) => {
  const name = String(row.name || '');
  const groups = [
    ['科技', ['半导体', '科技', '科创', '信息技术', '计算机', '软件', '人工智能', '电子', '数字经济', '通信', '机器人']],
    ['医药', ['医药', '医疗', '创新药', '生物']],
    ['防御', ['银行', '煤炭', '红利', '电力', '黄金']],
    ['消费', ['消费', '酒ETF', '家电', '旅游']],
    ['海外', ['恒生', '纳指', '标普', '道琼斯', '德国', '法国', '日经', '沙特', '印度', '东南亚']],
    ['新能源制造', ['电池', '光伏', '汽车', '电网', '工业母机', '军工', '航空航天']],
  ];
  return groups.find(([, keys]) => keys.some(key => name.includes(key)))?.[0] || row.type || '其他';
};
function buildLiveRecommendations(rows) {
  const candidates = [...rows]
    .filter(r => r.status === 'core' && Number(r.signal_score) >= 52 && ['可持有', '回踩候选', '观察'].includes(r.trade_state || r.action))
    .sort((a, b) => (Number(b.signal_score) || -Infinity) - (Number(a.signal_score) || -Infinity));
  const selected = [];
  const themes = new Set();
  for (const row of candidates) {
    const theme = themeOf(row);
    if (themes.has(theme)) continue;
    selected.push({ ...row, theme });
    themes.add(theme);
    if (selected.length >= 5) break;
  }
  return selected;
}
function quoteStateOf(generatedAt, hasLive) {
  if (!hasLive) return '基准快照';
  const match = String(generatedAt || '').match(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})/);
  if (!match) return '实时行情';
  const [, date, hour, minute] = match;
  const weekday = new Date(`${date}T12:00:00+08:00`).getUTCDay();
  if (weekday === 0 || weekday === 6) return '休市快照';
  const minutes = Number(hour) * 60 + Number(minute);
  if (minutes < 9 * 60 + 30) return '盘前快照';
  if (minutes <= 11 * 60 + 30 || (minutes >= 13 * 60 && minutes <= 15 * 60)) return '盘中行情';
  if (minutes < 13 * 60) return '午间快照';
  return '收盘快照';
}
function render() {
  const type = $('type-filter').value;
  const stateFilter = $('state-filter').value;
  const sortKey = $('sort-key').value;
  let rows = applyLiveRows(rawRows);
  rows.sort((a, b) => (Number(b[sortKey]) || -Infinity) - (Number(a[sortKey]) || -Infinity));
  const summary = buildLiveSummary(rows);
  const recommendations = buildLiveRecommendations(rows);
  if (type !== '全部') rows = rows.filter(r => r.type === type);
  if (stateFilter !== '全部') rows = rows.filter(r => (r.trade_state || r.action) === stateFilter);
  if ($('absolute-only').checked) rows = rows.filter(r => r.status === 'core' && r.cooldown_state !== '止损观察');
  const recCodes = new Set(recommendations.map(r => r.code));
  const totalAfterFilter = rows.length;
  const pageSizeValue = $('page-size').value;
  const pageSize = pageSizeValue === 'all' ? Math.max(totalAfterFilter, 1) : Number(pageSizeValue);
  const totalPages = Math.max(1, Math.ceil(totalAfterFilter / pageSize));
  currentPage = Math.min(Math.max(1, currentPage), totalPages);
  const pageStart = (currentPage - 1) * pageSize;
  const shownRows = rows.slice(pageStart, pageStart + pageSize);
  const liveTime = livePayload?.generated_at ? livePayload.generated_at.replace(' UTC+08:00', '').slice(11, 16) : null;
  const quoteState = quoteStateOf(livePayload?.generated_at, Boolean(livePayload));
  const tradeDate = payload?.latest_trade_date;
  const weak = [...rawRows].sort((a, b) => (Number(a.ret20) || Infinity) - (Number(b.ret20) || Infinity)).slice(0, 3);
  $('metric-total').textContent = `${summary.universe_count || rows.length}`;
  $('metric-pass').textContent = `${summary.core_count || rows.filter(r => r.status === 'core' && r.cooldown_state !== '止损观察').length} / ${summary.universe_count || rows.length}`;
  $('metric-rec').textContent = `${recommendations.length}`;
  $('metric-regime').textContent = payload?.market_regime?.state || '—';
  $('metric-time').textContent = `${quoteState}${liveTime ? ` ${liveTime}` : ''}`;
  const regime = payload?.market_regime || {};
  const state = regime.state || '—';
  const recNames = recommendations.slice(0, 3).map(r => `${r.code} ${r.name.replace(/ETF.*/, 'ETF')}`).join(' / ') || '空仓观察';
  $('decision-title').textContent = `${state}市场｜${recommendations.length ? '主题代表观察' : '空仓防御'}｜趋势通过 ${summary.core_count || 0}/${summary.universe_count || rows.length}`;
  $('decision-subtitle').textContent = `Top候选：${recNames}。${actionHint(state)}。`;
  $('decision-badges').innerHTML = [`<span class="badge-state ${stateClass(state)}">市场 ${state}</span>`, `<span class="badge-state">权益 ${regime.equity_allocation || '—'}</span>`, `<span class="badge-state">防御 ${regime.defense_allocation || '—'}</span>`, `<span class="badge-state">${quoteState} ${liveTime || '—'}</span>`].join('');
  $('decision-action').textContent = actionHint(state);
  $('decision-risk').innerHTML = weak.length ? weak.map(r => `${String(r.name || r.code).replace(/ETF.*/, 'ETF')} <span class="${cls(r.ret20)}">${fmt(r.ret20)}%</span>`).join(' / ') : '暂无尾部数据';
  $('decision-source').textContent = `${summary.valid_count || 0}/${summary.universe_count || rows.length}有效｜qfq ${summary.qfq_count || 0}`;
  $('date-pill').textContent = `模型 ${payload?.evaluation_date || payload?.run_date || '—'}｜行情 ${tradeDate || '—'}`;
  $('scope-note').textContent = `实时重算：${(payload?.realtime_scope || ['当前价', '收益估算', 'MA20状态']).join('、')}。快照字段：${(payload?.snapshot_scope || ['综合分', '交易风险', '交易状态', '市场状态']).join('、')}。`;
  const firstShown = totalAfterFilter ? pageStart + 1 : 0;
  const lastShown = Math.min(pageStart + shownRows.length, totalAfterFilter);
  const basis = summary.raw_fallback_count ? `，日线口径 qfq ${summary.qfq_count || 0} / 未复权备用 ${summary.raw_fallback_count}` : '';
  $('status-line').textContent = `当前显示 ${firstShown}–${lastShown}/${totalAfterFilter}只，趋势通过 ${summary.core_count || 0}只${basis}，${quoteState}${livePayload?.latency_ms ? `，延迟 ${livePayload.latency_ms}ms` : ''}`;
  $('filter-result').textContent = `结果 ${totalAfterFilter}只`;
  const rowMarkup = (r) => {
    const risks = (r.risk_flags || []).slice(0, 2).join(' / ') || '—';
    const tradeState = r.trade_state || r.action || '观察';
    return `<tr class="${recCodes.has(r.code) ? 'recommended' : ''}"><td><strong>${r.name}</strong>${recCodes.has(r.code) ? ' <span class="tag pass">代表</span>' : ''}</td><td>${r.code}</td><td>${r.type}</td><td><strong>${r.strength_level || '—'} / ${fmt(r.signal_score)}</strong></td><td><span class="tag ${tradeState === '禁止追高' || tradeState === '退出' ? 'fail' : (tradeState === '可持有' ? 'pass' : 'watch')}">${tradeState}</span></td><td class="${cls(r.ret5)}">${fmt(r.ret5)}%</td><td class="${cls(r.ret20)}">${fmt(r.ret20)}%</td><td>${fmt(Number(r.close_position) * 100, 0)}%</td><td><strong>${r.risk_level || '—'}</strong> / ${fmt(r.trading_risk_score, 0)}</td><td>${risks}</td></tr>`;
  };
  $('etf-body').innerHTML = shownRows.map(rowMarkup).join('') || '<tr><td colspan="10">当前筛选下无ETF。</td></tr>';
  $('mobile-pool').innerHTML = shownRows.map(r => {
    const tradeState = r.trade_state || r.action || '观察';
    const risks = (r.risk_flags || []).slice(0, 2).join(' / ') || '暂无风险提示';
    return `<article class="mobile-etf"><div class="mobile-etf-head"><div><h3>${r.code} · ${r.name}</h3><p>${r.type} · ${risks}</p></div><span class="tag ${tradeState === '禁止追高' || tradeState === '退出' ? 'fail' : (tradeState === '可持有' ? 'pass' : 'watch')}">${tradeState}</span></div><div class="mobile-etf-grid"><div><span>趋势</span><b>${r.strength_level || '—'} / ${fmt(r.signal_score)}</b></div><div><span>交易风险</span><b>${r.risk_level || '—'} / ${fmt(r.trading_risk_score, 0)}</b></div><div><span>5日</span><b class="${cls(r.ret5)}">${fmt(r.ret5)}%</b></div><div><span>20日</span><b class="${cls(r.ret20)}">${fmt(r.ret20)}%</b></div><div><span>收盘位置</span><b>${fmt(Number(r.close_position) * 100, 0)}%</b></div><div><span>现价</span><b>${fmt(r.price, 3)}</b></div></div></article>`;
  }).join('') || '<p>当前筛选下无ETF。</p>';
  $('page-info').textContent = pageSizeValue === 'all' ? `显示全部 ${totalAfterFilter}只` : `显示 ${firstShown}–${lastShown} / 共${totalAfterFilter}只 · 第${currentPage}/${totalPages}页`;
  $('prev-page').disabled = currentPage <= 1;
  $('next-page').disabled = currentPage >= totalPages;
  $('page-numbers').innerHTML = pageSizeValue === 'all' ? '' : Array.from({ length: totalPages }, (_, i) => i + 1).map(page => `<button type="button" data-page="${page}" class="${page === currentPage ? 'active' : ''}" aria-label="第${page}页">${page}</button>`).join('');
  $('page-numbers').querySelectorAll('button').forEach(button => button.addEventListener('click', () => goPage(Number(button.dataset.page))));
  $('recommendations').innerHTML = recommendations.map((r, i) => {
    const scores = r.agent_scores || {};
    const bull = (r.agent_bull || []).slice(0, 2).join('；');
    const bear = (r.agent_bear || []).slice(0, 2).join('；');
    const tradeState = r.trade_state || r.action || '观察';
    return `<div class="rec-card"><div class="rec-top"><h3>${i + 1}. ${r.code} ${r.name}</h3><div class="rec-code">趋势 ${r.strength_level || '—'} / ${fmt(r.signal_score)}｜交易风险 ${r.risk_level || '中'} / ${fmt(r.trading_risk_score, 0)}</div></div><p class="rec-metrics"><span><strong>5日：</strong><span class="${cls(r.ret5)}">${fmt(r.ret5)}%</span>　<strong>20日：</strong><span class="${cls(r.ret20)}">${fmt(r.ret20)}%</span></span><span><strong>状态：</strong>${tradeState}　<strong>位置：</strong>${fmt(Number(r.close_position) * 100, 0)}%</span></p><div class="agent-box"><strong>看点：</strong>${bull || '相对强势观察'}；<strong>风险：</strong>${bear || '暂无'}</div><div class="rec-tags"><span class="tag ${tradeState === '禁止追高' || tradeState === '退出' ? 'fail' : (tradeState === '可持有' ? 'pass' : 'watch')}">${tradeState}</span><span class="tag ${r.risk_level === '高' ? 'fail' : 'watch'}">${r.risk_level || '中'}风险</span><span class="tag">${r.theme || themeOf(r)}</span></div></div>`;
  }).join('') || '<p class="muted">当前无ETF满足趋势强度与交易风险双重准入，按空仓/防御策略处理。</p>';
  $('today-notes').innerHTML = [
    `<li><strong>ETF罗盘 91 池：</strong>覆盖宽基/海外/行业/商品/LOF 五大类，评估日期 ${payload?.evaluation_date || '—'}。</li>`,
    recommendations.length ? `<li><strong>操作结论：</strong>${state}市场，${actionHint(state)}。</li><li><strong>Top观察：</strong>${recommendations.map(r => `${r.code} ${r.name}(${r.action || '观察'})`).join(' / ')}。</li>` : '<li><strong>空仓策略：</strong>绝对动量为空时优先十年国债ETF 511260、公司债ETF 511110与黄金ETF 518880。</li>',
    weak.length ? `<li><strong>弱势尾部：</strong>${weak.map(r => `${r.name} <span class="${cls(r.ret20)}">${fmt(r.ret20)}%</span>`).join('，')}。</li>` : '',
    `<li><strong>市场状态：</strong>${payload?.market_regime?.state || '—'}，权益仓 ${payload?.market_regime?.equity_allocation || '—'}，防御仓 ${payload?.market_regime?.defense_allocation || '—'}。</li>`,
    '<li><strong>本站口径：</strong>趋势强度、交易风险和交易状态分层展示；盘中行情只更新可实时重算字段。</li>'
  ].join('');
}
function goPage(page) {
  currentPage = page;
  render();
  document.querySelector('.data-details').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
}
async function loadLive() {
  if (!payload || liveLoading || document.hidden) return;
  liveLoading = true;
  try {
    if (!adapter?.aShareSymbolsParam || !adapter?.normalizeQuotePayload) {
      throw new Error('EtfQuote adapter missing');
    }
    const codes = [...new Set((rawRows || []).map((row) => row.code).filter(Boolean))];
    if (!codes.length) return;
    // Edge caps batch size; chunk to stay under 50 symbols per request.
    const chunkSize = 45;
    const items = [];
    let source = 'Cloudflare-Edge';
    let generatedAt = null;
    for (let i = 0; i < codes.length; i += chunkSize) {
      const chunk = codes.slice(i, i + chunkSize);
      const symbols = adapter.aShareSymbolsParam(chunk);
      const res = await fetch(`${EDGE_QUOTE_URL}?symbols=${encodeURIComponent(symbols)}&t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`Edge HTTP ${res.status}`);
      const normalized = adapter.normalizeQuotePayload(await res.json());
      if (!normalized.ok || !normalized.items.length) continue;
      source = normalized.source || source;
      generatedAt = normalized.generated_at || generatedAt;
      for (const item of normalized.items) {
        if (!(Number(item.price) > 0)) continue;
        items.push({
          code: item.code || item.symbol,
          price: item.price,
          prev_close: item.prev_close,
          change_pct: item.change_pct ?? item.change_percent,
          high: item.high,
          low: item.low,
          source: item.source || source,
        });
      }
    }
    if (!items.length) throw new Error('Edge 返回空/无效价格');
    livePayload = {
      ok: true,
      generated_at: generatedAt || new Date().toISOString(),
      source,
      items,
    };
    render();
  } catch (err) {
    $('status-line').innerHTML = `<span class="error">实时行情暂时不可用：${err.message}；已保留最新静态快照。</span>`;
  } finally {
    liveLoading = false;
  }
}
async function load() {
  $('refresh-btn').disabled = true;
  $('refresh-btn').setAttribute('aria-busy', 'true');
  $('refresh-btn').textContent = '刷新中…';
  $('status-line').textContent = '正在读取 ETF罗盘 91 池基准快照…';
  try {
    payload = await fetch(`${DATA_URL}?t=${Date.now()}`).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    rawRows = payload.all_rows || [...(payload.core_pool || []), ...(payload.watch_pool || [])];
    render();
    void loadLive();
    if (!liveTimer) {
      if (window.EtfLivePoll?.startLivePoll) {
        liveTimer = window.EtfLivePoll.startLivePoll({ intervalMs: 60000, immediate: false, tick: loadLive });
      } else {
        liveTimer = setInterval(loadLive, 60000);
        document.addEventListener('visibilitychange', () => {
          if (!document.hidden) void loadLive();
        });
      }
    }
  } catch (err) {
    $('status-line').innerHTML = `<span class="error">加载失败：${err.message}</span>`;
    $('etf-body').innerHTML = `<tr><td colspan="10">91池加载失败，请稍后刷新。</td></tr>`;
    $('mobile-pool').innerHTML = '<p>91池加载失败，请点击“刷新数据”重试。</p>';
    $('page-info').textContent = '数据不可用';
  } finally {
    $('refresh-btn').disabled = false;
    $('refresh-btn').removeAttribute('aria-busy');
    $('refresh-btn').textContent = '刷新行情';
  }
}
['type-filter', 'state-filter', 'sort-key', 'absolute-only', 'page-size'].forEach(id => $(id).addEventListener('change', () => { currentPage = 1; render(); }));
$('prev-page').addEventListener('click', () => { if (currentPage > 1) goPage(currentPage - 1); });
$('next-page').addEventListener('click', () => goPage(currentPage + 1));
$('refresh-btn').addEventListener('click', load);
load();

})();
