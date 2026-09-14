import { pinyin } from 'pinyin-pro';

export function stockNameInitials(name) {
  return pinyin(String(name || '').trim(), {
    pattern: 'first',
    toneType: 'none',
    type: 'array',
  }).join('').toLowerCase().replace(/[^a-z0-9]/g, '');
}
