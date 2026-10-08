import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../public/js/store.js';
import { Net } from '../public/js/net.js';
import { installTeamChat, sendTeamChat } from '../public/js/chat.js';

function clockTimers() {
  let time = 1000; let next = 0;
  const jobs = new Map();
  return {
    now: () => time,
    setTimeout(fn, ms) { const id = ++next; jobs.set(id, { fn, at: time + ms }); return id; },
    clearTimeout(id) { jobs.delete(id); },
    setInterval() { return 0; }, clearInterval() {},
    advance(ms) {
      const end = time + ms;
      for (;;) {
        const due = [...jobs].filter(([, j]) => j.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, job] = due; jobs.delete(id); time = job.at; job.fn();
      }
      time = end;
    },
    count: () => jobs.size,
  };
}
const own = { playerId: 'p_me', name: 'Me', connected: true, isBot: false };
const peer = { playerId: 'p_peer', name: 'Peer', connected: true, isBot: false };
const room = (code = 'ABCD') => ({ code, mode: 'coop', seats: [own, peer], spectators: [] });
function setup() {
  const timers = clockTimers();
  const store = createStore({ me: { playerId: own.playerId }, room: room(), match: { unchanged: true } });
  const target = createStore({ messages: [], lastSentAt: null, error: null, sending: false });
  const listeners = new Map();
  const requests = [];
  const net = {
    status: 'online',
    on(t, fn) { listeners.set(t, fn); return () => listeners.delete(t); },
    request(t, fields, opts) { requests.push({ t, fields, opts }); return Promise.resolve({ t: 'ok' }); },
  };
  const dispose = installTeamChat({ net, store, target, timers, now: timers.now });
  const emit = (seq, fields = {}) => listeners.get('room.chat')?.({ t: 'room.chat', code: 'ABCD', seq, playerId: peer.playerId, name: peer.name, text: '안녕', at: 1, ...fields });
  return { timers, store, target, net, requests, emit, dispose };
}

describe('isolated team chat controller', () => {
  test('TTL uses local receive time, bounds history, deduplicates and never changes match state', () => {
    const c = setup();
    try {
      const match = c.store.get().match;
      for (let seq = 1; seq <= 60; seq++) c.emit(seq, { at: 9e12 });
      assert.equal(c.target.get().messages.length, 50); assert.equal(c.target.get().messages[0].seq, 11);
      c.emit(60); assert.equal(c.target.get().messages.length, 50);
      assert.equal(c.store.get().match, match);
      c.timers.advance(9999); assert.equal(c.target.get().messages.length, 50);
      c.timers.advance(1); assert.deepEqual(c.target.get().messages, []); assert.equal(c.timers.count(), 0);
    } finally { c.dispose(); }
  });
  test('room, identity and spectator transitions clear messages; unchanged room resync retains them', () => {
    const c = setup();
    try {
      c.emit(1); c.store.set({ room: room() }); assert.equal(c.target.get().messages.length, 1);
      c.store.set({ room: { ...room(), seats: [{ ...own, connected: false }, peer] } });
      assert.equal(c.target.get().messages.length, 1, 'temporary disconnect does not change membership');
      c.store.set({ room: room() });
      c.emit(2, { code: 'OTHER' }); c.emit(3, { playerId: 'ai_bot' }); assert.equal(c.target.get().messages.length, 1);
      c.store.set({ room: room('EFGH') }); assert.deepEqual(c.target.get().messages, []);
      c.emit(4); assert.deepEqual(c.target.get().messages, [], 'late previous-room frame discarded');
      c.store.set({ room: room() }); c.emit(5);
      c.store.set({ me: { playerId: 'p_new' } }); assert.deepEqual(c.target.get().messages, []);
      c.store.set({ me: { playerId: own.playerId } }); c.emit(6);
      c.store.set({ room: { ...room(), seats: [peer], spectators: [own] } }); assert.deepEqual(c.target.get().messages, []);
      c.emit(7); assert.deepEqual(c.target.get().messages, []);
    } finally { c.dispose(); }
  });
  test('send is normalized, unqueued, cooldown bounded, and reports errors immediately', async () => {
    const c = setup();
    try {
      await sendTeamChat(' 안녕\n친구 ');
      assert.deepEqual(c.requests, [{ t: 'room.chat', fields: { text: '안녕 친구' }, opts: { queue: false } }]);
      c.timers.advance(999); await assert.rejects(sendTeamChat('second'), { code: 'RATE' });
      c.timers.advance(1); await sendTeamChat('second'); assert.equal(c.requests.length, 2);
      c.net.status = 'reconnecting'; await assert.rejects(sendTeamChat('offline'), { code: 'OFFLINE' });
      assert.equal(c.requests.length, 2); assert.equal(c.target.get().error, 'OFFLINE');
      c.net.status = 'online'; c.timers.advance(1000); await assert.rejects(sendTeamChat(' '), { code: 'BAD_MSG' });
    } finally { c.dispose(); }
    await assert.rejects(sendTeamChat('uninstalled'), { code: 'OFFLINE' });
  });
  test('late request completion cannot restore cleared state after a room change', async () => {
    const c = setup();
    let resolve;
    c.net.request = () => new Promise((r) => { resolve = r; });
    try {
      const pending = sendTeamChat('old room'); assert.equal(c.target.get().sending, true);
      c.store.set({ room: room('EFGH') }); resolve({ t: 'ok' }); await pending;
      assert.equal(c.target.get().lastSentAt, null); assert.equal(c.target.get().sending, false);
      assert.deepEqual(c.target.get().messages, []);
    } finally { c.dispose(); }
  });
});

test('Net queue:false never retains offline or failed-send requests; normal queueing still works', async () => {
  const timers = clockTimers(); const sent = [];
  const net = new Net({ timers, now: timers.now });
  net.name = 'Me'; net.status = 'handshaking';
  await assert.rejects(net.request('room.chat', { text: 'offline' }, { queue: false }), { code: 'OFFLINE' });
  assert.equal(net.pendingCount, 0);
  assert.equal(net._pending.size, 0);
  net.status = 'online'; net.ws = { readyState: 0, send: (raw) => sent.push(JSON.parse(raw)) };
  await assert.rejects(net.request('room.chat', { text: 'send race' }, { queue: false }), { code: 'OFFLINE' });
  assert.equal(net._pending.size, 0); assert.equal(timers.count(), 0);
  net.ws.readyState = 1; net._flushQueue(); assert.deepEqual(sent, []);
  net.status = 'handshaking';
  const ordinary = net.request('room.ready', { ready: true });
  assert.equal(net._pending.size, 1);
  net.status = 'online'; net._flushQueue(); assert.equal(sent[0].t, 'room.ready');
  net._onMessage(JSON.stringify({ t: 'ok', rid: sent[0].rid })); await ordinary;
  assert.equal(net._pending.size, 0); assert.equal(timers.count(), 0);
});
