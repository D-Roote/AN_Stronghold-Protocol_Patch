import { test } from 'node:test';
import assert from 'node:assert/strict';
import { collectAssetUrls, estimateAssetBytes, createAssetPrefetchController, activateAssetCache } from '../public/js/assetPrefetch.js';
import { ASSET_CACHE_PREFIX, ASSET_METADATA_CACHE, isAssetRequest, isCacheableAssetResponse, cachedRangeResponse, installAssetCacheWorker } from '../public/asset-cache-sw.js';

const ORIGIN = 'https://game.test';
const manifest = { hash: 'tiny-fixture', stats: { bytes: 12 }, ui: { one: '/assets/a.png', duplicate: '/assets/a.png' },
  audio: { voice: { op: { select: ['/assets/audio/voice/kr/op/cn_023.mp3'] } } },
  fonts: { css: '/fonts/fonts.css', external: 'https://fonts.example/font.woff2' } };
const local = { groups: { model: { item: { path: '/assets/local/model/item.atlas', bytes: 4 } } } };
const key = (url) => typeof url === 'string' ? new URL(url, ORIGIN).href : url.url;
const response = (url) => new Response('data', { headers: { 'content-type': url.includes('/media/') ? 'audio/mpeg'
  : url.endsWith('.atlas') ? 'text/plain' : url.endsWith('.css') ? 'text/css' : 'image/png' } });
const waitUntil = async (predicate) => {
  for (let i = 0; i < 400; i++) { if (predicate()) return; await new Promise((resolve) => setTimeout(resolve, 5)); }
  throw new Error('Timed out waiting for fixture');
};

class MemoryCacheStorage {
  constructor() { this.entries = new Map(); }
  async open(name) {
    if (!this.entries.has(name)) this.entries.set(name, new Map());
    const map = this.entries.get(name);
    return {
      match: async (url) => map.get(key(url))?.clone(),
      put: async (url, value) => {
        const bytes = await value.arrayBuffer();
        map.set(key(url), new Response(bytes, { status: value.status, headers: value.headers }));
      },
      delete: async (url) => map.delete(key(url)),
    };
  }
  async keys() { return [...this.entries.keys()]; }
  async delete(name) { return this.entries.delete(name); }
}

function fixture(options = {}) {
  const saved = new Map(), requests = [], activations = [];
  const storage = { getItem: (name) => saved.get(name) || null, setItem: (name, value) => saved.set(name, value) };
  const caches = new MemoryCacheStorage();
  const dependencies = { origin: ORIGIN, manifest, localManifest: local, storage, caches,
    activateCache: async (name) => { activations.push(name); },
    fetch: async (url) => { requests.push(url); return response(url); }, ...options };
  return { controller: createAssetPrefetchController(dependencies), dependencies, saved, requests, activations, caches };
}

test('manifest collection deduplicates, rewrites playback URLs and excludes external resources', () => {
  assert.deepEqual(collectAssetUrls(manifest, local, ORIGIN), [
    '/assets/a.png', '/assets/local/model/item.atlas', '/fonts/fonts.css', '/media/voice/kr/op/cn_023',
  ]);
  assert.equal(estimateAssetBytes(manifest, local), 16);
});

test('initial visit and persisted decline never download resources or create a cache', async () => {
  const f = fixture();
  await f.controller.init();
  assert.equal(f.controller.getState().decision, null);
  assert.equal(f.requests.length, 0);
  assert.equal(f.activations.length, 0);
  assert.deepEqual(await f.caches.keys(), []);
  f.controller.decline();
  const reloaded = createAssetPrefetchController(f.dependencies);
  await reloaded.init();
  await reloaded.resume();
  assert.equal(reloaded.getState().decision, 'declined');
  assert.equal(f.requests.length, 0);
  assert.deepEqual(await f.caches.keys(), []);
});

test('accepted download survives reload and cached files are not fetched again', async () => {
  const f = fixture();
  await f.controller.accept();
  assert.equal(f.controller.getState().status, 'complete');
  assert.equal(f.controller.getState().completed, 4);
  assert.equal(f.controller.getState().downloadedBytes, 16);
  assert.equal(f.requests.length, 4);
  const reloaded = createAssetPrefetchController(f.dependencies);
  await reloaded.init();
  await reloaded.resume();
  assert.equal(reloaded.getState().status, 'complete');
  assert.equal(reloaded.getState().cached, 4);
  assert.equal(f.requests.length, 4);
});

