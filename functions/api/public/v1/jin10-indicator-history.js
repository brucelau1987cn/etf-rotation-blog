// GET /api/public/v1/jin10-indicator-history — retired. No page caller; Jin10 rili-open-api is unused.

export async function onRequestGet() {
  return Response.json(
    {
      ok: false,
      status: 'gone',
      code: 'GONE',
      message: 'GET /api/public/v1/jin10-indicator-history is retired',
    },
    { status: 410 },
  );
}
