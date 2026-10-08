// Transient team text chat and the compact match tools. Messages are rendered as Preact strings.
import { useEffect, useLayoutEffect, useRef, useState } from '../../vendor/hooks.module.js';
import { CHAT_MAX_LENGTH, CHAT_COOLDOWN_MS, CHAT_TTL_MS, normalizeChatText } from '../../../shared/chat.js';
import { t } from '../../../shared/i18n.js';
import { chatStore, sendTeamChat } from '../chat.js';
import { useStore } from '../store.js';
import { html, Icon } from './components.js';

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

/** Display lifetime is based on local receipt, so server/client clock differences do not extend it. */
export function visibleChatMessages(messages, now = Date.now()) {
  return (Array.isArray(messages) ? messages : []).filter((m) => {
    const expires = Number.isFinite(m?.expiresAt) ? m.expiresAt : m?.receivedAt + CHAT_TTL_MS;
    return Number.isFinite(expires) && expires > now;
  });
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
    case 'NOT_IN_ROOM': case 'SPECTATOR': return t('仅合作队伍成员可以聊天');
    default: return code ? t('聊天发送失败，请重试') : '';
  }
}

/** User-provided names and messages must remain text children; never insert HTML. */
export function TeamChatFeed({ messages = [], playerId, empty = false }) {
  return html`<div class="team-chat__feed" role="log" aria-label=${t('队伍聊天')} aria-live="polite" aria-relevant="additions" aria-atomic="false">
    ${messages.map((m) => html`<div key=${m.id} class=${`team-chat__message${m.playerId === playerId ? ' is-self' : ''}`}>
      <span class="team-chat__name">${m.name}</span><span class="team-chat__text">${m.text}</span>
    </div>`)}
    ${empty && messages.length === 0 ? html`<p class="team-chat__empty">${t('消息将在10秒后消失')}</p>` : null}
  </div>`;
}

