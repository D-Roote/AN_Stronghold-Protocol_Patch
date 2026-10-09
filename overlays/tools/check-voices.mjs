// Check a downloaded manifest before publishing an image with a different dub.
// CN_* is the shared voice slot name, including in the Korean dump; the directory selects the dub.
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const LANGUAGES = new Set(['cn', 'jp', 'en', 'kr']);

/** Validate the dub and files actually referenced by the manifest, without changing it. */
export function checkVoices(manifest, lang, root = ROOT) {
  if (!LANGUAGES.has(lang)) throw new Error(`Unsupported voice language: ${lang}`);
  const errors = [];
  const files = new Set();
  const bank = manifest.audio?.voicePacks?.[lang] ?? manifest.audio?.voice ?? {};
  const chars = Object.keys(bank);
  const walk = (value, key) => {
    if (typeof value === 'string') {
      if (!value.startsWith(`/assets/audio/voice/${lang}/`) || value.includes('..') || value.includes('\\')) {
        errors.push(`${key}: unexpected voice URL ${value}`);
      } else {
        files.add(value);
        if (!existsSync(join(root, 'public', value.slice(1)))) errors.push(`${key}: missing file ${value}`);
      }
    } else if (value && typeof value === 'object') {
      for (const [k, v] of Object.entries(value)) walk(v, `${key}.${k}`);
    } else {
      errors.push(`${key}: invalid voice entry`);
    }
  };
  walk(bank, `audio.voicePacks.${lang}`);
  if (!files.size) errors.push(`No ${lang.toUpperCase()} voice files in the manifest`);
  return { lang, chars: chars.length, files: files.size, errors };
}

const invoked = (() => { try { return pathToFileURL(realpathSync(process.argv[1] || '')).href; } catch { return null; } })();
if (invoked === import.meta.url) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 1 || !args[0].startsWith('--lang=')) throw new Error('Usage: node tools/check-voices.mjs --lang=cn|jp|en|kr');
    const lang = args[0].slice('--lang='.length);
    const result = checkVoices(JSON.parse(readFileSync(join(ROOT, 'data/assets.json'), 'utf8')), lang);
    console.log(`Voices: ${lang.toUpperCase()} · ${result.chars} operators · ${result.files} files · ${result.errors.length} errors`);
    for (const error of result.errors.slice(0, 20)) console.error(error);
    if (result.errors.length) process.exitCode = 1;
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
