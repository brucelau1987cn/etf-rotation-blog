const BAOSTOCK_VERSION = '00.9.30';
const SEP = '\x01';
const BAOSTOCK_HEADER_LENGTH = 21;
const COMPRESSED_TYPES = new Set(['96', '99', '9B', '9D']);

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function buildBaoStockMessage(type, body) {
  const encoder = new TextEncoder();
  const bodyLength = encoder.encode(body).length;
  const header = `${BAOSTOCK_VERSION}${SEP}${type}${SEP}${String(bodyLength).padStart(10, '0')}`;
  const headBody = `${header}${body}`;
  return `${headBody}${SEP}${crc32(encoder.encode(headBody))}\n`;
}

async function inflateZlib(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function expectedBaoStockFrameLength(bytes) {
  if (bytes.length < BAOSTOCK_HEADER_LENGTH) return null;
  const header = new TextDecoder().decode(bytes.slice(0, BAOSTOCK_HEADER_LENGTH));
  const fields = header.split(SEP);
  const bodyLength = Number(fields[2]);
  if (fields.length !== 3 || !Number.isInteger(bodyLength) || bodyLength < 0) return -1;
  return BAOSTOCK_HEADER_LENGTH + bodyLength + (COMPRESSED_TYPES.has(fields[1]) ? 13 : 1);
}

async function parseBaoStockResponseBytes(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (bytes.length < BAOSTOCK_HEADER_LENGTH) throw new Error('truncated baostock response');
  const decoder = new TextDecoder();
  const header = decoder.decode(bytes.slice(0, BAOSTOCK_HEADER_LENGTH));
  const headerFields = header.split(SEP);
  if (headerFields.length !== 3 || !/^00\.9\.\d{2}$/.test(headerFields[0])) {
    throw new Error(`invalid baostock header ${JSON.stringify(header)}`);
  }
  const [version, type, bodyLengthRaw] = headerFields;
  const bodyLength = Number(bodyLengthRaw);
  if (!Number.isInteger(bodyLength) || bodyLength < 0 || bytes.length < BAOSTOCK_HEADER_LENGTH + bodyLength) {
    throw new Error('invalid baostock body length');
  }
  let bodyBytes = bytes.slice(BAOSTOCK_HEADER_LENGTH, BAOSTOCK_HEADER_LENGTH + bodyLength);
  if (COMPRESSED_TYPES.has(type)) bodyBytes = await inflateZlib(bodyBytes);
  const body = decoder.decode(bodyBytes).replace(/\n$/, '');
  return { version, type, body, fields: body.split(SEP) };
}

function parseHistoryResponse(parsed) {
  const records = parseBaoStockTableResponse(parsed, '96');
  return records.map((item) => {
    const result = {
      ...item,
      date: String(item.date || ''),
      open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close),
      volume: Number(item.volume), hsl: Number(item.turn),
    };
    if (!result.date || [result.open, result.high, result.low, result.close, result.volume, result.hsl].some((value) => !Number.isFinite(value))) {
      throw new Error('invalid baostock row');
    }
    return result;
  });
}

export function parseBaoStockTableResponse(parsed, expectedType) {
  const fields = parsed.fields;
  if (fields[0] !== '0') throw new Error(`baostock error ${fields[0] || 'unknown'}`);
  if (parsed.type !== expectedType || fields.length < 7) throw new Error('invalid baostock table response');
  let records;
  try {
    records = JSON.parse(fields[6]).record;
  } catch {
    throw new Error('invalid baostock records');
  }
  const columnsIndex = expectedType === '34' ? 9 : 8;
  const columns = String(fields[columnsIndex] || '').split(',').map((field) => field.trim()).filter(Boolean);
  if (!Array.isArray(records) || !columns.length) throw new Error('invalid baostock records');
  return records.map((row) => {
    if (!Array.isArray(row) || row.length !== columns.length) throw new Error('invalid baostock row');
    return Object.fromEntries(columns.map((column, index) => [column, row[index]]));
  });
}