function useDismissible({ rootRef, toggleRef, focusRef, open, onToggle }) {
  const latest = useRef(onToggle);
  latest.current = onToggle;
  useLayoutEffect(() => {
    if (!open) return undefined;
    const doc = rootRef.current?.ownerDocument;
    if (!doc) return undefined;
    focusRef?.current?.focus({ preventScroll: true });
    const outside = (e) => { if (!rootRef.current?.contains(e.target)) latest.current(false); };
    const escape = (e) => {
      if (e.key !== 'Escape' || doc.querySelector('.modal, .guide')) return;
      e.preventDefault();
      e.stopPropagation();
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

/** A co-op player sees an ephemeral feed even while the composer is closed. */
export function TeamChat({ open, onToggle, online, playerId, target = chatStore, onSend = sendTeamChat }) {
  const state = useStore((s) => s, undefined, target);
  const [text, setText] = useState('');
  const [error, setError] = useState(null);
  const [, redraw] = useState(0);
  const rootRef = useRef(null);
  const toggleRef = useRef(null);
  const inputRef = useRef(null);
  const pending = useRef(false);
  const now = Date.now();
  const messages = visibleChatMessages(state.messages, now);
  const send = chatSendState({ text, online, sending: state.sending || pending.current, lastSentAt: state.lastSentAt }, now);
  const chars = [...text].length;
  // The store prunes on receipt/expiry too; this also updates cooldown and injected development stores.
  useEffect(() => {
    const deadlines = visibleChatMessages(state.messages).map((m) => m.expiresAt ?? m.receivedAt + CHAT_TTL_MS);
    if (state.lastSentAt != null) deadlines.push(state.lastSentAt + CHAT_COOLDOWN_MS);
    const next = deadlines.filter((at) => at > Date.now()).sort((a, b) => a - b)[0];
    if (next == null) return undefined;
    const timer = setTimeout(() => redraw((n) => n + 1), Math.max(1, next - Date.now()));
    return () => clearTimeout(timer);
  }, [state.messages, state.lastSentAt, now]);
  useEffect(() => { ensureTeamChatCss(); }, []);
  useEffect(() => {
    const feed = rootRef.current?.querySelector('.team-chat__feed');
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [state.messages, open]);
  useDismissible({ rootRef, toggleRef, focusRef: inputRef, open, onToggle });

  const submit = async (e) => {
    e.preventDefault();
    const current = chatSendState({ text, online, sending: state.sending || pending.current, lastSentAt: state.lastSentAt });
    if (current.disabled) { setError(current.reason); return; }
    pending.current = true;
    setError(null);
    redraw((n) => n + 1);
    try {
      await onSend(current.text);
      setText('');
    } catch (err) { setError(err.code || 'FAILED'); }
    finally { pending.current = false; redraw((n) => n + 1); }
  };
  const storedError = state.error === 'RATE' && send.reason !== 'RATE' ? null : state.error;
  const localError = error === 'RATE' && send.reason !== 'RATE' ? null : error;
  const note = chatErrorLabel(send.reason || localError || storedError);

  return html`<div class=${`team-chat${open ? ' is-open' : ''}`} ref=${rootRef}
    onKeyDown=${(e) => { if (open || (e.target === toggleRef.current && [' ', 'Enter'].includes(e.key))) e.stopPropagation(); }}>
    ${open || messages.length ? html`<section id="team-chat-panel" class=${`team-chat__panel${open ? '' : ' is-feed-only'}`} aria-label=${t('队伍聊天')}>
      <${TeamChatFeed} messages=${messages} playerId=${playerId} empty=${open} />
      ${open ? html`<form class="team-chat__form" onSubmit=${submit}>
        <label class="team-chat__label" for="team-chat-input">${t('队伍聊天')}</label>
        <div class="team-chat__compose">
          <input id="team-chat-input" class="team-chat__input" ref=${inputRef} type="text" value=${text} maxlength=${CHAT_MAX_LENGTH * 2}
            placeholder=${t('输入消息，Enter发送')} autocomplete="off" spellcheck=${false} aria-describedby="team-chat-note"
            onInput=${(e) => { setText(e.currentTarget.value); setError(null); }} />
          <button type="submit" class="team-chat__send" disabled=${send.disabled}>${t('发送')}</button>
        </div>
        <div class="team-chat__meta"><span id="team-chat-note" class="team-chat__note" role="status">${note || t('消息将在10秒后消失')}</span>
          <span class=${`team-chat__count${chars > CHAT_MAX_LENGTH ? ' is-long' : ''}`} aria-hidden="true">${chars}/${CHAT_MAX_LENGTH}</span></div>
      </form>` : null}
    </section>` : null}
    <button type="button" class="team-chat__toggle" ref=${toggleRef} aria-expanded=${open ? 'true' : 'false'} aria-controls="team-chat-panel"
      aria-label=${t('队伍聊天')} title=${t('队伍聊天')} onClick=${() => onToggle(!open)}>
      <svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 4h16v12H9l-5 4zm2 2v10l2-2h10V6z" fill-rule="evenodd" /></svg>
      <span>${t('聊天')}</span>
    </button>
  </div>`;
}

/** One-button-width disclosure; existing settings, guide and fullscreen buttons are its children. */
export function MatchTools({ open, onToggle, children }) {
  const rootRef = useRef(null);
  const toggleRef = useRef(null);
  const menuRef = useRef(null);
  useEffect(() => { ensureTeamChatCss(); }, []);
  useEffect(() => { if (open) menuRef.current?.querySelector('button:not(:disabled)')?.focus({ preventScroll: true }); }, [open]);
  useDismissible({ rootRef, toggleRef, open, onToggle });
  const keys = (e) => {
    if (!open) {
      if (e.target === toggleRef.current && [' ', 'Enter'].includes(e.key)) e.stopPropagation();
      return;
    }
    e.stopPropagation();
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    const buttons = [...menuRef.current.querySelectorAll('button:not(:disabled)')];
    if (!buttons.length) return;
    e.preventDefault();
    const index = buttons.indexOf(menuRef.current.ownerDocument.activeElement);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : index < 0 ? (e.key === 'ArrowUp' ? buttons.length - 1 : 0)
      : (index + (e.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next].focus();
  };
  return html`<div class=${`gm__tools${open ? ' is-open' : ''}`} ref=${rootRef} onKeyDown=${keys}>
    ${open ? html`<div id="gm-tools-menu" class="gm__tools-menu" ref=${menuRef} role="group" aria-label=${t('游戏工具')}
      onClick=${(e) => { if (e.target.closest('button')) onToggle(false); }}>${children}</div>` : null}
    <button type="button" class="gm__gear gm__tools-toggle" ref=${toggleRef} aria-expanded=${open ? 'true' : 'false'}
      aria-controls="gm-tools-menu" aria-label=${t(open ? '收起游戏工具' : '展开游戏工具')} title=${t('游戏工具')}
      onClick=${() => onToggle(!open)}><${Icon} name="chevronRight" /></button>
  </div>`;
}
