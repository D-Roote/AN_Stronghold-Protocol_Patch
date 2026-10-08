// Session team chat, faction marker picker and the compact match tools.
import { useEffect, useLayoutEffect, useRef, useState } from '../../vendor/hooks.module.js';
import {
  CHAT_MAX_LENGTH, CHAT_COOLDOWN_MS, CHAT_CLOSED_PREVIEW_MS, CHAT_FACTIONS,
  normalizeChatText, chatFaction,
} from '../../../shared/chat.js';
import { t } from '../../../shared/i18n.js';
import { chatStore, sendTeamChat, sendTeamFaction } from '../chat.js';
import { useStore } from '../store.js';
import { html, Icon } from './components.js';
import { useData } from '../data.js';
import { emoteUiSprite } from './emotes.js';

export const TEAM_CHAT_CSS_HREF = '/css/team-chat.css';

/** Also load the stylesheet in development harnesses that do not use the main index. */
export function ensureTeamChatCss(doc = globalThis.document) {
  if (!doc?.head || typeof doc.querySelector !== 'function') return false;
  if (doc.querySelector(`link[rel="stylesheet"][href$="${TEAM_CHAT_CSS_HREF}"]`)) return false;
  const link = doc.createElement('link');
  link.rel = 'stylesheet';
  link.href = TEAM_CHAT_CSS_HREF;
  doc.head.appendChild(link);
  return true;
}

/** Freeze the rendered history at close time; later store updates cannot alter this shallow snapshot. */
export function makeClosedChatPreview(messages, now = Date.now(), factions = {}, scrollTop = 0) {
  return { messages: Array.isArray(messages) ? [...messages] : [], factions: { ...factions }, scrollTop, expiresAt: now + CHAT_CLOSED_PREVIEW_MS };
}

export function closedChatPreviewMessages(preview, now = Date.now()) {
  return preview && Number.isFinite(preview.expiresAt) && preview.expiresAt > now ? preview.messages : [];
}

/** The same state drives the disabled button and submit guard (Enter included). */
export function chatSendState({ text = '', online = false, sending = false, lastSentAt = null } = {}, now = Date.now()) {
  const normalized = normalizeChatText(text);
  const elapsed = lastSentAt == null ? Infinity : now - lastSentAt;
  const cooling = elapsed >= 0 && elapsed < CHAT_COOLDOWN_MS;
  const reason = !online ? 'OFFLINE' : sending ? 'SENDING' : cooling ? 'RATE' : text.trim() && normalized === null ? 'BAD_MSG' : null;
  return { disabled: !!reason || normalized === null, reason, text: normalized };
}

export function chatErrorLabel(code) {
  switch (code) {
    case 'OFFLINE': case 'CLOSED': return t('连接断开，暂时无法发送聊天');
    case 'SENDING': return t('发送中…');
    case 'RATE': return t('请等待1秒后再发送');
    case 'BAD_MSG': return t('请输入1至200字的聊天内容');
    case 'BAD_FACTION': return t('请选择有效阵营');
    case 'NOT_IN_ROOM': case 'SPECTATOR': return t('仅合作队伍成员可以聊天');
    default: return code ? t('聊天发送失败，请重试') : '';
  }
}

/** Keep translation ids literal so the strict catalogue check can discover every faction label. */
export function factionLabel(value) {
  switch (value) {
    case 'yanShip': return t('炎');
    case 'sargonShip': return t('萨尔贡');
    case 'victoriaShip': return t('维多利亚');
    case 'kjeragShip': return t('谢拉格');
    case 'lateranoShip': return t('拉特兰');
    case 'egirShip': return t('阿戈尔');
    case 'siracusaShip': return t('叙拉古');
    case 'kazimierzShip': return t('卡西米尔');
    default: return '';
  }
}

export function FactionTag({ faction }) {
  const meta = chatFaction(faction);
  return meta ? html`<span class="team-chat__faction" style=${`--faction-color:${meta.color}`}>(${factionLabel(meta.id)})</span>` : null;
}

