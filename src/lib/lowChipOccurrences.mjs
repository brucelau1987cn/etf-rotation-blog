export function buildLowChipOccurrenceLookup(snapshots) {
  const ordered = [...snapshots]
    .filter((item) => item && item.date)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const states = new Map();
  const lookup = {};

  ordered.forEach((snapshot, dayIndex) => {
    const codes = new Set(snapshot.intersection || []);
    lookup[snapshot.date] = {};
    for (const code of codes) {
      const previous = states.get(code);
      const continued = previous && previous.lastDayIndex === dayIndex - 1;
      const state = {
        appearances: (previous?.appearances || 0) + 1,
        episodes: continued ? previous.episodes : (previous?.episodes || 0) + 1,
        streak: continued ? previous.streak + 1 : 1,
        lastDayIndex: dayIndex,
      };
      states.set(code, state);
      lookup[snapshot.date][code] = {
        appearances: state.appearances,
        episodes: state.episodes,
        streak: state.streak,
        isNew: state.appearances === 1,
        isReentry: !continued && state.episodes > 1,
      };
    }
  });

  return lookup;
}

export function lowChipOccurrenceLabel(meta) {
  if (!meta) return '';
  if (meta.isNew) return '新入池';
  if (meta.isReentry) return `第${meta.episodes}次入池`;
  if (meta.episodes > 1) return `第${meta.episodes}轮 · 连续${meta.streak}日`;
  return `连续${meta.streak}日`;
}

export function lowChipOccurrenceTitle(meta) {
  if (!meta) return '';
  return `累计入池${meta.appearances}日 · 共${meta.episodes}轮 · 本轮连续${meta.streak}日`;
}