test('queue limits concurrent requests and activates the exact cache it fills', async () => {
  let active = 0, peak = 0;
  const small = { hash: 'bounded', stats: { bytes: 20 }, ui: Object.fromEntries(Array.from({ length: 5 }, (_, i) => [i, `/assets/${i}.png`])) };
  const f = fixture({ manifest: small, localManifest: null, concurrency: 2, fetch: async (url, options) => {
    assert.equal(options.cache, 'no-cache');
    active++; peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 15));
    active--; return response(url);
  } });
  await f.controller.accept();
  assert.equal(peak, 2);
  assert.equal(f.controller.getState().completed, 5);
  assert.deepEqual(f.activations, [f.controller.getState().cacheName]);
});

test('HTML fallback, partial, missing and unknown-status responses are not cached', async () => {
  const invalid = { hash: 'bad-responses', ui: { a: '/assets/a.png', b: '/assets/b.png', c: '/assets/c.png', d: '/assets/d.png' } };
  let repaired = false;
  const f = fixture({ manifest: invalid, localManifest: null, fetch: async (url) => {
    if (repaired) return response(url);
    if (url.endsWith('/a.png')) return new Response('<html>fallback</html>', { headers: { 'content-type': 'text/html' } });
    if (url.endsWith('/b.png')) return new Response('part', { status: 206, headers: { 'content-type': 'image/png' } });
    if (url.endsWith('/c.png')) return new Response('missing', { status: 404 });
    return { ok: true, headers: new Headers({ 'content-type': 'image/png' }) };
  } });
  await f.controller.accept();
  assert.equal(f.controller.getState().failed, 4);
  assert.equal(f.controller.getState().completed, 0);
  assert.equal(f.caches.entries.get(f.controller.getState().cacheName).size, 0);
  repaired = true;
  await f.controller.resume();
  assert.equal(f.controller.getState().status, 'complete');
  assert.equal(f.controller.getState().failed, 0);
});

test('pause aborts the current request, persists across reload and resume skips completed files', async () => {
  let resumed = false, pending = false;
  const calls = [];
  const f = fixture({ manifest: { hash: 'pause', ui: { a: '/assets/a.png', b: '/assets/b.png' } }, localManifest: null, concurrency: 1,
    fetch: async (url, options) => {
      calls.push(url);
      if (url.endsWith('a.png') || resumed) return response(url);
      pending = true;
      return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true }));
    } });
  const downloading = f.controller.accept();
  await waitUntil(() => pending && f.controller.getState().completed === 1);
  f.controller.pause();
  await downloading;
  assert.equal(f.controller.getState().status, 'paused');
  const reloaded = createAssetPrefetchController(f.dependencies);
  await reloaded.init();
  assert.equal(reloaded.getState().status, 'paused');
  assert.equal(calls.length, 2);
  resumed = true;
  await reloaded.resume();
  assert.equal(reloaded.getState().status, 'complete');
  assert.equal(reloaded.getState().cached, 1);
  assert.deepEqual(calls, ['/assets/a.png', '/assets/b.png', '/assets/b.png']);
});

test('request timeout stops a hanging download without storing incomplete content', async () => {
  let aborted = false;
  const f = fixture({ manifest: { ui: { a: '/assets/a.png' } }, localManifest: null, timeoutMs: 10,
    fetch: async (_, options) => new Promise((_, reject) => options.signal.addEventListener('abort', () => {
      aborted = true; reject(Object.assign(new Error('timeout'), { name: 'AbortError' }));
    }, { once: true })) });
  await f.controller.accept();
  assert.equal(aborted, true);
  assert.equal(f.controller.getState().failed, 1);
  assert.equal(f.controller.getState().completed, 0);
});

test('quota error stops downloading and leaves already cached bytes usable', async () => {
  const f = fixture({ concurrency: 1 });
  const open = f.caches.open.bind(f.caches);
  let puts = 0, exhausted = true;
  f.caches.open = async (name) => {
    const cache = await open(name), put = cache.put;
    cache.put = async (...args) => {
      if (exhausted && puts++ > 0) throw Object.assign(new Error('quota'), { name: 'QuotaExceededError' });
      return put(...args);
    };
    return cache;
  };
  await f.controller.accept();
  assert.equal(f.controller.getState().errorCode, 'quota');
  assert.equal(f.controller.getState().completed, 1);
  assert.equal(f.requests.length, 2);
  exhausted = false;
  await f.controller.resume();
  assert.equal(f.controller.getState().status, 'complete');
  assert.equal(f.controller.getState().cached, 1);
});

