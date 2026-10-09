#!/usr/bin/env node
// Add selectable dubs to an already downloaded asset manifest. Download only voice files.
import { readFile, writeFile, rename, mkdtemp, rm } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { realpathSync } from 'node:fs';
import { indexVoice, VOICE_DIRS, VOICE_BATTLE_SLOTS } from './assets/audio.mjs';
import { RAW, normalizeProxyPrefix } from './assets/sources.mjs';
import { cachedJson } from './assets/cache.mjs';
import { Downloader } from './assets/downloader.mjs';
import { MirrorPolicy, selectDownloadSource, validateSource } from './assets/network.mjs';
import { collectLeaves, downloadLeaves, resolveTemplate, totalBytes, contentHash } from './assets/manifest.mjs';
import { kindOf } from './assets/formats.mjs';
import { checkVoices } from './check-voices.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const validLanguage = (lang) => typeof lang === 'string' && Object.hasOwn(VOICE_DIRS, lang);

/** Plan only the battle slots of operators this manifest can render. */
export function planVoicePack(manifest, charword, lang) {
  if (!validLanguage(lang)) throw new Error(`Unsupported voice language: ${lang}`);
  const template = {};
  for (const [charId, slots] of indexVoice(charword, 'CN', VOICE_BATTLE_SLOTS)) {
    if (!manifest.chars?.[charId] || !/^[a-z0-9_]+$/i.test(charId)) continue;
    const bank = {};
    for (const [slot, assets] of Object.entries(slots)) {
      const lines = [];
      for (const asset of assets) {
        const [owner, voiceId, extra] = String(asset).split('/');
        if (owner !== charId || extra !== undefined || !/^CN_\d+$/i.test(voiceId || '')) continue;
        const file = `${charId}/${voiceId.toLowerCase()}.mp3`;
        const rel = `audio/voice/${lang}/${file}`;
        lines.push({ alts: [{ rel, urls: [RAW.aa2voice + VOICE_DIRS[lang] + '/' + file], kind: kindOf(rel) }] });
      }
      if (lines.length) bank[slot] = lines.length === 1 ? lines[0] : lines;
    }
    if (Object.keys(bank).length) template[charId] = bank;
  }
  return template;
}

/**
 * Keep the primary bank and atomically publish the complete selected pack set.
 * @param {string} root
 * @param {{baseLang?: string, languages?: string[], charword?: any, offline?: boolean,
 *   source?: 'direct'|'mirror', log?: (line: string) => void, download?: typeof downloadLeaves}} [options]
 */
export async function fetchVoicePacks(root = ROOT, options = {}) {
  const baseLang = options.baseLang || 'kr';
  const languages = [...new Set(options.languages || ['kr', 'jp'])];
  if (!validLanguage(baseLang) || !languages.length || languages.some((lang) => !validLanguage(lang))) {
    throw new Error('Voice pack languages must be cn, jp, en or kr');
  }
  const log = options.log || console.log;
  const path = join(root, 'data/assets.json');
  const manifest = JSON.parse(await readFile(path, 'utf8'));
  const primary = checkVoices({ audio: { voice: manifest.audio?.voice } }, baseLang, root);
  if (primary.errors.length) throw new Error(`Primary ${baseLang.toUpperCase()} voice bank is incomplete`);
  const sourceMode = options.source || process.env.SP_ASSET_SOURCE || 'direct';
  validateSource(sourceMode);
  const proxyPrefix = options.offline || sourceMode !== 'mirror' ? '' : normalizeProxyPrefix(process.env.SP_GITHUB_PROXY);
  const source = await selectDownloadSource({ mode: sourceMode, offline: !!options.offline, proxyPrefix, log });
  const mirrorPolicy = new MirrorPolicy({ source, proxyPrefix, log });
  const network = { source, proxyPrefix, mirrorPolicy };
  const charword = options.charword || await cachedJson({
    cacheFile: join(root, '.cache/gamedata/excel/charword_table.json'),
    url: RAW.gamedata + 'excel/charword_table.json', offline: !!options.offline, log, ...network,
  });
  const assets = join(root, 'public/assets');
  const dl = new Downloader({ root: assets, ledgerPath: join(root, '.cache/voice-packs-ledger.json'), log, ...network });
  await dl.loadLedger();
  const banks = { ...manifest.audio?.voicePacks, [baseLang]: manifest.audio.voice };
  for (const lang of languages) {
    if (lang === baseLang) continue;
    if (lang === 'jp' && manifest.audio?.voiceJp) {
      const native = checkVoices({ audio: { voicePacks: { jp: manifest.audio.voiceJp } } }, 'jp', root);
      if (!native.errors.length) {
        banks.jp = manifest.audio.voiceJp;
        log(`[voice-packs] JP: reuse the validated upstream voice pack (${native.files} files)`);
        continue;
      }
    }
    const template = planVoicePack(manifest, charword, lang);
    const leaves = collectLeaves(template);
    log(`[voice-packs] ${lang.toUpperCase()}: ${leaves.length} planned battle lines`);
    if (!options.offline) {
      const failures = await (options.download || downloadLeaves)(leaves, dl, assets, `${lang.toUpperCase()} voice`);
      if (failures.length) throw new Error(`${lang.toUpperCase()} voice downloads had ${failures.length} transient failures; retry the build`);
    }
    banks[lang] = resolveTemplate(template, { root: assets, spine: new Map() }).value;
  }
  const counts = {};
  for (const lang of new Set([baseLang, ...languages])) {
    const checked = checkVoices({ audio: { voicePacks: banks } }, lang, root);
    if (checked.errors.length) throw new Error(`${lang.toUpperCase()} voice pack validation failed: ${checked.errors.slice(0, 3).join('; ')}`);
    counts[lang] = { chars: checked.chars, files: checked.files };
    log(`[voice-packs] ${lang.toUpperCase()}: ${checked.chars} operators, ${checked.files} files`);
  }
  const { version, hash: _hash, generator: _generator, stats, ...body } = manifest;
  body.audio = { ...body.audio, voicePacks: banks, defaultVoiceLang: baseLang };
  const files = new Set();
  const walk = (value) => {
    if (typeof value === 'string' && value.startsWith('/assets/') && !value.includes('..') && !value.includes('\\')) files.add(value.slice('/assets/'.length));
    else if (value && typeof value === 'object') Object.values(value).forEach(walk);
  };
  walk(body);
  const result = { version, hash: contentHash(body), generator: 'tools/fetch-voice-packs.mjs',
    stats: { ...stats, bytes: totalBytes(assets, files), files: files.size, voicePacks: counts }, ...body };
  const temporary = await mkdtemp(join(root, 'data/.voice-packs-'));
  try {
    const staged = join(temporary, 'assets.json');
    await writeFile(staged, JSON.stringify(result, null, 2) + '\n');
    await rename(staged, path);
  } finally { await rm(temporary, { recursive: true, force: true }); }
  return counts;
}

const invoked = (() => { try { return pathToFileURL(realpathSync(process.argv[1] || '')).href; } catch { return null; } })();
if (invoked === import.meta.url) {
  try {
    const options = {};
    for (const arg of process.argv.slice(2)) {
      if (arg.startsWith('--base-lang=')) options.baseLang = arg.slice('--base-lang='.length);
      else if (arg.startsWith('--languages=')) options.languages = arg.slice('--languages='.length).split(',');
      else if (arg === '--offline') options.offline = true;
      else throw new Error('Usage: fetch-voice-packs.mjs --base-lang=kr --languages=kr,jp [--offline]');
    }
    await fetchVoicePacks(ROOT, options);
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
