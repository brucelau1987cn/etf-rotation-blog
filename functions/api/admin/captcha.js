// 验证码接口 — 预留（2026-08-08 加入，后期启用）
// GET  /api/admin/captcha → { enabled, hint }  当前未启用
// POST /api/admin/captcha → 预留校验入口（未启用返回 501）
// 注意：不要用子路径 /api/admin/captcha/status（Pages 目录路由会回退到 404 页）
export async function onRequestGet() {
  return Response.json({
    ok: true,
    enabled: false, // 未启用 — 后期接入验证码服务后置 true
    hint: '验证码校验接口已预留；启用后管理员登录需额外通过验证码。',
  });
}

export async function onRequestPost() {
  return Response.json(
    { ok: false, error: '验证码服务未启用' },
    { status: 501 },
  );
}