test('new manifest version and cache clearing remove only this feature caches', async () => {
  const f = fixture();
  await (await f.caches.open('unrelated-application-cache')).put('/other', new Response('keep'));
  await f.controller.accept();
  const oldName = f.controller.getState().cacheName;
  const next = createAssetPrefetchController({ ...f.dependencies, manifest: { ...manifest, hash: 'new-release' } });
  await next.init();
  await next.resume();
  assert.notEqual(next.getState().cacheName, oldName);
  assert.equal(f.caches.entries.has(oldName), false);
  await (await f.caches.open(ASSET_METADATA_CACHE)).put('/metadata', new Response('metadata'));
  await next.clearCache();
  assert.deepEqual(await f.caches.keys(), ['unrelated-application-cache']);
  assert.equal(f.activations.at(-1), null);
  assert.equal(next.getState().status, 'paused');
});

test('malformed persisted preference is ignored without throwing on consent', async () => {
  for (const stored of ['42', '"string"', '[]', '{bad JSON']) {
    const f = fixture();
    f.saved.set('sp.pref.assetDownload', stored);
    const controller = createAssetPrefetchController(f.dependencies);
    await controller.accept();
    assert.equal(controller.getState().status, 'complete');
  }
});

test('paused accepted downloads invalidate old-version cached bytes without downloading', async () => {
  const f = fixture();
  await f.controller.accept();
  f.controller.pause();
  const previous = f.controller.getState().cacheName;
  const next = createAssetPrefetchController({ ...f.dependencies, manifest: { ...manifest, hash: 'paused-new-version' } });
  await next.init();
  assert.equal(next.getState().status, 'paused');
  assert.notEqual(next.getState().cacheName, previous);
  assert.equal(f.activations.at(-1), next.getState().cacheName);
  assert.equal(f.caches.entries.has(previous), false);
  assert.equal(f.requests.length, 4);
});

test('pausing during asynchronous cache lookup does not recreate a cache or overwrite paused status', async () => {
  const f = fixture();
  let finishLookup;
  const originalKeys = f.caches.keys.bind(f.caches);
  f.caches.keys = () => new Promise((resolve) => { finishLookup = async () => resolve(await originalKeys()); });
  const downloading = f.controller.accept();
  await waitUntil(() => finishLookup);
  f.controller.pause();
  await finishLookup();
  await downloading;
  assert.equal(f.controller.getState().status, 'paused');
  assert.equal(f.requests.length, 0);
  assert.deepEqual(await originalKeys(), []);
});

test('cache clearing serializes resume and waits for cancelled cache opens to settle', async () => {
  const f = fixture();
  let finishOpen;
  const originalOpen = f.caches.open.bind(f.caches);
  f.caches.open = (name) => new Promise((resolve) => { finishOpen = async () => resolve(await originalOpen(name)); });
  const downloading = f.controller.accept();
  await waitUntil(() => finishOpen);
  const clearing = f.controller.clearCache();
  const resumedWhileClearing = f.controller.resume();
  assert.equal(f.controller.getState().clearing, true);
  await finishOpen();
  await Promise.all([downloading, clearing, resumedWhileClearing]);
  assert.equal(f.controller.getState().status, 'paused');
  assert.equal(f.controller.getState().clearing, false);
  assert.equal(f.requests.length, 0);
  assert.deepEqual(await f.caches.keys(), []);
  f.caches.open = originalOpen;
  await f.controller.resume();
  assert.equal(f.controller.getState().status, 'complete');
});

test('worker activation timeout does not configure a cache later when ready eventually resolves', async () => {
  let becomeReady, posts = 0, channels = 0;
  const sw = { register: async () => {}, ready: new Promise((resolve) => { becomeReady = resolve; }),
    controller: { postMessage: () => { posts++; } },
    addEventListener: () => {}, removeEventListener: () => {} };
  class FakeChannel { constructor() { channels++; this.port1 = { close() {} }; this.port2 = {}; } }
  await assert.rejects(activateAssetCache(ASSET_CACHE_PREFIX + 'timeout', { navigator: { serviceWorker: sw }, timeoutMs: 5, MessageChannel: FakeChannel }), /timed out/);
  becomeReady();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(posts, 0);
  assert.equal(channels, 0);
});

