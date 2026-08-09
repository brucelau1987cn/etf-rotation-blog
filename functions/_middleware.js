// 全站订阅鉴权中间件：未登录（无有效订阅 cookie）访问任何页面 → 302 /login/
// 静态资源与登录/管理 API 放行。
import { isSubscribed, isAdmin } from './_lib/subscription-auth.js';

// 放行清单（无需订阅即可访问）
const PUBLIC_PREFIXES = [
  '/login',
  '/api/', // 全部 API 由各端点自行鉴权（admin API 校验管理员、订阅 API 校验订阅）
];
// 静态资源扩展名（CSS/JS/图片/字体等）
const STATIC_RE = /\.(css|js|mjs|png|jpg|jpeg|webp|gif|svg|ico|woff2?|ttf|eot|map|txt)$/i;
// 管理后台路径（页面放行渲染登录表单，API 由 API 层校验 admin）
const ADMIN_PREFIXES = ['/admin'];

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const { pathname } = url;

  // 静态资源直接放行（页面 CSS/JS/图 不被拦）
  if (STATIC_RE.test(pathname)) return next();

  // 公共路径放行
  if (PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))) return next();

  // 管理后台页面放行（页面内 JS 判断登录态并调用 API）；管理 API 由 API 层校验
  if (ADMIN_PREFIXES.some((p) => pathname.startsWith(p))) return next();

  // 其余全部页面需要订阅登录（或管理员登录）
  if (await isSubscribed(request, env)) return next();
  if (await isAdmin(request, env)) return next(); // 管理员 cookie 同样解锁全站

  // 未登录 → 跳登录页（带原路径，登录后跳回）
  const nextUrl = pathname === '/' ? '/' : pathname;
  return Response.redirect(`${url.origin}/login/?next=${encodeURIComponent(nextUrl)}`, 302);
}
