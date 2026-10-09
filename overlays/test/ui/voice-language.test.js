import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AudioManager } from '../../public/js/audio.js';
import { DEFAULT_VOICE_LANGUAGE, VOICE_LANGUAGES, normalizeVoiceLanguage, voiceBank } from '../../shared/voiceLanguages.js';

const kr = '/assets/audio/voice/kr/char_a/cn_023.mp3';
const jp = '/assets/audio/voice/jp/char_a/cn_023.mp3';
const manifest = { audio: { voice: { char_legacy: { select: kr } }, voicePacks: {
  kr: { char_a: { select: kr }, char_kr_only: { select: kr } }, jp: { char_a: { select: jp } },
} } };

test('voice choice defaults to KR, validates stored values, and never mixes in a different dub', () => {
  assert.equal(DEFAULT_VOICE_LANGUAGE, 'kr');
  assert.deepEqual(VOICE_LANGUAGES.map(({ id, label }) => [id, label]), [['kr', '한국어'], ['jp', '日本語']]);
  for (const input of [null, undefined, 'ko', 'cn', {}, '']) assert.equal(normalizeVoiceLanguage(input), 'kr');
  assert.equal(normalizeVoiceLanguage('jp'), 'jp');
  assert.equal(voiceBank(manifest, 'jp').char_a.select, jp);
  assert.equal(voiceBank(manifest, 'jp').char_kr_only, undefined);
  assert.deepEqual(voiceBank({ audio: { voice: { char_a: { select: kr } } } }, 'jp'), {});
  assert.equal(voiceBank({ audio: { voice: { char_a: { select: kr } } } }, 'kr').char_a.select, kr);
});

test('switching languages resets the voice cooldown and affects only the voice channel', () => {
  const manager = new AudioManager({ win: null, getManifest: () => manifest });
  manager.ctx = {}; manager.voiceGain = {};
  const requested = [];
  manager._playVoice = (url) => requested.push(url);
  const volumes = { ...manager.volumes };
  manager.bgmKey = 'combat';
  assert.equal(manager.voice('char_a', 'select', { unitKey: 1 }), true);
  const oldToken = manager.voiceToken;
  manager.setVoiceLanguage('jp');
  assert.ok(manager.voiceToken > oldToken, 'old deferred decodes are invalidated');
  assert.equal(manager.voice('char_a', 'select', { unitKey: 1 }), true, 'the same slot is immediately usable in JP');
  assert.deepEqual(requested, [kr, jp]);
  assert.equal(manager.voice('char_kr_only', 'select'), false);
  const current = manager.voiceToken;
  manager.setVoiceLanguage('jp');
  assert.equal(manager.voiceToken, current, 'reselecting the active language keeps its line playing');
  manager.setVoiceLanguage('kr');
  assert.deepEqual(manager.volumes, volumes);
  assert.equal(manager.bgmKey, 'combat');
});

test('an old KR decode resolving after a JP switch cannot start a sound or release the new channel', async () => {
  const manager = new AudioManager({ win: null, getManifest: () => manifest });
  let resolveOld;
  manager._buffer = () => new Promise((resolve) => { resolveOld = resolve; });
  let sources = 0;
  manager.ctx = { createBufferSource: () => { sources++; throw new Error('stale decode must not create a source'); } };
  manager.voiceGain = {};
  assert.equal(manager.voice('char_a', 'select'), true);
  manager.setVoiceLanguage('jp');
  manager._playVoice = () => {};
  assert.equal(manager.voice('char_a', 'select'), true);
  resolveOld({ duration: 1 });
  await Promise.resolve();
  assert.equal(sources, 0);
  assert.equal(manager.voice('char_a', 'select'), false, 'the JP line still holds the gate');
});
