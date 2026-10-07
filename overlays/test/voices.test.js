import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { checkVoices } from '../tools/check-voices.mjs';

test('a KR manifest uses shared cn_* slot names, checks every file, and counts reused lines once', () => {
  const root = mkdtempSync(join(tmpdir(), 'sp-voices-'));
  try {
    const dir = join(root, 'public/assets/audio/voice/kr/char_102_texas');
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'cn_023.mp3'), 'fixture');
    const url = '/assets/audio/voice/kr/char_102_texas/cn_023.mp3';
    const manifest = { audio: { voice: { char_102_texas: { select: [url], place: [url] } } } };
    assert.deepEqual(checkVoices(manifest, 'kr', root), { lang: 'kr', chars: 1, files: 1, errors: [] });
    manifest.audio.voice.char_102_texas.place.push('/assets/audio/voice/kr/char_102_texas/cn_024.mp3');
    assert.match(checkVoices(manifest, 'kr', root).errors.join('\n'), /missing file.*cn_024/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('a stale or mixed CN manifest cannot pass a KR build check', () => {
  const manifest = { audio: { voice: { char_102_texas: { select: ['/assets/audio/voice/cn/char_102_texas/cn_023.mp3'] } } } };
  assert.match(checkVoices(manifest, 'kr').errors.join('\n'), /unexpected voice URL.*\/voice\/cn\//);
  assert.match(checkVoices({ audio: { voice: {} } }, 'kr').errors.join('\n'), /No KR voice files/);
  assert.throws(() => checkVoices(manifest, 'ko'), /Unsupported voice language/);
  manifest.audio.voice.char_102_texas.select = ['/assets/audio/voice/kr/../../secret'];
  assert.match(checkVoices(manifest, 'kr').errors.join('\n'), /unexpected voice URL/);
});
