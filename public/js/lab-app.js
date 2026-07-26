/**
 * ETF Compass lab client: auth gate, research panels, upload jobs.
 */
    const welcome = document.querySelector('#welcome'); const status = document.querySelector('#status'); const jobs = document.querySelector('#jobs');
    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));
    const signed = (value, suffix) => { if (value === null || value === undefined || value === '') return '—'; const number = Number(value); if (!Number.isFinite(number)) return '—'; const clean = Math.abs(number) < 0.005 ? 0 : number; return `${clean > 0 ? '+' : ''}${clean.toFixed(2)}${suffix}`; };
    const pct = value => signed(value, '%');
    const pp = value => signed(value, '个百分点');
    const whole = value => { if (value === null || value === undefined || value === '') return '—'; const number = Number(value); return Number.isInteger(number) && number >= 0 ? String(number) : '—'; };
    async function loadAudit() {
      const box = document.querySelector('#audit-content');
      try {
        const response = await fetch(`/data/model-lab/a-share-research-audit.json?cb=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json(), dataset = data.dataset || {}, provenance = dataset.provenance || {}, walk = data.walk_forward || {}, aggregate = walk.aggregate || {}, cluster = aggregate.date_cluster_bootstrap_95ci || {}, execution = data.execution_audit || {}, blockers = execution.blockers || {}, chip = data.chip_poc || {};
        const blockerValue = item => item?.status === 'known' ? whole(item.count) : '未知';
        const range = values => Array.isArray(values) && values.length === 2 ? `${pct(values[0])}—${pct(values[1])}` : '—';
        const foldRows = (walk.folds || []).map(fold => `<div class="audit-row"><strong>${escapeHtml(fold.test_start)}—${escapeHtml(fold.test_end)}</strong><span>样本 ${escapeHtml(whole(fold.test?.count))} · 命中 ${escapeHtml(pct(fold.test?.hit_rate_pct))} · 方向收益 ${escapeHtml(pct(fold.test?.average_directional_return_pct))} · 相对训练退化 ${escapeHtml(pp(fold.hit_rate_degradation_pp))}</span></div>`).join('');
        const chipMain = chip.status === 'evaluated' ? `${whole(chip.eligible_symbols)}只已评估` : '数据质量阻断';
        box.innerHTML = `<div class="audit-grid">
          <article class="audit-card"><span>冻结数据指纹</span><strong><code>${escapeHtml(String(dataset.value || '').slice(0,16))}</code></strong><small>${escapeHtml(whole(dataset.record_count))}条方向记录＋${escapeHtml(whole(dataset.pool_row_count))}条当前池｜截至 ${escapeHtml(dataset.as_of || '—')}</small></article>
          <article class="audit-card"><span>滚动历史时间切片</span><strong>${escapeHtml(pct(aggregate.equal_date_weighted_hit_rate_pct))}</strong><small>${escapeHtml(whole(aggregate.trade_date_count))}个唯一交易日 · ${escapeHtml(whole(aggregate.oos_count))}条 · 日等权收益 ${escapeHtml(pct(aggregate.equal_date_weighted_return_pct))} · 命中95%区间 ${escapeHtml(range(cluster.hit_rate_pct))}</small></article>
          <article class="audit-card warning"><span>执行数据审计</span><strong>${escapeHtml(blockerValue(blockers.invalid_levels))}条可执行关键位异常</strong><small>非执行状态不适用 ${escapeHtml(blockerValue(blockers.model_not_applicable_for_trade_state))} · 陈旧 ${escapeHtml(blockerValue(blockers.stale_rows))} · 数据未知 ${escapeHtml(blockerValue(blockers.unknown_market_data))}</small></article>
          <article class="audit-card warning"><span>换手衰减筹码POC</span><strong>${escapeHtml(chipMain)}</strong><small>${escapeHtml(chip.reason || chip.caveat || '仅作研究旁路')}</small></article>
        </div><details class="audit-details"><summary>查看分折、阻断与复现口径</summary><div class="audit-detail-body">
          ${foldRows || '<div class="audit-row"><strong>历史时间分折</strong><span>历史样本仍在积累。</span></div>'}
          <div class="audit-row"><strong>日期聚类区间</strong><span>命中 ${escapeHtml(range(cluster.hit_rate_pct))}；方向收益 ${escapeHtml(range(cluster.average_directional_return_pct))}；${escapeHtml(walk.limitation || '')}</span></div>
          <div class="audit-row"><strong>盘中严格数据</strong><span>等待收盘确认：${escapeHtml(blockerValue(blockers.pending_close_confirmation))}；缺少严格5分钟：${escapeHtml(blockerValue(blockers.missing_strict_5m_bars))}。运行时数据未进入静态快照时保持“未知”。</span></div>
          <div class="audit-row"><strong>复现口径</strong><span>${escapeHtml(provenance.adjustment || '—')}；${escapeHtml(provenance.execution_basis || '—')}；账户级成本尚未计入方向标签评价。</span></div>
          <div class="audit-row"><strong>推广门禁</strong><span>${(data.promotion_gate?.requirements || []).map(escapeHtml).join('；')}</span></div>
        </div></details><p class="audit-note">该区只做数据质量、历史时间切片稳定性和研究旁路审计。正式动作、权重、关键位、仓位及模拟盘规则保持原状态。</p>`;
      } catch (error) { box.innerHTML = `<div class="empty">研究审计快照读取失败：${escapeHtml(error?.message || error)}</div>`; }
    }
    async function loadResearch() {
      const box = document.querySelector('#research-content');
      try {
        const response = await fetch(`/data/model-lab/a-share-shadow.json?cb=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json(); const feature = data.signal_enhancement;
        if (!feature) { box.innerHTML = '<div class="empty">研究增强快照尚未生成。</div>'; return; }
        const s = feature.summary, v = feature.historical_validation, br = s.break_retest;
        const priority = item => { const x = item.signal_enhancement; return x.atr_defense.state === 'breached' ? 0 : x.rsi_take_profit.state === 'cooling_trigger' ? 1 : x.break_retest.state === 'armed' ? 2 : x.atr_defense.state === 'near' ? 3 : 4; };
        const selected = (data.items || []).filter(item => { const x = item.signal_enhancement; return x && (x.break_retest.state === 'armed' || x.atr_defense.state !== 'above' || ['overheated','cooling_trigger'].includes(x.rsi_take_profit.state)); }).sort((a,b) => priority(a) - priority(b)).slice(0, 12);
        const rows = selected.map(item => { const x = item.signal_enhancement, tags = [];
          if (x.break_retest.state === 'armed') tags.push('<span class="tag buy">回踩候场</span>');
          if (x.atr_defense.state === 'breached') tags.push('<span class="tag sell">ATR跌破</span>'); else if (x.atr_defense.state === 'near') tags.push('<span class="tag sell">接近ATR防守</span>');
          if (x.rsi_take_profit.state === 'overheated') tags.push('<span class="tag sell">Z-score过热</span>'); else if (x.rsi_take_profit.state === 'cooling_trigger') tags.push('<span class="tag sell">Z-score回落</span>');
          tags.push(`<span class="tag">多周期 ${escapeHtml(x.multi_timeframe.score ?? '—')}</span>`);
          return `<div class="watch-row"><strong>${escapeHtml(item.symbol)} · ${escapeHtml(item.name)}</strong><div class="tags">${tags.join('')}</div></div>`;
        }).join('');
        box.innerHTML = `<div class="research-grid">
          <article class="research-card buy"><span>多周期强一致</span><strong>${escapeHtml(whole(s.multi_timeframe.strong_bullish))}只</strong><small>日/周/60日已收盘信号投票</small></article>
          <article class="research-card sell"><span>ATR动态防守</span><strong>${escapeHtml(whole(s.atr_defense.breached))}跌破 · ${escapeHtml(whole(s.atr_defense.near))}接近</strong><small>2×ATR单向跟踪，当前角色：旁路观察</small></article>
          <article class="research-card sell"><span>RSI Z-score止盈</span><strong>${escapeHtml(whole(s.rsi_take_profit.overheated))}过热 · ${escapeHtml(whole(s.rsi_take_profit.cooling_trigger))}回落</strong><small>RSI14相对20期分布，阈值 +2</small></article>
          <article class="research-card buy"><span>突破回踩状态机</span><strong>${escapeHtml(whole(br.armed))}候场 · 已决胜率${escapeHtml(pct(br.win_rate))}</strong><small>确认 ${escapeHtml(whole(br.historical_confirmed))}；已决 ${escapeHtml(whole(br.decided_count))}；到期 ${escapeHtml(whole(br.outcome_expired))}；开放 ${escapeHtml(whole(br.outcome_open))}</small></article>
        </div><div class="validation">
          <div>强一致组未来5日<b>${escapeHtml(pct(v.multi_timeframe.strong_alignment.average_pct))} · 相对提升 ${escapeHtml(pp(v.multi_timeframe.average_return_lift_pct))}</b></div>
          <div>RSI过热回落后5日<b>${escapeHtml(pct(v.rsi_cooling.average_pct))} · 样本 ${escapeHtml(whole(v.rsi_cooling.count))}</b></div>
          <div>ATR跌破后5日<b>${escapeHtml(pct(v.atr_breach.average_pct))} · 样本 ${escapeHtml(whole(v.atr_breach.count))}</b></div>
        </div><p class="research-note">历史覆盖 ${escapeHtml(whole(feature.coverage?.symbols_at_least_260))}/${escapeHtml(whole(feature.coverage?.symbols))}只达到260根；统计使用ETF×日期重叠观察值，交易成本将在推广闸门阶段纳入。</p>${rows ? `<div class="watch-list">${rows}</div>` : '<div class="empty">当前没有旁路关注项。</div>'}`;
      } catch (error) { box.innerHTML = `<div class="empty">研究快照读取失败：${escapeHtml(error?.message || error)}</div>`; }
    }
    async function loadFuturePath() {
      const box = document.querySelector('#kronos-content');
      try {
        const response = await fetch(`/data/model-lab/a-share-path-shadow.json?cb=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json(), coverage = data.coverage || {}, summary = data.summary || {};
        const ranked = [...(data.items || [])].sort((a,b) => Number(b.five_day?.predicted_return_pct) - Number(a.five_day?.predicted_return_pct));
        const selected = [...ranked.slice(0,5), ...ranked.slice(-5)].filter((item,index,list) => list.findIndex(other => other.symbol === item.symbol) === index);
        const rows = selected.map(item => { const value = Number(item.five_day?.predicted_return_pct), tone = value > 0 ? 'market-up-text' : value < 0 ? 'market-down-text' : '', quality = item.quality?.raw_ohlc_valid ? '' : '<span class="quality-warning">OHLC校验提醒</span>';
          return `<div class="kronos-row"><strong>${escapeHtml(item.symbol)} · ${escapeHtml(item.name)}</strong><span class="kronos-return ${tone}">${pct(value)}</span><span class="kronos-range">路径低/高 ${pct(item.five_day?.path_low_pct)} / ${pct(item.five_day?.path_high_pct)} ${quality}</span></div>`;
        }).join('');
        box.innerHTML = `<div class="kronos-grid">
          <article class="kronos-card"><span>预测覆盖</span><strong>${escapeHtml(whole(coverage.predicted_symbols))}/${escapeHtml(whole(coverage.expected_symbols))}</strong><small>正式轮动ETF · 数据日 ${escapeHtml(data.latest_trade_date)}</small></article>
          <article class="kronos-card"><span>未来5日方向</span><strong><span class="market-up-text">${escapeHtml(whole(summary.bullish_symbols))}正</span> · <span class="market-down-text">${escapeHtml(whole(summary.bearish_symbols))}负</span></strong><small>只统计模型路径终点方向</small></article>
          <article class="kronos-card"><span>中位预测收益</span><strong class="${Number(summary.median_predicted_return_pct) >= 0 ? 'market-up-text' : 'market-down-text'}">${escapeHtml(pct(summary.median_predicted_return_pct))}</strong><small>截面中位数 · horizon 5个交易日</small></article>
          <article class="kronos-card"><span>原始OHLC质量</span><strong>${escapeHtml(whole(coverage.raw_ohlc_valid_symbols))}/${escapeHtml(whole(coverage.predicted_symbols))}</strong><small>异常路径保留原值并提示，未用于正式信号</small></article>
        </div><div class="kronos-list">${rows}</div><p class="kronos-note">输入最近${escapeHtml(whole(data.model?.parameters?.lookback))}根OHLC；未来交易日：${(data.forecast_definition?.future_sessions || []).map(escapeHtml).join('、')}。当前输出是一条固定参数的研究路径，用于旁路观察、历史积累和后续点时账本验证；正式动作、权重、关键位与模拟盘规则保持原状态。</p>`;
      } catch (error) { box.innerHTML = `<div class="empty">未来路径快照读取失败：${escapeHtml(error?.message || error)}</div>`; }
    }
    async function load() {
      const me = await fetch('/api/auth/me'); if (!me.ok) { location.href = '/login/'; return; }
      const user = await me.json(); welcome.textContent = `已登录：${user.user.username} · ${user.user.role === 'admin' ? '管理员' : '用户'}`;
      loadAudit();
      loadResearch();
      loadFuturePath();
      const response = await fetch('/api/jobs'); const data = await response.json(); if (!response.ok) { jobs.textContent = data.error || '任务读取失败'; return; }
      const statusLabel = value => ({queued:'排队中',processing:'处理中',completed:'已完成',failed:'失败',waiting_close:'等待收盘'})[value] || value;
      jobs.innerHTML = data.jobs.length ? data.jobs.map(job => {
        const result = job.result || {}, summary = result.by_category || {}, quality = result.data_quality || {};
        const labels = [['candidate','候场'],['伏击','伏击'],['止盈观察','止盈观察'],['兑现','兑现']].map(([key,label]) => {
          const item = summary[key]; if (!item || !Number(item.count)) return '';
          const target = `触价 ${whole(item.target_hit)}/${whole(item.target_samples ?? item.count)}`;
          const confirmed = key === '伏击' ? ` · 收盘确认 ${Number(item.confirmation_samples) ? `${whole(item.confirmed)}/${whole(item.confirmation_samples)}` : '待定'}` : '';
          return `<span>${escapeHtml(label)} ${escapeHtml(whole(item.count))}只 · ${escapeHtml(target)}${escapeHtml(confirmed)} · T+1 ${escapeHtml(pct(item.t1_hit_rate))}</span>`;
        }).join('');
        const qualityLabels = result.version === 'strict_intraday_v2' ? `<div class="quality"><span>严格5分钟 ${escapeHtml(whole(quality.m5_final ?? 0))}</span>${Number(quality.m5_partial) ? `<span class="warning">盘中待收盘 ${escapeHtml(whole(quality.m5_partial))}</span>` : ''}${Number(quality.pending_intraday) ? `<span class="warning">等待下午行情 ${escapeHtml(whole(quality.pending_intraday))}</span>` : ''}${Number(quality.daily_fallback) ? `<span class="warning">日线粗略回退 ${escapeHtml(whole(quality.daily_fallback))}</span>` : ''}</div>` : (result.version ? '<div class="quality"><span class="warning">旧版日线预回测</span></div>' : '');
        const method = result.methodology ? `<details class="method"><summary>查看回测口径</summary><p>${escapeHtml(result.methodology)}</p></details>` : '';
        return `<div class="job"><strong>${escapeHtml(job.filename)}</strong><span class="job-status">${escapeHtml(statusLabel(job.status))}</span><span class="meta">日期：${escapeHtml(job.trade_date || result.trade_date || '待识别')}</span><span class="meta">提交：${escapeHtml(job.created_at)}</span>${labels ? `<div class="summary">${labels}</div>` : ''}${qualityLabels}${method}${job.error_message ? `<div class="summary"><span>错误：${escapeHtml(job.error_message)}</span></div>` : ''}</div>`;
      }).join('') : '<div class="empty">还没有上传任务。</div>';
    }
    document.querySelector('#upload-form').addEventListener('submit', async event => { event.preventDefault(); status.textContent = '上传中…'; const response = await fetch('/api/upload', { method: 'POST', body: new FormData(event.currentTarget) }); const data = await response.json().catch(() => ({})); status.textContent = response.ok ? `已排队：${data.filename}（${data.count}行）` : (data.error || '上传失败'); if (response.ok) { event.currentTarget.reset(); load(); } });
    document.querySelector('#logout').addEventListener('click', async () => { await fetch('/api/auth/logout', { method: 'POST' }); location.href = '/login/'; });
    load();
