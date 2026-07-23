export const BUY_CYCLES = [
  ['●', '预备', 105, '1小时45分钟'],
  ['A', 'A', 120, '2小时'],
  ['A15', 'A', 135, '2小时15分钟'],
  ['A30', 'A', 150, '2小时30分钟'],
  ['A45', 'A', 165, '2小时45分钟'],
  ['B', 'B', 180, '3小时'],
  ['B15', 'B', 195, '3小时15分钟'],
  ['B30', 'B', 210, '3小时30分钟'],
  ['B45', 'B', 225, '3小时45分钟'],
  ['C', 'C', 240, '4小时'],
  ['C15', 'C', 255, '4小时15分钟'],
  ['C30', 'C', 270, '4小时30分钟'],
  ['C45', 'C', 285, '4小时45分钟'],
  ['D', 'D', 300, '5小时'],
  ['D15', 'D', 315, '5小时15分钟'],
  ['D30', 'D', 330, '5小时30分钟'],
  ['D45', 'D', 345, '5小时45分钟'],
  ['E', 'E', 360, '6小时'],
  ['E15', 'E', 375, '6小时15分钟'],
  ['E30', 'E', 390, '6小时30分钟'],
  ['E45', 'E', 405, '6小时45分钟'],
  ['F', 'F', 420, '7小时'],
  ['F15', 'F', 435, '7小时15分钟'],
  ['F30', 'F', 450, '7小时30分钟'],
  ['F45', 'F', 465, '7小时45分钟'],
  ['G', 'G', 480, '8小时']
];

export function validatePublicPayload(payload) {
  if (!payload || typeof payload !== 'object') throw new Error('payload must be an object');
  if (payload.schema_version !== 'a-rolling-energy-v2') throw new Error('unsupported schema_version');
  if (!Array.isArray(payload.buy_cycles)) throw new Error('buy_cycles must be an array');
  return payload;
}

export function projectUpstream(upstream, generatedAt = new Date().toISOString(), staleAfterSeconds = 900) {
  return upstream;
}

export function asLkg(payload, reason) {
  return payload;
}
