// Optional consent-driven asset cache. Application code, pages and live data always use the network.
export const ASSET_CACHE_PREFIX = 'sp-assets-v1-';
export const ASSET_METADATA_CACHE = 'sp-assets-metadata-v1';
const METADATA_PATH = '/__sp_asset_cache_metadata__';

/** Only downloaded, same-origin game resources can be read from this cache. */
export function isAssetRequest(request, origin) {
  if (!request || (request.method && request.method !== 'GET')) return false;
  try {
    const url = new URL(typeof request === 'string' ? request : request.url, origin);
    return url.origin === origin && /^\/(?:assets|fonts|media)\//.test(url.pathname)
      && !/\.(?:html?|m?js|webmanifest)$/i.test(url.pathname);
  } catch { return false; }
}

/** Reject partial, opaque and HTML fallback responses before they can poison the cache. */
export function isCacheableAssetResponse(url, response, origin) {
  if (!isAssetRequest(url, origin) || response?.status !== 200 || response.type === 'opaque' || response.type === 'opaqueredirect') return false;
  if (response.url) { try { if (new URL(response.url).origin !== origin) return false; } catch { return false; } }
  const type = (response.headers?.get?.('content-type') || '').split(';')[0].trim().toLowerCase();
  const path = new URL(url, origin).pathname.toLowerCase();
  if (path.startsWith('/media/') || /\.(?:mp3|ogg|oga|opus|wav|m4a|aac)$/.test(path)) return type.startsWith('audio/');
  if (/\.(?:png|jpg|jpeg|gif|webp|avif|svg|ico)$/.test(path)) return type.startsWith('image/');
  if (path.endsWith('.atlas')) return type === 'text/plain';
  if (/\.(?:skel|bin)$/.test(path)) return type === 'application/octet-stream';
  if (path.startsWith('/fonts/') && path.endsWith('.css')) return type === 'text/css';
  if (path.startsWith('/fonts/') && /\.(?:woff2?|otf|ttf)$/.test(path)) {
    return type.startsWith('font/') || ['application/font-woff', 'application/vnd.ms-opentype', 'application/octet-stream'].includes(type);
  }
  return false;
}

/** One byte range, including suffix/open-ended ranges; null means leave unsupported ranges to the server. */
export function parseAssetRange(header, size) {
  const match = /^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$/i.exec(header || '');
  if (!match || (!match[1] && !match[2])) return null;
  const startValue = match[1] ? Number(match[1]) : null;
  const endValue = match[2] ? Number(match[2]) : null;
  if ((startValue != null && !Number.isSafeInteger(startValue)) || (endValue != null && !Number.isSafeInteger(endValue))) return null;
  if (startValue == null) {
    if (endValue === 0 || size === 0) return { unsatisfiable: true };
    return { start: Math.max(0, size - endValue), end: size - 1 };
  }
  if (endValue != null && endValue < startValue) return null;
  if (startValue >= size) return { unsatisfiable: true };
  return { start: startValue, end: endValue == null ? size - 1 : Math.min(size - 1, endValue) };
}

/** A cached complete response can serve audio Range requests without another network download. */
export async function cachedRangeResponse(response, rangeHeader) {
  if (response?.status !== 200) return null;
  const bytes = new Uint8Array(await response.arrayBuffer());
  const range = parseAssetRange(rangeHeader, bytes.byteLength);
  if (!range) return null;
  const headers = new Headers(response.headers);
  headers.delete('content-encoding');
  headers.delete('transfer-encoding');
  headers.delete('content-range');
  headers.set('accept-ranges', 'bytes');
  if (range.unsatisfiable) {
    headers.set('content-range', `bytes */${bytes.byteLength}`);
    headers.set('content-length', '0');
    return new Response(null, { status: 416, statusText: 'Range Not Satisfiable', headers });
  }
  const body = bytes.slice(range.start, range.end + 1);
  headers.set('content-range', `bytes ${range.start}-${range.end}/${bytes.byteLength}`);
  headers.set('content-length', String(body.byteLength));
  return new Response(body, { status: 206, statusText: 'Partial Content', headers });
}

export function installAssetCacheWorker(scope) {
  let active = null;
  const metadataUrl = new URL(METADATA_PATH, scope.location.origin).href;
  const validName = (name) => typeof name === 'string' && /^sp-assets-v1-[a-z0-9_-]{1,100}$/i.test(name);
  const activeCache = () => {
    if (!active) active = (async () => {
      try {
        const cache = await scope.caches.open(ASSET_METADATA_CACHE);
        const response = await cache.match(metadataUrl);
        const value = response ? await response.json() : null;
        return validName(value?.cacheName) ? value.cacheName : null;
      } catch { return null; }
    })();
    return active;
  };
  scope.addEventListener('install', (event) => event.waitUntil(scope.skipWaiting()));
  scope.addEventListener('activate', (event) => event.waitUntil(scope.clients.claim()));
  scope.addEventListener('message', (event) => {
    if (event.data?.type !== 'SP_ASSET_CACHE_CONFIGURE') return;
    const name = event.data.cacheName;
    if (name !== null && !validName(name)) { event.ports?.[0]?.postMessage({ ok: false }); return; }
    const configure = (async () => {
      const cache = await scope.caches.open(ASSET_METADATA_CACHE);
      if (name === null) await cache.delete(metadataUrl);
      else await cache.put(metadataUrl, new Response(JSON.stringify({ cacheName: name }), { headers: { 'content-type': 'application/json' } }));
      active = Promise.resolve(name);
      event.ports?.[0]?.postMessage({ ok: true });
    })().catch(() => { event.ports?.[0]?.postMessage({ ok: false }); });
    event.waitUntil(configure);
  });
  scope.addEventListener('fetch', (event) => {
    if (!isAssetRequest(event.request, scope.location.origin)) return;
    event.respondWith((async () => {
      try {
        const name = await activeCache();
        if (name) {
          const cache = await scope.caches.open(name);
          const cached = await cache.match(event.request.url);
          if (isCacheableAssetResponse(event.request.url, cached, scope.location.origin)) {
            const range = event.request.headers.get('range');
            if (!range) return cached;
            const partial = await cachedRangeResponse(cached, range);
            if (partial) return partial;
          }
        }
      } catch { /* A cache failure must never stop ordinary game loading. */ }
      return scope.fetch(event.request);
    })());
  });
}

if (globalThis.registration && typeof globalThis.addEventListener === 'function') installAssetCacheWorker(globalThis);
