// GET /api/public/v1/subscription/status — retired. Use /api/public/v1/me.

export async function onRequestGet() {
  return Response.json(
    { ok: false, status: 'gone', code: 'GONE', message: 'GET /api/public/v1/subscription/status is retired; use /api/public/v1/me' },
    { status: 410 },
  );
}
