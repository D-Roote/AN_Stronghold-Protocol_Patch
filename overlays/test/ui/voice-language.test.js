import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AudioManager } from '../../public/js/audio.js';
import { DEFAULT_VOICE_LANGUAGE, VOICE_LANGUAGES, normalizeVoiceLanguage, voiceBank, migrateVoiceSettings } from '../../shared/voiceLanguages.js';

const kr = '/assets/audio/voice/kr/char_a/cn_023.mp3';
const jp = '/assets/audio/voice/jp/char_a/cn_023.mp3';
const manifest = { audio: { voice: { char_legacy: { select: kr } }, voicePacks: {
  kr: { char_a: { select: kr, place: kr }, char_kr_only: { select: kr } }, jp: { char_a: { select: jp, place: jp } },
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
  manager.setVoiceLang(DEFAULT_VOICE_LANGUAGE);
  manager.ctx = {}; manager.voiceGain = {};
  const requested = [];
  manager._playVoice = (url) => requested.push(url);
  const volumes = { ...manager.volumes };
  manager.bgmKey = 'combat';
  assert.equal(manager.voice('char_a', 'select', { unitKey: 1 }), true);
  const oldToken = manager.voiceToken;
  manager.setVoiceLang('jp');
  assert.ok(manager.voiceToken > oldToken, 'old deferred decodes are invalidated');
  assert.equal(manager.voice('char_a', 'select', { unitKey: 1 }), true, 'the same slot is immediately usable in JP');
  assert.deepEqual(requested, [kr, jp]);
  assert.equal(manager.voice('char_kr_only', 'select'), false);
  const current = manager.voiceToken;
  manager.setVoiceLang('jp');
  assert.equal(manager.voiceToken, current, 'reselecting the active language keeps its line playing');
  manager.setVoiceLang('kr');
  assert.deepEqual(manager.volumes, volumes);
  assert.equal(manager.bgmKey, 'combat');
});

test('an old KR decode resolving after a JP switch cannot start a sound or release the new channel', async () => {
  const manager = new AudioManager({ win: null, getManifest: () => manifest });
  manager.setVoiceLang(DEFAULT_VOICE_LANGUAGE);
  let resolveOld;
  manager._buffer = () => new Promise((resolve) => { resolveOld = resolve; });
  let sources = 0;
  manager.ctx = { createBufferSource: () => { sources++; throw new Error('stale decode must not create a source'); } };
  manager.voiceGain = {};
  assert.equal(manager.voice('char_a', 'place'), true);
  manager.setVoiceLang('jp');
  manager._playVoice = () => {};
  assert.equal(manager.voice('char_a', 'place'), true);
  resolveOld({ duration: 1 });
  await Promise.resolve();
  assert.equal(sources, 0);
  assert.equal(manager.voice('char_a', 'place'), false, 'the JP line still holds the gate');
});

test('native per-operator overrides select KR/JP packs and changing one invalidates the previous voice', () => {
  const id = 'char_263_skadi';
  const packs = { audio: { voicePacks: { kr: { [id]: manifest.audio.voicePacks.kr.char_a },
    jp: { [id]: manifest.audio.voicePacks.jp.char_a } } } };
  const manager = new AudioManager({ win: null, getManifest: () => packs });
  manager.setVoiceLang('kr', { [id]: 'jp' });
  manager.ctx = {}; manager.voiceGain = {};
  const requested = [];
  manager._playVoice = (url) => requested.push(url);
  assert.equal(manager.voice(id, 'select'), true);
  assert.equal(requested[0], jp, 'the operator override wins over the global KR setting');
  const previous = manager.voiceToken;
  manager.setVoiceLang('kr', { [id]: 'kr' });
  assert.ok(manager.voiceToken > previous);
  assert.equal(manager.voice(id, 'select'), true);
  assert.equal(requested[1], kr);
  manager.setVoiceLang('jp', {});
  assert.equal(manager.voice(id, 'select'), true);
  assert.equal(requested[2], jp, 'removing an override follows the current global choice');
  assert.equal(manager.voice('char_kr_only', 'select'), false, 'a missing dub does not fall back to another pack');
});

test('upgrading preserves the old JP preference once, while new settings take precedence', () => {
  assert.deepEqual(migrateVoiceSettings(null, 'jp'), { voiceLang: 'jp' });
  assert.deepEqual(migrateVoiceSettings({ bgm: .3 }, 'jp'), { bgm: .3, voiceLang: 'jp' });
  assert.deepEqual(migrateVoiceSettings({ voiceLang: 'kr', muted: true }, 'jp'), { voiceLang: 'kr', muted: true });
  assert.deepEqual(migrateVoiceSettings(null, null), { voiceLang: 'kr' });
  assert.deepEqual(migrateVoiceSettings({ voiceLang: 'cn' }, 'jp'), { voiceLang: 'kr' });
});
