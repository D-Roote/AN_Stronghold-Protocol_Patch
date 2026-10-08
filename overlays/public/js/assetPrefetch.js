// Download bytes after consent, without decoding every image, skeleton or voice into memory.
import { mediaUrl } from './media.js';
import { ASSET_CACHE_PREFIX, ASSET_METADATA_CACHE, isCacheableAssetResponse } from '../asset-cache-sw.js';

const PREF_KEY = 'sp.pref.assetDownload';
const LOCAL_AVERAGE_BYTES = 66748205 / 1598; // v0.2.1 local art; estimates are labelled as estimates in the UI.
const errorMessage = (error) => String(error?.message || error || 'Download failed');
const abortError = () => Object.assign(new Error('Download paused'), { name: 'AbortError' });

export function openAssetPrefetchControls() {
  if (typeof globalThis.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
    globalThis.dispatchEvent(new CustomEvent('sp:asset-prefetch-open'));
  }
}

export function collectAssetUrls(manifest, localManifest, origin = globalThis.location?.origin || 'http://localhost') {
  const urls = new Set();
  const walk = (value) => {
    if (typeof value === 'string' && /^\/(?:assets|fonts)\//.test(value)) {
      try {
        const url = new URL(mediaUrl(value, origin), origin);
        if (url.origin === origin && /^\/(?:assets|fonts|media)\//.test(url.pathname)) urls.add(url.pathname + url.search);
      } catch { /* Invalid optional URLs do not become background requests. */ }
    } else if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === 'object') Object.values(value).forEach(walk);
  };
  walk(manifest);
  walk(localManifest);
  return [...urls].sort();
}

export function estimateAssetBytes(manifest, localManifest) {
  let localBytes = 0, count = 0;
  for (const group of Object.values(localManifest?.groups || {})) {
    for (const entry of Object.values(group || {})) {
      if (typeof entry?.path !== 'string' || !entry.path.startsWith('/assets/local/')) continue;
      count++;
      localBytes += Number.isFinite(entry.bytes) && entry.bytes >= 0 ? entry.bytes : LOCAL_AVERAGE_BYTES;
    }
  }
  return Math.round(Math.max(0, Number(manifest?.stats?.bytes) || 0) + (count ? localBytes : 0));
}

async function cacheVersion(manifest, localManifest, urls, cryptoApi) {
  const input = JSON.stringify({ manifest: manifest || {}, local: localManifest || {}, urls });
  if (cryptoApi?.subtle) {
    const digest = await cryptoApi.subtle.digest('SHA-256', new TextEncoder().encode(input));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('').slice(0, 32);
  }
  // CacheStorage/service workers normally require a secure context with Web Crypto. Keep injected tests usable.
  let first = 2166136261, second = 5381;
  for (let i = 0; i < input.length; i++) { first = Math.imul(first ^ input.charCodeAt(i), 16777619); second = Math.imul(second, 33) ^ input.charCodeAt(i); }
  return `${(first >>> 0).toString(16)}-${(second >>> 0).toString(16)}`;
}