/** User-provided names and messages must remain text children; never insert HTML. */
export function TeamChatFeed({ messages = [], factions = {}, playerId, onScroll = undefined }) {
  return html`<div class="team-chat__feed" role="log" aria-label=${t('队伍聊天')} aria-live="polite" aria-relevant="additions" aria-atomic="false" onScroll=${onScroll}>
    ${messages.map((message) => {
      const faction = chatFaction(message.kind === 'faction' ? message.faction : factions[message.playerId]);
      if (message.kind === 'faction' && faction) {
        return html`<div key=${message.id} class="team-chat__message is-system" style=${`--faction-color:${faction.color}`}>
          <span class="team-chat__system-dot" aria-hidden="true"></span>
          <span>${t('{name}博士选择了{faction}阵营', { name: message.name, faction: factionLabel(faction.id) })}</span>
        </div>`;
      }
      return html`<div key=${message.id} class=${`team-chat__message${message.playerId === playerId ? ' is-self' : ''}`}>
        <span class="team-chat__author"><span class="team-chat__name">${message.name}</span><${FactionTag} faction=${faction?.id} /></span>
        <span class="team-chat__text">${message.text}</span>
      </div>`;
    })}
  </div>`;
}

function FactionPicker({ selected, disabled, onSelect }) {
  return html`<div id="team-chat-picker" class="team-chat__picker" role="dialog" aria-label=${t('表情与阵营')}>
    <div class="team-chat__picker-head">
      <strong>${t('选择阵营')}</strong>
      <span>${t('表情功能稍后提供')}</span>
    </div>
    <div class="team-chat__factions" role="radiogroup" aria-label=${t('选择阵营')}>
      ${CHAT_FACTIONS.map((faction) => html`<button key=${faction.id} type="button" role="radio"
        class=${`team-chat__faction-option${selected === faction.id ? ' is-selected' : ''}`}
        style=${`--faction-color:${faction.color}`} aria-checked=${selected === faction.id ? 'true' : 'false'}
        disabled=${disabled || selected === faction.id} onClick=${() => onSelect(faction.id)}>
        <span class="team-chat__faction-swatch" aria-hidden="true"></span><span>${factionLabel(faction.id)}</span>
      </button>`)}
    </div>
  </div>`;
}

function useDismissible({ rootRef, toggleRef, focusRef, open, onToggle, onEscape }) {
  const latest = useRef(onToggle);
  const escapeFirst = useRef(onEscape);
  latest.current = onToggle;
  escapeFirst.current = onEscape;
  useLayoutEffect(() => {
    if (!open) return undefined;
    const doc = rootRef.current?.ownerDocument;
    if (!doc) return undefined;
    focusRef?.current?.focus({ preventScroll: true });
    const outside = (event) => { if (!rootRef.current?.contains(event.target)) latest.current(false); };
    const escape = (event) => {
      if (event.key !== 'Escape' || doc.querySelector('.modal, .guide')) return;
      event.preventDefault();
      event.stopPropagation();
      if (escapeFirst.current?.()) return;
      latest.current(false);
      toggleRef.current?.focus({ preventScroll: true });
    };
    doc.addEventListener('pointerdown', outside, true);
    doc.addEventListener('keydown', escape, true);
    return () => {
      doc.removeEventListener('pointerdown', outside, true);
      doc.removeEventListener('keydown', escape, true);
    };
  }, [open]);
}

