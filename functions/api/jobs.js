import { json, requireUser } from '../../_lib/auth.js';

export async function onRequestGet({ request, env }) {
  const result = await requireUser(request, env);
  if (result.error) return result.error;
  const { results } = await env.DB.prepare(`SELECT id, filename, trade_date, status, result_json, error_message, created_at, completed_at
    FROM upload_jobs WHERE user_id = ? ORDER BY id DESC LIMIT 100`).bind(result.user.id).all();
  return json({ jobs: results.map(job => ({ ...job, result: job.result_json ? JSON.parse(job.result_json) : null })) });
}
