/**
 * Shared helpers for A/US momentum client apps.
 */
(function (global) {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const num = (n) => Number(n) || 0;
  const fmt = (v, digits = 2) => (Number.isFinite(Number(v)) ? Number(v).toFixed(digits) : '—');
  const cls = (v) => (Number(v) >= 0 ? 'up' : 'down');
  const up = cls;

  const tradeStateClassA = (tradeState) => (
    tradeState === '禁止追高' || tradeState === '退出' ? 'fail'
      : (tradeState === '可持有' ? 'pass' : 'watch')
  );

  const tradeStateClassUs = (tradeState) => (
    tradeState === '禁止追高' ? 'danger'
      : (tradeState === '可持有' ? 'hold' : 'watch')
  );

  const bindFilterRerender = (ids, onChange) => {
    ids.forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener('change', onChange);
    });
  };

  const bindPager = ({ prevId = 'prev-page', nextId = 'next-page', getPage, setPage, maxPage, go }) => {
    const prev = $(prevId);
    const next = $(nextId);
    if (prev) prev.addEventListener('click', () => {
      const p = getPage();
      if (p > 1) go(p - 1);
    });
    if (next) next.addEventListener('click', () => {
      const p = getPage();
      if (p < maxPage()) go(p + 1);
    });
  };

  global.EtfMomentumShared = {
    $,
    num,
    fmt,
    cls,
    up,
    tradeStateClassA,
    tradeStateClassUs,
    bindFilterRerender,
    bindPager,
  };
})(window);
