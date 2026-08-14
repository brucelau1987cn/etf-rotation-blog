// 站点访问鉴权中间件：
// - 仅滚动罗盘相关页面需要订阅/管理员登录
// - 其余页面全部开放
// - /api/ 由各端点自行鉴权；静态资源直接放行
import { isSubscribed, isAdmin } from './_lib/subscription-auth.js';

// 需要登录才能访问的页面前缀（滚动罗盘全系）
const PROTECTED_PREFIXES = [
  '/rolling',
];

// 静态资源扩展名（CSS/JS/图片/字体等）
const STATIC_RE = /\.(css|js|mjs|png|jpg|jpeg|webp|gif|svg|ico|woff2?|ttf|eot|map|txt|json|xml|webmanifest)$/i;
const PRIVATE_LOW_CHIP_DATA = [
  '/data/a-low-chip-stocks.json',
  '/data/low-chip-history-index.json',
  '/data/low-chip-history/',
  '/data/low-chip-tracking.json',
];

function isPrivateLowChipData(pathname) {
  return PRIVATE_LOW_CHIP_DATA.some((path) => pathname === path || (path.endsWith('/') && pathname.startsWith(path)));
}

function normalizePathname(pathname) {
  let decoded = pathname;
  let stabilized = false;
  for (let depth = 0; depth < 8; depth += 1) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) {
        stabilized = true;
        break;
      }
      decoded = next;
    } catch {
      return null;
    }
  }
  if (!stabilized) return null;
  decoded = decoded.replace(/\\/g, '/');
  const segments = [];
  for (const segment of decoded.split('/')) {
    if (!segment || segment === '.') continue;
    if (segment === '..') segments.pop();
    else segments.push(segment);
  }
  return `/${segments.join('/')}`;
}

function isProtectedPath(pathname) {
  // exact /rolling or any /rolling/... page
  return PROTECTED_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const pathname = normalizePathname(url.pathname);
  if (!pathname) {
    return Response.json({ ok: false, error: '无效路径' }, { status: 400 });
  }

  if (isPrivateLowChipData(pathname)) {
    return Response.json({ ok: false, error: '资源不存在' }, { status: 404 });
  }

  // 静态资源直接放行
  if (STATIC_RE.test(pathname)) return next();

  // 非滚动罗盘页面：全部开放
  if (!isProtectedPath(pathname)) return next();

  // 滚动罗盘页面：订阅或管理员可访问
  if (await isSubscribed(request, env)) return next();
  if (await isAdmin(request, env)) return next();

  return Response.redirect(`${url.origin}/login/?next=${encodeURIComponent(pathname)}`, 302);
}