async function readBaoStockFrame(reader, timeoutMs = 10000) {
  const chunks = [];
  let size = 0;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const remaining = Math.max(1, deadline - Date.now());
    const result = await Promise.race([
      reader.read(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('baostock read timeout')), remaining)),
    ]);
    if (result.done) break;
    chunks.push(result.value);
    size += result.value.length;
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const expected = expectedBaoStockFrameLength(bytes);
    if (expected === -1) throw new Error('invalid baostock frame');
    if (expected && size >= expected) return bytes.slice(0, expected);
  }
  throw new Error('truncated baostock frame');
}

async function sendBaoStockRequest(writer, reader, type, body) {
  await writer.write(new TextEncoder().encode(buildBaoStockMessage(type, body)));
  return parseBaoStockResponseBytes(await readBaoStockFrame(reader));
}

export function baoStockSecCode(symbol) {
  const value = String(symbol || '').toLowerCase();
  const suffix = value.match(/^(\d{6})\.(sh|sz)$/);
  if (suffix) return `${suffix[2]}.${suffix[1]}`;
  return /^(sh|sz)\.\d{6}$/.test(value)
    ? value
    : (value.startsWith('6') ? `sh.${value}` : `sz.${value}`);
}

async function withBaoStockSession(connectOverride, operation) {
  const connectFn = connectOverride || (await import('cloudflare:sockets')).connect;
  const socket = connectFn({ hostname: 'public-api.baostock.com', port: 10030 });
  let writer = null;
  let reader = null;
  try {
    await socket.opened;
    writer = socket.writable.getWriter();
    reader = socket.readable.getReader();
    const login = await sendBaoStockRequest(writer, reader, '00', ['login', 'anonymous', '123456', '0'].join(SEP));
    if (login.type !== '01' || login.fields[0] !== '0' || !login.fields[3]) {
      throw new Error(`baostock login ${login.fields[0] || 'failed'}`);
    }
    return await operation({ writer, reader, userId: login.fields[3] });
  } finally {
    try { writer?.releaseLock(); } catch {}
    try { reader?.releaseLock(); } catch {}
    try { await socket.close(); } catch {}
  }
}

async function fetchKlineFromBaoStock(symbol, options = '', connectOverride = null) {
  const config = typeof options === 'string' ? { adjust: options } : (options || {});
  const adjust = config.adjust ?? '';
  const adjustFlag = { '': '3', none: '3', qfq: '2', hfq: '1' }[adjust];
  if (!adjustFlag) throw new Error('invalid baostock adjust');
  const secCode = baoStockSecCode(symbol);
  const end = config.end || new Date().toISOString().slice(0, 10);
  const start = config.start || new Date(Date.now() - 500 * 86400000).toISOString().slice(0, 10);
  const requestedFields = config.fields || ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn'];
  const fields = Array.isArray(requestedFields) ? requestedFields.join(',') : String(requestedFields);
  return withBaoStockSession(connectOverride, async ({ writer, reader, userId }) => {
    const body = ['query_history_k_data_plus', userId, '1', '2000', secCode,
      fields, start, end, 'd', adjustFlag].join(SEP);
    const parsed = await sendBaoStockRequest(writer, reader, '95', body);
    if (typeof options === 'string' || options == null) return parseHistoryResponse(parsed);
    return parseBaoStockTableResponse(parsed, '96');
  });
}

async function fetchCalendarFromBaoStock(start, end, connectOverride = null) {
  return withBaoStockSession(connectOverride, async ({ writer, reader, userId }) => {
    const body = ['query_trade_dates', userId, '1', '2000', start, end].join(SEP);
    return parseBaoStockTableResponse(await sendBaoStockRequest(writer, reader, '33', body), '34');
  });
}

export {
  BAOSTOCK_HEADER_LENGTH,
  buildBaoStockMessage,
  expectedBaoStockFrameLength,
  fetchCalendarFromBaoStock,
  fetchKlineFromBaoStock,
  parseBaoStockResponseBytes,
  parseHistoryResponse,
};
