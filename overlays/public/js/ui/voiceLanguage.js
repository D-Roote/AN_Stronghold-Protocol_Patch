import { html, MicroLabel } from './components.js';
import { createStore, useStore, loadPref, savePref } from '../store.js';
import { audio } from '../audio.js';
import { VOICE_LANGUAGES, normalizeVoiceLanguage } from '../../../shared/voiceLanguages.js';
import { t } from '../../../shared/i18n.js';

export const voiceLanguageStore = createStore({ lang: normalizeVoiceLanguage(loadPref('voiceLanguage', null)) });
voiceLanguageStore.subscribe(({ lang }) => {
  savePref('voiceLanguage', lang);
  audio.setVoiceLanguage(lang);
});
audio.setVoiceLanguage(voiceLanguageStore.get().lang);

export function updateVoiceLanguage(lang) {
  voiceLanguageStore.set({ lang: normalizeVoiceLanguage(lang) });
}

export function VoiceLanguageSetting() {
  const lang = useStore((s) => s.lang, Object.is, voiceLanguageStore);
  return html`<div class="set-row">
    <span class="set-row__label">${t('干员配音语言')}<${MicroLabel}>VOICE LANGUAGE<//></span>
    <div class="set-seg set-voice" role="radiogroup" aria-label=${t('干员配音语言')} data-testid="voice-language">
      ${VOICE_LANGUAGES.map(({ id, label }) => html`<button key=${id} type="button" role="radio"
        data-voice-lang=${id} aria-checked=${lang === id ? 'true' : 'false'} class=${lang === id ? 'is-on' : ''}
        onClick=${() => updateVoiceLanguage(id)}><span class="set-voice__label">${label}</span></button>`)}
    </div>
  </div><p class="set-hint">${t('所选语言没有该干员的语音时保持静音。')}</p>`;
}
