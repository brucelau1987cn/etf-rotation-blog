// POST /api/public/v1/logout — 清除登录 cookie（HttpOnly cookie 无法用 JS 删除）
export async function onRequestPost() {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  headers.append('Set-Cookie', 'etf_sub=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax');
  headers.append('Set-Cookie', 'etf_admin=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax');
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers });
}
