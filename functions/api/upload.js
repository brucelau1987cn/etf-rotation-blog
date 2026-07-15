import { csvTradeDate, json, requireUser } from '../_lib/auth.js';

const REQUIRED = ['行业', '代码', '名称', '目标', '对应指数', '操作'];

function validateCsv(text) {
  const firstLine = text.replace(/^\uFEFF/, '').split(/\r?\n/, 1)[0] || '';
  const headers = firstLine.split(',').map(value => value.trim());
  const missing = REQUIRED.filter(field => !headers.includes(field));
  if (missing.length) return { error: `CSV缺少字段：${missing.join('、')}` };
  const rows = text.split(/\r?\n/).filter(Boolean).slice(1);
  if (!rows.length) return { error: 'CSV没有数据行' };
  if (rows.length > 500) return { error: '单个文件最多500行' };
  const ops = new Set(['准备种花', '种花', '准备摘花', '摘花', '候场', '伏击', '止盈观察', '兑现']);
  const bad = rows.slice(0, 10).find(row => {
    const values = row.split(',');
    return values.length < REQUIRED.length || !ops.has(values[5]?.trim());
  });
  if (bad) return { error: '存在字段数量或操作词不规范的数据行' };
  return { count: rows.length };
}

export async function onRequestPost({ request, env }) {
  const result = await requireUser(request, env);
  if (result.error) return result.error;
  const maxBytes = Number(env.MAX_UPLOAD_BYTES || 1048576);
  const contentLength = Number(request.headers.get('content-length') || 0);
  if (contentLength > maxBytes) return json({ error: '文件不能超过1MB' }, 413);
  const form = await request.formData();
  const file = form.get('file');
  if (!file || typeof file.text !== 'function') return json({ error: '请选择CSV文件' }, 400);
  const text = await file.text();
  if (new TextEncoder().encode(text).byteLength > maxBytes) return json({ error: '文件不能超过1MB' }, 413);
  const validation = validateCsv(text);
  if (validation.error) return json({ error: validation.error }, 400);
  const filename = String(file.name || 'upload.csv').slice(0, 160);
  const tradeDate = csvTradeDate(text) || String(form.get('trade_date') || '').slice(0, 10) || null;
  const inserted = await env.DB.prepare('INSERT INTO upload_jobs (user_id, filename, trade_date, csv_text) VALUES (?, ?, ?, ?)').bind(result.user.id, filename, tradeDate, text).run();
  return json({ ok: true, job_id: inserted.meta.last_row_id, filename, trade_date: tradeDate, count: validation.count, status: 'queued' }, 201);
}
