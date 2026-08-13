/**
 * GET /api/public/v1/ths/* — retired public proxy.
 * THS chip/kline remains available via dedicated skills / CF worker, not this site surface.
 */

function gone() {
  return new Response(JSON.stringify({
    status: 'gone',
    code: 'GONE',
    message: 'GET /api/public/v1/ths/* is retired',
  }), {
    status: 410,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=300',
      'access-control-allow-origin': '*',
    },
  });
}

export async function onRequestGet() {
  return gone();
}

export async function onRequestOptions() {
  return gone();
}