/** Activate and claim the current page before publishing the active cache name to the worker. */
export async function activateAssetCache(cacheName, options = {}) {
  const navigatorObject = options.navigator || globalThis.navigator;
  const sw = navigatorObject?.serviceWorker;
  if (!sw) throw new Error('Asset cache is unsupported');
  const timeoutMs = options.timeoutMs || 15000;
  let timer = null, listener = null, port = null, cancelled = false;
  const assertActive = () => { if (cancelled) throw new Error('Asset cache activation was cancelled'); };
  try {
    return await Promise.race([(async () => {
      await sw.register('/asset-cache-sw.js', { type: 'module', updateViaCache: 'none' });
      assertActive();
      await sw.ready;
      assertActive();
      if (!sw.controller) await new Promise((resolve) => {
        listener = () => { if (sw.controller) resolve(); };
        sw.addEventListener('controllerchange', listener);
        listener();
      });
      assertActive();
      const MessageChannelClass = options.MessageChannel || globalThis.MessageChannel;
      const channel = new MessageChannelClass();
      port = channel.port1;
      const reply = new Promise((resolve, reject) => {
        port.onmessage = (event) => event.data?.ok ? resolve(true) : reject(new Error('Asset cache could not be configured'));
      });
      sw.controller.postMessage({ type: 'SP_ASSET_CACHE_CONFIGURE', cacheName }, [channel.port2]);
      return reply;
    })(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Asset cache activation timed out')), timeoutMs); })]);
  } finally {
    cancelled = true;
    clearTimeout(timer);
    if (listener) sw.removeEventListener('controllerchange', listener);
    port?.close?.();
  }
}

/** Injectable browser controller. Small manifests may be injected for tests without downloading the live bundle. */
export function createAssetPrefetchController(options = {}) {
  const origin = options.origin || globalThis.location?.origin || 'http://localhost';
  const fetchResource = options.fetch || ((...args) => globalThis.fetch(...args));
  const cacheStorage = options.caches || globalThis.caches;
  let storage = options.storage;
  if (storage === undefined) { try { storage = globalThis.localStorage; } catch { storage = null; } }
  const prefKey = options.prefKey || PREF_KEY;
  const activate = options.activateCache || ((name) => activateAssetCache(name, options));
  const supported = !!cacheStorage?.open && (!!options.activateCache || !!(options.navigator || globalThis.navigator)?.serviceWorker);
  const concurrency = Math.max(1, Math.min(3, Number(options.concurrency) || 2));
  const timeoutMs = Math.max(1, Number(options.timeoutMs) || 45000);
  let preference = {};
  try { preference = JSON.parse(storage?.getItem(prefKey) || '{}') || {}; } catch { /* Storage can be unavailable. */ }
  if (typeof preference !== 'object' || Array.isArray(preference)) preference = {};
  let state = {
    decision: ['accepted', 'declined'].includes(preference.decision) ? preference.decision : null,
    status: 'idle', supported, total: 0, completed: 0, cached: 0, failed: 0,
    downloadedBytes: 0, estimatedBytes: 0, error: null, errorCode: null, cacheName: null, clearing: false,
  };
  const listeners = new Set(), controllers = new Set();
  let urls = [], initialized = false, initializing = null, running = null, clearing = null, generation = 0;
  let notifiedAt = 0;
  const persist = () => { try { storage?.setItem(prefKey, JSON.stringify(preference)); } catch { /* Session-only fallback. */ } };
  const notify = () => { for (const listener of listeners) { try { listener({ ...state }); } catch { /* UI cannot interrupt downloading. */ } } };
  const set = (change) => { state = { ...state, ...change }; notify(); };
  const abortAll = () => { for (const controller of controllers) controller.abort(); controllers.clear(); };
  const pruneOldCaches = async (current) => {
    const names = await cacheStorage.keys();
    if (current !== generation) return false;
    for (const name of names) {
      if (current !== generation) return false;
      if (name.startsWith(ASSET_CACHE_PREFIX) && name !== state.cacheName) await cacheStorage.delete(name);
    }
    return current === generation;
  };

  async function readManifest(url, optional) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchResource(url, { cache: 'no-cache', signal: controller.signal });
      if (response?.status !== 200) { if (optional) return null; throw new Error('Asset manifest is unavailable'); }
      const result = await response.json();
      if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Invalid asset manifest');
      return result;
    } catch (error) { if (optional) return null; throw error; }
    finally { clearTimeout(timer); }
  }

  function init() {
    if (initialized) return Promise.resolve({ ...state });
    if (initializing) return initializing;
    set({ status: 'preparing', error: null, errorCode: null });
    initializing = (async () => {
      try {
        const [manifest, local] = await Promise.all([
          options.manifest !== undefined ? options.manifest : readManifest(options.manifestUrl || '/data/assets.json', false),
          options.localManifest !== undefined ? options.localManifest : readManifest(options.localManifestUrl || '/data/local-assets.json', true),
        ]);
        urls = collectAssetUrls(manifest, local, origin);
        const version = await cacheVersion(manifest, local, urls, options.crypto || globalThis.crypto);
        initialized = true;
        set({ total: urls.length, estimatedBytes: estimateAssetBytes(manifest, local), cacheName: ASSET_CACHE_PREFIX + version,
          status: !supported ? 'unsupported' : preference.paused ? 'paused' : 'idle' });
        // A paused download must not leave an old-version worker serving stale models after a deployment.
        if (state.decision === 'accepted' && preference.paused && supported && !state.clearing) {
          const current = generation;
          await activate(state.cacheName);
          await pruneOldCaches(current);
        }
        if (state.decision === 'accepted' && !preference.paused && supported) queueMicrotask(() => resume());
      } catch (error) {
        initializing = null;
        set({ status: 'error', error: errorMessage(error), errorCode: 'manifest' });
      }
      return { ...state };
    })();
    return initializing;
  }

  async function download(cache, url, current) {
    const controller = new AbortController();
    controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      // Asset URLs are not content-addressed: a new manifest must revalidate the browser's 1-day HTTP cache.
      const response = await fetchResource(url, { cache: 'no-cache', credentials: 'same-origin', signal: controller.signal });
      if (!isCacheableAssetResponse(url, response, origin)) {
        try { await response?.body?.cancel?.(); } catch { /* Do not drain wrong responses. */ }
        throw new Error(`Unusable asset response: ${response?.status ?? 'unknown'}`);
      }
      if (current !== generation || controller.signal.aborted) throw abortError();
      const headers = new Headers(response.headers);
      // fetch has decoded Content-Encoding already; cached synthetic responses contain decoded bytes.
      headers.delete('content-encoding');
      headers.delete('content-length');
      headers.delete('transfer-encoding');
      let body = response.body;
      if (body?.getReader && typeof ReadableStream === 'function') {
        const reader = body.getReader();
        body = new ReadableStream({
          async pull(stream) {
            if (controller.signal.aborted || current !== generation) { await reader.cancel(); stream.error(abortError()); return; }
            try {
              const chunk = await reader.read();
              if (chunk.done) { stream.close(); return; }
              if (current === generation) {
                state.downloadedBytes += chunk.value.byteLength;
                if (Date.now() - notifiedAt > 200) { notifiedAt = Date.now(); notify(); }
              }
              stream.enqueue(chunk.value);
            } catch (error) { stream.error(error); }
          },
          cancel(reason) { return reader.cancel(reason); },
        });
      } else {
        body = await response.arrayBuffer();
        if (current === generation) state.downloadedBytes += body.byteLength;
      }
      await cache.put(new URL(url, origin).href, new Response(body, { status: 200, headers }));
      if (controller.signal.aborted || current !== generation) throw abortError();
    } finally { clearTimeout(timer); controllers.delete(controller); }
  }

  function resume() {
    if (state.clearing) return clearing || Promise.resolve({ ...state });
    if (running) return running;
    if (state.decision !== 'accepted') return Promise.resolve({ ...state });
    preference.paused = false;
    persist();
    const current = ++generation;
    const promise = (async () => {
      await init();
      if (current !== generation || !initialized) return { ...state };
      if (!supported) { set({ status: 'unsupported', errorCode: 'unsupported' }); return { ...state }; }
      set({ status: 'preparing', error: null, errorCode: null, completed: 0, cached: 0, failed: 0, downloadedBytes: 0 });
      try {
        await activate(state.cacheName);
        if (current !== generation) return { ...state };
        if (!await pruneOldCaches(current)) return { ...state };
        const cache = await cacheStorage.open(state.cacheName);
        if (current !== generation) return { ...state };
        set({ status: 'downloading' });
        let index = 0;
        const worker = async () => {
          while (current === generation && index < urls.length) {
            const url = urls[index++];
            try {
              const cached = await cache.match(new URL(url, origin).href);
              if (current !== generation) return;
              if (isCacheableAssetResponse(url, cached, origin)) set({ completed: state.completed + 1, cached: state.cached + 1 });
              else { await download(cache, url, current); if (current === generation) set({ completed: state.completed + 1 }); }
            } catch (error) {
              if (current !== generation) return;
              if (error?.name === 'QuotaExceededError' || error?.code === 22) {
                generation++;
                abortAll();
                preference.paused = true;
                persist();
                set({ status: 'error', error: errorMessage(error), errorCode: 'quota' });
                return;
              }
              set({ failed: state.failed + 1, error: errorMessage(error) });
            }
          }
        };
        await Promise.all(Array.from({ length: concurrency }, worker));
        if (current === generation) set({ status: state.failed ? 'error' : 'complete', errorCode: state.failed ? 'download' : null });
      } catch (error) { if (current === generation) set({ status: 'error', error: errorMessage(error), errorCode: 'cache' }); }
      return { ...state };
    })();
    running = promise;
    promise.finally(() => { if (running === promise) running = null; });
    return promise;
  }

  function pause() {
    generation++;
    abortAll();
    running = null;
    preference.paused = true;
    persist();
    set({ status: state.clearing ? 'preparing' : 'paused' });
  }

  function clearCache() {
    if (clearing) return clearing;
    const previous = running || initializing;
    state.clearing = true;
    pause();
    const promise = (async () => {
      try {
        // Let cancelled reads/cache.put calls settle before deleting their destination cache.
        await previous;
        if (supported) await activate(null);
        for (const name of await cacheStorage.keys()) if (name.startsWith(ASSET_CACHE_PREFIX) || name === ASSET_METADATA_CACHE) await cacheStorage.delete(name);
        set({ completed: 0, cached: 0, failed: 0, downloadedBytes: 0, error: null, errorCode: null, status: 'paused' });
      } catch (error) { set({ status: 'error', error: errorMessage(error), errorCode: 'cache' }); }
      finally { state.clearing = false; clearing = null; notify(); }
      return { ...state };
    })();
    clearing = promise;
    return promise;
  }

  return {
    init,
    getState: () => ({ ...state }),
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    async accept() { preference.decision = 'accepted'; state.decision = 'accepted'; persist(); notify(); return resume(); },
    decline() { pause(); preference.decision = 'declined'; state.decision = 'declined'; persist(); set({ status: 'idle' }); },
    pause, resume, clearCache,
  };
}

export const assetPrefetch = createAssetPrefetchController();
