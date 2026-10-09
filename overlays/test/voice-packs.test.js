import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { planVoicePack, fetchVoicePacks } from '../tools/fetch-voice-packs.mjs';
import { collectLeaves } from '../tools/assets/manifest.mjs';
import { collectAssetUrls } from '../public/js/assetPrefetch.js';

const id = 'char_102_texas';
const url = (lang, line = '023') => `/assets/audio/voice/${lang}/${id}/cn_${line}.mp3`;
const word = (line, placeType, charId = id) => ({ charId, wordKey: charId,
  voiceId: `CN_${line}`, voiceAsset: `${charId}/CN_${line}`, placeType, voiceIndex: Number(line) });
const words = { charWords: { a: word('023', 'BATTLE_SELECT'), b: word('024', 'BATTLE_SELECT'),
  c: word('023', 'BATTLE_PLACE'), unused: word('001', 'GACHA'), outside: word('023', 'BATTLE_SELECT', 'char_other'),
  unsafe: { ...word('025', 'BATTLE_START'), voiceAsset: `${id}/../CN_025` } } };

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'sp-voice-packs-'));
  const path = join(root, 'data/assets.json');
  mkdirSync(join(root, 'data'), { recursive: true });
  const put = (lang, line = '023') => {
    const file = join(root, 'public', url(lang, line).slice(1));
    mkdirSync(join(file, '..'), { recursive: true });
    writeFileSync(file, 'voice');
  };
  put('kr');
  const manifest = { version: 1, hash: 'old', generator: 'fixture', stats: { bytes: 5, files: 1 },
    chars: { [id]: { name: 'Texas' } }, audio: { voice: { [id]: { select: url('kr'), place: url('kr') } } } };
  writeFileSync(path, JSON.stringify(manifest));
  return { root, path, put, manifest, clean: () => rmSync(root, { recursive: true, force: true }) };
}

test('additional dubs plan only battle slots of the current cast, using shared CN IDs and JP source folder', () => {
  const plan = planVoicePack({ chars: { [id]: {} } }, words, 'jp');
  assert.deepEqual(Object.keys(plan), [id]);
  assert.deepEqual(Object.keys(plan[id]), ['select', 'place']);
  const leaves = collectLeaves(plan);
  assert.equal(leaves.length, 3);
  assert.ok(leaves.every(({ leaf }) => leaf.alts[0].rel.startsWith(`audio/voice/jp/${id}/cn_`)));
  assert.ok(leaves.every(({ leaf }) => leaf.alts[0].urls[0].includes(`/sound_beta_2/voice/${id}/cn_`)));
  assert.throws(() => planVoicePack({}, words, 'ko'), /Unsupported voice language/);
});

test('publish both banks atomically, deduplicate reused KR files, and include JP in browser prefetch', async () => {
  const f = fixture();
  try {
    f.put('jp'); f.put('jp', '024');
    const options = { offline: true, charword: words, log: () => {} };
    assert.deepEqual(await fetchVoicePacks(f.root, options), { kr: { chars: 1, files: 1 }, jp: { chars: 1, files: 2 } });
    const raw = readFileSync(f.path, 'utf8');
    const result = JSON.parse(raw);
    assert.deepEqual(result.audio.voice, f.manifest.audio.voice);
    assert.deepEqual(result.audio.voicePacks.kr, f.manifest.audio.voice);
    assert.deepEqual(result.audio.voicePacks.jp[id].select, [url('jp'), url('jp', '024')]);
    assert.equal(result.audio.defaultVoiceLang, 'kr');
    assert.equal(result.stats.files, 3);
    assert.equal(result.stats.bytes, 15);
    assert.notEqual(result.hash, 'old');
    assert.equal(collectAssetUrls(result, null).length, 3);
    assert.ok(collectAssetUrls(result, null).some((entry) => entry.includes('/voice/jp/')));
    await fetchVoicePacks(f.root, options);
    assert.equal(readFileSync(f.path, 'utf8'), raw, 'repeating preparation preserves the hash and manifest');
  } finally { f.clean(); }
});

test('definitively missing individual JP lines are omitted, without filling them with KR audio', async () => {
  const f = fixture();
  try {
    f.put('jp');
    await fetchVoicePacks(f.root, { offline: true, charword: words, log: () => {} });
    const result = JSON.parse(readFileSync(f.path, 'utf8'));
    assert.deepEqual(result.audio.voicePacks.jp[id].select, [url('jp')]);
    assert.equal(result.stats.voicePacks.jp.files, 1);
    assert.equal(result.stats.files, 2);
  } finally { f.clean(); }
});

test('transient download failures and empty JP packs leave the published primary manifest untouched', async () => {
  const f = fixture();
  try {
    const original = readFileSync(f.path, 'utf8');
    await assert.rejects(fetchVoicePacks(f.root, { charword: words, source: 'direct', log: () => {},
      download: async () => ['char_102_texas.select'] }), /transient failures/);
    assert.equal(readFileSync(f.path, 'utf8'), original);
    await assert.rejects(fetchVoicePacks(f.root, { offline: true, charword: words, log: () => {} }), /JP voice pack validation failed/);
    assert.equal(readFileSync(f.path, 'utf8'), original);
  } finally { f.clean(); }
});
