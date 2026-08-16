export function isTakeProfitSide(raw = '') {
  const text = String(raw);
  return text.includes('兑现') || text.includes('止盈');
}

export function publicHomeCopy(raw = '') {
  return String(raw)
    .replace(/22:00夜间?(最终版|最终收盘)/gi, '22:00更新')
    .replace(/夜间最终(?:版|收盘)?/g, '最新更新')
    .replaceAll('今日', '当日')
    .replace(/(?:08:30|11:30|14:30)(?:最终版|最终收盘)/gi, (value) => value.slice(0, 5) + '更新')
    .replace(/旧?plant/gi, '交易计划')
    .replace(/Plan\s*1/gi, '交易计划')
    .replace(/mid_macro/gi, '行业环境')
    .replace(/market_regime/gi, '市场状态')
    .replace(/canonical\s+position/gi, '标准仓位')
    .replace(/canonical/gi, '标准')
    .replaceAll('伏击位', '计划关注价')
    .replaceAll('兑现位', '止盈参考价')
    .replaceAll('防守线', '风险退出价')
    .replaceAll('失效线', '风险退出价')
    .replaceAll('伏击/兑现', '关注/止盈')
    .replaceAll('伏击', '关注')
    .replaceAll('兑现', '止盈')
    .replaceAll('候场', '观察')
    .replace(/最终版|最终收盘/g, '最新更新');
}