/** Open history is retained in bounded memory; closing freezes a ten-second visual snapshot. */
export function TeamChat({
  open, onToggle, online, playerId, target = chatStore, onSend = sendTeamChat, onSelectFaction = sendTeamFaction,
}) {
  useData('local');
  const state = useStore((value) => value, undefined, target);
  const [text, setText] = useState('');
  const [error, setError] = useState(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [, redraw] = useState(0);
  const rootRef = useRef(null);
  const toggleRef = useRef(null);
  const inputRef = useRef(null);
  const pending = useRef(false);
  const wasOpen = useRef(open);
  const messageRef = useRef(state.messages || []);
  const factionRef = useRef(state.factions || {});
  const previewTimer = useRef(null);
  const feedScroll = useRef(0);
  messageRef.current = state.messages || [];
  factionRef.current = state.factions || {};
  const now = Date.now();
  const messages = open ? messageRef.current : closedChatPreviewMessages(preview, now);
  const send = chatSendState({ text, online, sending: state.sending || pending.current, lastSentAt: state.lastSentAt }, now);
  const factionAction = chatSendState({ text: 'faction', online, sending: state.sending || pending.current, lastSentAt: state.lastSentAt }, now);
  const chars = [...text].length;

  useLayoutEffect(() => {
    if (previewTimer.current != null) clearTimeout(previewTimer.current);
    previewTimer.current = null;
    if (open) setPreview(null);
    else if (wasOpen.current) {
      const frozen = makeClosedChatPreview(messageRef.current, Date.now(), factionRef.current, feedScroll.current);
      setPreview(frozen);
      previewTimer.current = setTimeout(() => { previewTimer.current = null; setPreview(null); }, CHAT_CLOSED_PREVIEW_MS);
    }
    wasOpen.current = open;
  }, [open]);
  useEffect(() => () => { if (previewTimer.current != null) clearTimeout(previewTimer.current); }, []);
  useEffect(() => { if (!open) setPickerOpen(false); }, [open]);
  useEffect(() => {
    const deadline = state.lastSentAt == null ? null : state.lastSentAt + CHAT_COOLDOWN_MS;
    if (deadline == null || deadline <= Date.now()) return undefined;
    const timer = setTimeout(() => redraw((value) => value + 1), Math.max(1, deadline - Date.now()));
    return () => clearTimeout(timer);
  }, [state.lastSentAt, now]);
  useEffect(() => { ensureTeamChatCss(); }, []);
  useLayoutEffect(() => {
    const feed = rootRef.current?.querySelector('.team-chat__feed');
    if (!feed) return;
    feed.scrollTop = open ? feed.scrollHeight : preview?.scrollTop || 0;
    if (open) feedScroll.current = feed.scrollTop;
  }, [state.messages, preview, open]);
  useDismissible({
    rootRef, toggleRef, focusRef: inputRef, open, onToggle,
    onEscape: () => { if (!pickerOpen) return false; setPickerOpen(false); return true; },
  });

  const submit = async (event) => {
    event.preventDefault();
    const current = chatSendState({ text, online, sending: state.sending || pending.current, lastSentAt: state.lastSentAt });
    if (current.disabled) { setError(current.reason); return; }
    pending.current = true;
    setError(null);
    redraw((value) => value + 1);
    try {
      await onSend(current.text);
      setText('');
    } catch (err) { setError(err.code || 'FAILED'); }
    finally { pending.current = false; redraw((value) => value + 1); }
  };
  const selectFaction = async (faction) => {
    if (factionAction.disabled) { setError(factionAction.reason); return; }
    pending.current = true;
    setError(null);
    redraw((value) => value + 1);
    try {
      await onSelectFaction(faction);
      setPickerOpen(false);
      inputRef.current?.focus({ preventScroll: true });
    } catch (err) { setError(err.code || 'FAILED'); }
    finally { pending.current = false; redraw((value) => value + 1); }
  };
  const storedError = state.error === 'RATE' && send.reason !== 'RATE' ? null : state.error;
  const localError = error === 'RATE' && send.reason !== 'RATE' ? null : error;
  const note = chatErrorLabel(send.reason || localError || storedError);
  const selectedFaction = state.factions?.[playerId] || null;

  return html`<div class=${`team-chat${open ? ' is-open' : ''}${emoteUiSprite('emoji_btn') ? ' has-emote-sprite' : ''}`} ref=${rootRef}
    onKeyDown=${(event) => { if (open || (event.target === toggleRef.current && [' ', 'Enter'].includes(event.key))) event.stopPropagation(); }}>
    ${open || messages.length ? html`<section id="team-chat-panel" class=${`team-chat__panel${open ? '' : ' is-feed-only'}`} aria-label=${t('队伍聊天')}>
      <${TeamChatFeed} messages=${messages} factions=${open ? state.factions || {} : preview?.factions || {}} playerId=${playerId}
        onScroll=${(event) => { if (open) feedScroll.current = event.currentTarget.scrollTop; }} />
      ${open ? html`<form class="team-chat__form" onSubmit=${submit}>
        <div class="team-chat__compose">
          <input id="team-chat-input" class="team-chat__input" ref=${inputRef} type="text" value=${text} maxlength=${CHAT_MAX_LENGTH * 2}
            placeholder=${t('输入消息，Enter发送')} autocomplete="off" spellcheck=${false} aria-label=${t('队伍聊天')}
            aria-describedby=${note ? 'team-chat-note' : undefined}
            onInput=${(event) => { setText(event.currentTarget.value); setError(null); }} />
          <span class="team-chat__picker-wrap">
            <button type="button" class=${`team-chat__picker-toggle${pickerOpen ? ' is-open' : ''}`}
              aria-expanded=${pickerOpen ? 'true' : 'false'} aria-controls="team-chat-picker" aria-haspopup="dialog"
              aria-label=${t('表情与阵营')} title=${t('表情与阵营')} onClick=${() => setPickerOpen((value) => !value)}>
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8.5" />
                <path d="M8.5 10h.01M15.5 10h.01M8.5 14c1.9 2 5.1 2 7 0" /></svg>
            </button>
            ${pickerOpen ? html`<${FactionPicker} selected=${selectedFaction} disabled=${factionAction.disabled} onSelect=${selectFaction} />` : null}
          </span>
          <button type="submit" class="team-chat__send" disabled=${send.disabled}>${t('发送')}</button>
        </div>
        <div class="team-chat__meta">
          ${note ? html`<span id="team-chat-note" class="team-chat__note" role="status">${note}</span>`
            : html`<span class="team-chat__note">${t('关闭聊天后，消息将在10秒后隐藏')}</span>`}
          <span class=${`team-chat__count${chars > CHAT_MAX_LENGTH ? ' is-long' : ''}`} aria-hidden="true">${chars}/${CHAT_MAX_LENGTH}</span>
        </div>
      </form>` : null}
    </section>` : null}
    <button type="button" class="team-chat__toggle" ref=${toggleRef} aria-expanded=${open ? 'true' : 'false'} aria-controls="team-chat-panel"
      aria-label=${t('队伍聊天')} title=${t('队伍聊天')} onClick=${() => onToggle(!open)}>
      <svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 4h16v12H9l-5 4zm2 2v10l2-2h10V6z" fill-rule="evenodd" /></svg>
      <span>${t('聊天')}</span>
    </button>
  </div>`;
}

/** Existing settings, guide and fullscreen controls expand horizontally to the toggle's right. */
export function MatchTools({ open, onToggle, children }) {
  useData('local');
  const rootRef = useRef(null);
  const toggleRef = useRef(null);
  const menuRef = useRef(null);
  useEffect(() => { ensureTeamChatCss(); }, []);
  useLayoutEffect(() => { if (open) menuRef.current?.querySelector('button:not(:disabled)')?.focus({ preventScroll: true }); }, [open]);
  useDismissible({ rootRef, toggleRef, open, onToggle });
  const keys = (event) => {
    if (!open) {
      if (event.target === toggleRef.current && [' ', 'Enter'].includes(event.key)) event.stopPropagation();
      return;
    }
    event.stopPropagation();
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...menuRef.current.querySelectorAll('button:not(:disabled)')];
    if (!buttons.length) return;
    event.preventDefault();
    const index = buttons.indexOf(menuRef.current.ownerDocument.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : index < 0 ? (event.key === 'ArrowLeft' ? buttons.length - 1 : 0)
        : (index + (event.key === 'ArrowLeft' ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next].focus();
  };
  return html`<div class=${`gm__tools${open ? ' is-open' : ''}${emoteUiSprite('emoji_btn') ? ' has-emote-sprite' : ''}`} ref=${rootRef} onKeyDown=${keys}>
    <button type="button" class="gm__gear gm__tools-toggle" ref=${toggleRef} aria-expanded=${open ? 'true' : 'false'}
      aria-controls="gm-tools-menu" aria-label=${t(open ? '收起游戏工具' : '展开游戏工具')} title=${t('游戏工具')}
      onClick=${() => onToggle(!open)}><${Icon} name="chevronRight" /></button>
    ${open ? html`<div id="gm-tools-menu" class="gm__tools-menu" ref=${menuRef} role="group" aria-label=${t('游戏工具')}
      onClick=${(event) => { if (event.target.closest('button')) onToggle(false); }}>${children}</div>` : null}
  </div>`;
}
