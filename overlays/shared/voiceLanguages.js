import { languageName } from './i18nPacks.js';

export const DEFAULT_VOICE_LANGUAGE = 'kr';
export const VOICE_LANGUAGES = Object.freeze([
  Object.freeze({ id: 'kr', label: languageName('ko') }),
  Object.freeze({ id: 'jp', label: languageName('ja') }),
]);

export function normalizeVoiceLanguage(value) {
  return VOICE_LANGUAGES.some(({ id }) => id === value) ? value : DEFAULT_VOICE_LANGUAGE;
}

/** Keep the legacy default bank usable; a missing selected dub stays silent. */
export function voiceBank(manifest, language = DEFAULT_VOICE_LANGUAGE) {
  const lang = normalizeVoiceLanguage(language);
  const audio = manifest?.audio;
  const bank = audio?.voicePacks?.[lang] ?? (lang === DEFAULT_VOICE_LANGUAGE ? audio?.voice : null);
  return bank && typeof bank === 'object' && !Array.isArray(bank) ? bank : {};
}
