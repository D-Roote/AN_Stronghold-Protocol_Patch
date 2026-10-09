// Room-owned briefing votes; game state and membership are always checked on the server.
import { ERR, PHASE } from '../shared/constants.js';
import { sendSession } from './net.js';

export const RESTART_COVER_MS = 240;
const votes = new WeakMap();
const OK = Object.freeze({ ok: true });
const fail = (error) => ({ error });
const humans = (room) => room.activeHumans();
const matchNow = (lobby, match) => match.sched?.now?.() ?? lobby.now();

export function restartVoteState(room) {
  if (!room.match) return {};
  if (room.match.phase !== PHASE.INFO_CHECK) return { matchNo: room.matchCount };
  const record = votes.get(room);
  const people = humans(room);
  return { matchNo: room.matchCount, restartVote: {
    total: people.length,
    voters: record?.match === room.match ? people.filter((s) => s.connected && record.voters.has(s.playerId)).map((s) => s.playerId) : [],
    requested: !!(record?.match === room.match),
    pending: !!(record?.match === room.match && record.pending),
  } };
}

/** Stop deferred work before a room or its current match is disposed. */
export function clearRestartVote(room) {
  const record = votes.get(room);
  if (!record) return false;
  clearTimeout(record.timer);
  record.match.restartPending = false;
  votes.delete(room);
  return true;
}

function cancelTransition(lobby, room, record) {
  clearTimeout(record.timer);
  record.timer = null;
  record.pending = false;
  const match = record.match;
  match.restartPending = false;
  if (room.match !== match || match.disposed || match.ended || match.phase !== PHASE.INFO_CHECK) return;
  const remaining = record.deadline ? Math.max(.001, (record.deadline - matchNow(lobby, match)) / 1000) : 0;
  match.setDeadline(remaining, () => match.enterBandDraft());
  match.markPublic();
  match.flush(true);
  match.maybeEndInfo();
}

/** Called when room membership or the match phase changes; never restart merely because somebody left. */
export function syncRestartVote(lobby, room) {
  const record = votes.get(room);
  if (!record) return false;
  if (room.disposed || room.match !== record.match || room.match.phase !== PHASE.INFO_CHECK) return clearRestartVote(room);
  const people = humans(room);
  const connected = new Set(people.filter((s) => s.connected).map((s) => s.playerId));
  for (const id of record.voters) if (!connected.has(id)) record.voters.delete(id);
  if (record.pending && (record.voters.size !== people.length || people.some((s) => !s.connected))) cancelTransition(lobby, room, record);
  return false;
}

function announce(lobby, room, seat) {
  const event = { t: 'room.restart', code: room.code, seq: ++room.chatSeq,
    playerId: seat.playerId, name: seat.name, at: lobby.now() };
  for (const teammate of humans(room)) {
    const peer = teammate.connected ? lobby.registry.byId(teammate.playerId) : null;
    if (peer?.connected && peer.roomCode === room.code) sendSession(peer, event);
  }
}

/** Explicit agree/withdraw plus match number makes duplicate and stale requests safe. */
export function voteRestart(lobby, session, msg) {
  const room = lobby.roomOf(session);
  if (!room || room.disposed) return fail(ERR.NOT_IN_ROOM);
  const seat = room.seatOf(session.playerId);
  if (!seat) return fail(room.spectatorOf(session.playerId) ? ERR.SPECTATOR : ERR.NOT_IN_ROOM);
  if (room.mode !== 'coop' || seat.isBot || seat.left || !seat.connected) return fail(ERR.NOT_IN_ROOM);
  const match = room.match;
  if (!match || match.disposed || match.ended || match.phase !== PHASE.INFO_CHECK || msg.matchNo !== room.matchCount) return fail(ERR.WRONG_PHASE);
  if (typeof msg.agree !== 'boolean' || typeof match.setDeadline !== 'function') return fail(ERR.BAD_MSG);
  syncRestartVote(lobby, room);
  let record = votes.get(room);
  if (!record) {
    if (!msg.agree) return OK;
    record = { match, voters: new Set(), pending: false, timer: null, deadline: 0 };
    votes.set(room, record);
    announce(lobby, room, seat);
  }
  if (msg.agree === record.voters.has(session.playerId)) return OK;
  if (msg.agree) record.voters.add(session.playerId);
  else record.voters.delete(session.playerId);
  if (record.pending) cancelTransition(lobby, room, record);
  const people = humans(room);
  if (people.length && people.every((s) => s.connected && record.voters.has(s.playerId))) {
    record.pending = true;
    record.deadline = match.deadline;
    match.restartPending = true;
    match.setDeadline(0);
    match.markPublic();
    match.flush(true);
    const ctx = room.matchCtx;
    record.timer = setTimeout(() => {
      syncRestartVote(lobby, room);
      if (votes.get(room) !== record || !record.pending || room.matchCtx !== ctx) return;
      const previousDisabledBonds = Array.isArray(match.disabledBonds) ? match.disabledBonds.slice() : [];
      const key = room.matchKey;
      clearRestartVote(room);
      lobby.disposeMatchCtx(ctx);
      room.match = null;
      room.matchCtx = null;
      room.matchKey = null;
      for (const s of room.seats) if (s && !s.isBot) s.ready = false;
      lobby.startMatch(room, key, { previousDisabledBonds });
    }, RESTART_COVER_MS);
    record.timer.unref?.();
  }
  lobby.broadcastState(room);
  return OK;
}
