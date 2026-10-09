// Session-local team chat: no storage, offline queue, match state or replay history.
import { createStore } from './store.js';
import { NetError } from './net.js';
import { setupRerollTransitionKey } from './setupRerollTransition.js';
import { PHASE } from '../../shared/constants.js';
import {
  CHAT_COOLDOWN_MS, CHAT_HISTORY_LIMIT, normalizeChatText, normalizeChatFaction, canTeamChat,
} from '../../shared/chat.js';

export { canTeamChat } from '../../shared/chat.js';
const fresh = () => ({ messages: [], factions: {}, lastSentAt: null, error: null, sending: false });
export const chatStore = createStore(fresh());
let installed = null;

/** Install once beside the main network wiring; return its cleanup function. */
export function installTeamChat({ net, store, target = chatStore, now = Date.now }) {
  installed?.dispose();
  let disposed = false;
  let generation = 0;
  let observedVote = null;
  let restartSequence = 0;
  const context = (s) => ({ playerId: s.me?.playerId || null, code: s.room?.code || null, active: canTeamChat(s.room, s.me?.playerId) });
  let current = context(store.get());
  const same = (a, b) => a.playerId === b.playerId && a.code === b.code && a.active === b.active;
  const reset = () => { generation++; observedVote = null; restartSequence = 0; target.set(fresh()); };
  const syncFactions = (room) => {
    const factions = {};
    for (const seat of Array.isArray(room?.seats) ? room.seats : []) {
      const faction = normalizeChatFaction(seat?.faction);
      if (seat?.playerId && !seat.isBot && !seat.left && faction) factions[seat.playerId] = faction;
    }
    const previous = target.get().factions || {};
    const keys = Object.keys(factions);
    if (keys.length !== Object.keys(previous).length || keys.some((key) => previous[key] !== factions[key])) target.set({ factions });
  };
  reset();
  syncFactions(store.get().room);
  const senderFor = (msg) => store.get().room?.seats?.find((seat) => seat?.playerId === msg.playerId
    && !seat.isBot && !seat.left && seat.connected !== false);
  const validEnvelope = (msg) => !disposed && current.active && msg.code === current.code
    && Number.isSafeInteger(msg.seq) && msg.seq >= 1 && typeof msg.playerId === 'string'
    && typeof msg.name === 'string' && msg.name.length <= 64 && Number.isFinite(msg.at) && senderFor(msg);
  const append = (message) => {
    const previous = target.get().messages || [];
    if (previous.some((item) => item.id === message.id)) return false;
    target.set({ messages: [...previous, message].slice(-CHAT_HISTORY_LIMIT) });
    return true;
  };
  // The upstream vote belongs to m.public. Display its first request without a second server vote protocol.
  const announceReroll = (proposer) => append({ id: `${current.code}:setup-reroll:${++restartSequence}`,
    kind: 'restart', code: current.code, playerId: proposer.playerId, name: proposer.name, at: now(), receivedAt: now() });
  const rerollSnapshot = (s) => ({ code: s?.room?.code, inMatch: !!s?.room?.inMatch,
    phase: s?.match?.public?.phase, revision: s?.match?.public?.setupRevision });
  const syncReroll = (s, previous) => {
    const pub = s.match?.public;
    if (!current.active || !s.room?.inMatch || (pub && pub.phase !== PHASE.INFO_CHECK)) {
      observedVote = null;
      return;
    }
    const vote = pub?.rerollVote;
    if (!vote) {
      // One human (with AI teammates) completes the native vote without publishing a pending vote.
      if (!previous || !same(context(previous), current) || previous.match?.public?.rerollVote
        || !setupRerollTransitionKey(rerollSnapshot(previous), rerollSnapshot(s))) return;
      const humans = s.room.seats.filter((seat) => seat && !seat.isBot && !seat.left);
      const proposer = humans[0];
      if (humans.length === 1 && proposer.playerId === s.room.hostId && proposer.connected !== false
        && typeof proposer.name === 'string' && proposer.name.length <= 64
        && pub.players?.some((player) => player.playerId === proposer.playerId && !player.isBot)) announceReroll(proposer);
      return;
    }
    if (!Number.isSafeInteger(vote.id) || vote.id < 1
      || !Number.isSafeInteger(pub.setupRevision) || pub.setupRevision < 0
      || !Array.isArray(vote.voters) || !vote.voters.includes(vote.proposerId)) return;
    const proposer = s.room.seats.find((seat) => seat?.playerId === vote.proposerId && !seat.isBot && !seat.left);
    if (!proposer || typeof proposer.name !== 'string' || proposer.name.length > 64) return;
    const key = `${current.code}:${pub.setupRevision}:${vote.id}`;
    if (observedVote === key) return;
    observedVote = key;
    announceReroll(proposer);
  };
  syncReroll(store.get());
  const offStore = store.subscribe((s, previous) => {
    const next = context(s);
    if (!same(next, current)) { current = next; reset(); }
    syncFactions(s.room);
    syncReroll(s, previous);
  });
  const offChat = net.on('room.chat', (msg) => {
    if (!validEnvelope(msg)) return;
    const text = normalizeChatText(msg.text);
    if (text === null) return;
    append({ id: `${msg.code}:${msg.seq}`, kind: 'chat', code: msg.code, seq: msg.seq,
      playerId: msg.playerId, name: msg.name, text, at: msg.at, receivedAt: now() });
  });
  const offFaction = net.on('room.faction', (msg) => {
    if (!validEnvelope(msg)) return;
    const faction = normalizeChatFaction(msg.faction);
    if (!faction) return;
    const added = append({ id: `${msg.code}:${msg.seq}`, kind: 'faction', code: msg.code, seq: msg.seq,
      playerId: msg.playerId, name: msg.name, faction, at: msg.at, receivedAt: now() });
    if (added) target.set((state) => ({ factions: { ...(state.factions || {}), [msg.playerId]: faction } }));
  });
  const request = async (type, fields) => {
    const state = target.get();
    let error = null;
    if (net.status !== 'online') error = 'OFFLINE';
    else if (!current.active) error = 'NOT_IN_ROOM';
    else if (state.sending || (state.lastSentAt != null && now() - state.lastSentAt < CHAT_COOLDOWN_MS)) error = 'RATE';
    if (error) { target.set({ error }); throw new NetError(error); }
    const started = generation;
    target.set({ sending: true, error: null });
    try {
      const reply = await net.request(type, fields, { queue: false });
      if (!disposed && started === generation) target.set({ lastSentAt: now(), error: null });
      return reply;
    } catch (err) {
      if (!disposed && started === generation) target.set({ error: err.code || 'OFFLINE' });
      throw err;
    } finally {
      if (!disposed && started === generation) target.set({ sending: false });
    }
  };
  const controller = {
    send(text) {
      const normalized = normalizeChatText(text);
      if (normalized === null) { target.set({ error: 'BAD_MSG' }); return Promise.reject(new NetError('BAD_MSG')); }
      return request('room.chat', { text: normalized });
    },
    selectFaction(value) {
      const faction = normalizeChatFaction(value);
      if (!faction) { target.set({ error: 'BAD_FACTION' }); return Promise.reject(new NetError('BAD_FACTION')); }
      return request('room.faction', { faction });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      offStore(); offChat(); offFaction(); reset();
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

/** Select a validated core faction through the same one-second interaction cooldown. */
export function sendTeamFaction(faction) {
  return installed ? installed.selectFaction(faction) : Promise.reject(new NetError('OFFLINE'));
}