test('worker accepts only same-origin resource GET requests and expected complete MIME', () => {
  assert.equal(isAssetRequest(new Request(`${ORIGIN}/healthz`), ORIGIN), false);
  assert.equal(isAssetRequest(new Request(`${ORIGIN}/js/main.js`), ORIGIN), false);
  assert.equal(isAssetRequest(new Request(`${ORIGIN}/assets/a.png`, { method: 'POST' }), ORIGIN), false);
  assert.equal(isAssetRequest(new Request('https://external.test/assets/a.png'), ORIGIN), false);
  assert.equal(isCacheableAssetResponse('/fonts/font.woff2', new Response('font', { headers: { 'content-type': 'font/woff2' } }), ORIGIN), true);
  assert.equal(isCacheableAssetResponse('/assets/model.skel', new Response('skeleton', { headers: { 'content-type': 'application/octet-stream' } }), ORIGIN), true);
  assert.equal(isCacheableAssetResponse('/media/voice/kr/op/cn_023', new Response('html', { headers: { 'content-type': 'text/html' } }), ORIGIN), false);
});

test('cached audio supports bounded, open-ended, suffix and unsatisfiable single ranges', async () => {
  const complete = () => new Response('0123456789', { headers: { 'content-type': 'audio/mpeg', 'content-encoding': 'gzip', 'content-length': '99' } });
  for (const [range, text, span] of [['bytes=2-4', '234', 'bytes 2-4/10'], ['bytes=7-', '789', 'bytes 7-9/10'],
    ['bytes=-4', '6789', 'bytes 6-9/10'], ['bytes=8-100', '89', 'bytes 8-9/10']]) {
    const result = await cachedRangeResponse(complete(), range);
    assert.equal(result.status, 206);
    assert.equal(result.headers.get('content-range'), span);
    assert.equal(result.headers.get('content-length'), String(text.length));
    assert.equal(result.headers.get('content-encoding'), null);
    assert.equal(await result.text(), text);
  }
  for (const range of ['bytes=10-', 'bytes=-0']) {
    const result = await cachedRangeResponse(complete(), range);
    assert.equal(result.status, 416);
    assert.equal(result.headers.get('content-range'), 'bytes */10');
    assert.equal(await result.text(), '');
  }
  assert.equal(await cachedRangeResponse(complete(), 'bytes=0-1,4-5'), null);
  assert.equal(await cachedRangeResponse(complete(), 'bytes=5-2'), null);
});

test('worker restart reads active metadata and serves cached audio ranges without network', async () => {
  const caches = new MemoryCacheStorage(), cacheName = ASSET_CACHE_PREFIX + 'fixture';
  const cache = await caches.open(cacheName);
  await cache.put('/media/voice/kr/op/cn_023', new Response('0123456789', { headers: { 'content-type': 'audio/mpeg' } }));
  let network = 0;
  const makeScope = () => {
    const handlers = new Map();
    const scope = { location: { origin: ORIGIN }, caches, handlers,
      addEventListener: (name, handler) => handlers.set(name, handler),
      skipWaiting: async () => {}, clients: { claim: async () => {} },
      fetch: async () => { network++; return new Response('NETWORK'); } };
    installAssetCacheWorker(scope);
    return scope;
  };
  const original = makeScope();
  let configured;
  original.handlers.get('message')({ data: { type: 'SP_ASSET_CACHE_CONFIGURE', cacheName },
    ports: [{ postMessage: (message) => assert.equal(message.ok, true) }], waitUntil: (promise) => { configured = promise; } });
  await configured;
  const restarted = makeScope();
  let answer;
  restarted.handlers.get('fetch')({ request: new Request(`${ORIGIN}/media/voice/kr/op/cn_023`, { headers: { Range: 'bytes=-3' } }),
    respondWith: (promise) => { answer = promise; } });
  const result = await answer;
  assert.equal(result.status, 206);
  assert.equal(await result.text(), '789');
  assert.equal(network, 0);
  restarted.handlers.get('fetch')({ request: new Request(`${ORIGIN}/assets/not-cached.png`), respondWith: (promise) => { answer = promise; } });
  assert.equal(await (await answer).text(), 'NETWORK');
  assert.equal(network, 1);
  answer = null;
  restarted.handlers.get('fetch')({ request: new Request(`${ORIGIN}/healthz`), respondWith: (promise) => { answer = promise; } });
  assert.equal(answer, null);
});
