// GET /api/public/v1/polymarket-prob?q=cpi-yoy
const TTL = 300;
const cache = new Map();
const SLUGS = {
  'cpi-yoy': 'july-inflation-us-annual-20260714150613267',
  'cpi-mom': 'july-inflation-us-monthly-20260714151042665',
  'core-cpi-yoy': 'core-cpi-yoy-july-2026-20260714151811920',
  'core-cpi-mom': 'core-cpi-mom-july-2026-20260705181328287',
};

export async function onRequestGet(context) {
  const { request } = context;
  const url = new URL(request.url);
  const q = String(url.searchParams.get('q') || '').toLowerCase().trim();
  if (!q || !SLUGS[q]) {
    return Response.json({ ok: false, error: 'unknown q; available: ' + Object.keys(SLUGS).join(',') }, { status: 400 });
  }
  const slug = SLUGS[q];
  const hit = cache.get(slug);
  if (hit && hit.expiresAt > Date.now()) {
    return Response.json(hit.payload, { headers: { 'Cache-Control': `public, max-age=${TTL}` } });
  }

  try {
    const resp = await fetch(`https://gamma-api.polymarket.com/events?slug=${encodeURIComponent(slug)}`, {
      headers: { 'User-Agent': 'Mozilla/5.0' },
    });
    if (!resp.ok) throw new Error(`Gamma ${resp.status}`);
    const body = await resp.json();
    const ev = (body || [])[0];
    if (!ev) throw new Error('event not found');
    const markets = (ev.markets || []).map((m) => {
      // outcomePrices 可能为数组 / "0.0185,0.9815" / "['0.0185','0.9815']"
      const raw = Array.isArray(m.outcomePrices)
        ? m.outcomePrices
        : (String(m.outcomePrices || '').match(/-?\d+(?:\.\d+)?/g) || []);
      const prices = raw.map((p) => Number(String(p).trim()) * 100);
      return {
        id: m.id,
        question: m.question,
        yes_prob: Number((prices[0] ?? 0).toFixed(1)),
        no_prob: Number((prices[1] ?? 0).toFixed(1)),
        end_date: m.endDate || null,
      };
    }).sort((a, b) => b.yes_prob - a.yes_prob);
    const payload = {
      ok: true, q, event: ev.title, slug, updated_at: new Date().toISOString(),
      top: markets[0] || null, markets: markets.slice(0, 8),
    };
    cache.set(slug, { expiresAt: Date.now() + TTL * 1000, payload });
    return Response.json(payload, { headers: { 'Cache-Control': `public, max-age=${TTL}` } });
  } catch (e) {
    return Response.json({ ok: false, error: String(e?.message || e) }, { status: 502 });
  }
}