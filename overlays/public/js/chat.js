// Session-local team chat: no storage, offline queue, match state or replay history.
import { createStore } from './store.js';
import { NetError } from './net.js';
import { CHAT_COOLDOWN_MS, CHAT_TTL_MS, CHAT_HISTORY_LIMIT, normalizeChatText, canTeamChat } from '../../shared/chat.js';

export { canTeamChat } from '../../shared/chat.js';
const fresh = () => ({ messages: [], lastSentAt: null, error: null, sending: false });
export const chatStore = createStore(fresh());
let installed = null;

/** Install once beside the main network wiring; return its cleanup function. */
export function installTeamChat({ net, store, target = chatStore, now = Date.now, timers = globalThis }) {
  installed?.dispose();
  let disposed = false;
  let generation = 0;
  let timer = null;
  const context = (s) => ({ playerId: s.me?.playerId || null, code: s.room?.code || null, active: canTeamChat(s.room, s.me?.playerId) });
  let current = context(store.get());
  const same = (a, b) => a.playerId === b.playerId && a.code === b.code && a.active === b.active;
  const clearTimer = () => { if (timer != null) timers.clearTimeout(timer); timer = null; };
  const reset = () => { generation++; clearTimer(); target.set(fresh()); };
  reset();
  const prune = () => {
    clearTimer();
    if (disposed) return;
    const state = target.get();
    const messages = state.messages.filter((m) => m.expiresAt > now());
    if (messages.length !== state.messages.length) target.set({ messages });
    if (messages.length) timer = timers.setTimeout(prune, Math.max(1, messages[0].expiresAt - now()));
  };
  const offStore = store.subscribe((s) => {
    const next = context(s);
    if (!same(next, current)) { current = next; reset(); }
  });
  const offChat = net.on('room.chat', (msg) => {
    if (disposed || !current.active || msg.code !== current.code || !Number.isSafeInteger(msg.seq) || msg.seq < 1
      || typeof msg.playerId !== 'string' || typeof msg.name !== 'string' || msg.name.length > 64 || !Number.isFinite(msg.at)) return;
    const text = normalizeChatText(msg.text);
    const sender = store.get().room?.seats?.find((s) => s?.playerId === msg.playerId && !s.isBot && !s.left && s.connected);
    if (!sender || text === null) return;
    const receivedAt = now();
    const id = `${msg.code}:${msg.seq}`;
    const previous = target.get().messages.filter((m) => m.expiresAt > receivedAt);
    if (previous.some((m) => m.id === id)) return;
    const message = { id, code: msg.code, seq: msg.seq, playerId: msg.playerId, name: msg.name, text, at: msg.at, receivedAt, expiresAt: receivedAt + CHAT_TTL_MS };
    target.set({ messages: [...previous, message].slice(-CHAT_HISTORY_LIMIT) });
    prune();
  });
  const controller = {
    async send(text) {
      const normalized = normalizeChatText(text);
      const state = target.get();
      let error = null;
      if (net.status !== 'online') error = 'OFFLINE';
      else if (!current.active) error = 'NOT_IN_ROOM';
      else if (normalized === null) error = 'BAD_MSG';
      else if (state.sending || (state.lastSentAt != null && now() - state.lastSentAt < CHAT_COOLDOWN_MS)) error = 'RATE';
      if (error) { target.set({ error }); throw new NetError(error); }
      const started = generation;
      target.set({ sending: true, error: null });
      try {
        const reply = await net.request('room.chat', { text: normalized }, { queue: false });
        if (!disposed && started === generation) target.set({ lastSentAt: now(), error: null });
        return reply;
      } catch (err) {
        if (!disposed && started === generation) target.set({ error: err.code || 'OFFLINE' });
        throw err;
      } finally {
        if (!disposed && started === generation) target.set({ sending: false });
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      offStore(); offChat(); reset();
      if (installed === controller) installed = null;
    },
  };
  installed = controller;
  return () => controller.dispose();
}

/** Send plain text immediately through the currently installed controller. */
export function sendTeamChat(text) {
  return installed ? installed.send(text) : Promise.reject(new NetError('OFFLINE'));
}
