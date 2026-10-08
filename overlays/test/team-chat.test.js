import { describe, test, before, beforeEach, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { startServer } from '../server/index.js';
import { StubMatch } from '../server/match/StubMatch.js';
import { TestClient } from './helpers/wsClient.js';
import { ERR, EMOTES } from '../shared/constants.js';
import { normalizeChatText } from '../shared/chat.js';
import { validateC2S } from '../shared/protocol.js';

describe('team chat: authoritative ephemeral room traffic', () => {
  let srv;
  let clock;
  const clients = new Set();
  const errors = [];
  before(async () => {
    srv = await startServer({ port: 0, host: '127.0.0.1', MatchClass: StubMatch,
      log: { info() {}, warn() {}, debug() {}, error: (...a) => errors.push(a.map(String).join(' ')) } });
    srv.lobby.now = () => clock;
  });
  beforeEach(() => { clock = 1_000_000; });
  afterEach(async () => { await Promise.all([...clients].map((c) => c.terminate())); clients.clear(); });
  after(async () => { await srv.close(); assert.deepEqual(errors, []); });
  async function player(name, token) {
    const c = await TestClient.connect(`ws://127.0.0.1:${srv.port}/ws`);
    clients.add(c);
    const w = await c.hello(name, token);
    c.id = w.playerId; c.token = w.token;
    return c;
  }
  async function ok(c, msg) { const r = await c.request(msg); assert.equal(r.t, 'ok', JSON.stringify(r)); return r; }
  async function error(c, msg, code) { const r = await c.request(msg); assert.equal(r.t, 'error', JSON.stringify(r)); assert.equal(r.code, code); }
  async function room(c, mode = 'coop') {
    await ok(c, { t: 'room.create', mode, difficulty: 'NORMAL' });
    return c.waitFor('room.state', (s) => s.hostId === c.id);
  }
  async function join(c, code) {
    await ok(c, { t: 'room.join', code });
    return c.waitFor('room.state', (s) => s.code === code && s.seats.some((p) => p?.playerId === c.id));
  }

  test('same-room humans only, server author metadata, no secret or forged fields', async () => {
    const host = await player('Host');
    const r = await room(host);
    const guest = await player('손님'); await join(guest, r.code);
    const spectator = await player('Watcher');
    await ok(spectator, { t: 'room.spectate', code: r.code });
    await spectator.waitFor('room.state', (s) => s.code === r.code);
    const outsider = await player('Other'); await room(outsider);
    const roomless = await player('Roomless');
    await ok(host, { t: 'room.addBot' });
    await host.waitFor('room.state', (s) => s.seats.some((p) => p?.isBot));
    await ok(guest, { t: 'room.chat', text: '  안녕 👋  ', playerId: host.id, name: 'FORGED', token: host.token, code: 'FAKE', at: 0 });
    const received = await host.waitFor('room.chat');
    assert.deepEqual(received, { t: 'room.chat', code: r.code, seq: 1, playerId: guest.id, name: '손님', text: '안녕 👋', at: clock });
    assert.deepEqual(await guest.waitFor('room.chat'), received, 'sender gets its own server echo');
    assert.ok(!JSON.stringify(received).includes(host.token));
    await error(spectator, { t: 'room.chat', text: 'spectator' }, ERR.SPECTATOR);
    await error(roomless, { t: 'room.chat', text: 'roomless' }, ERR.NOT_IN_ROOM);
    await Promise.all([spectator.expectNone('room.chat'), outsider.expectNone('room.chat'), roomless.expectNone('room.chat')]);
  });

  test('normalization and 200-character bounds; invalid messages never broadcast or consume cooldown', async () => {
    const host = await player('Host'); const r = await room(host);
    assert.equal(normalizeChatText(' 안녕\n친구\u0000\u202E '), '안녕 친구');
    assert.equal(normalizeChatText('👩‍💻'.repeat(10)), '👩‍💻'.repeat(10), 'emoji joiners remain intact');
    assert.equal(normalizeChatText('\uD800'), null);
    for (const text of ['', '  \u0000\t ', '한'.repeat(201), '\uD800']) {
      assert.notEqual(validateC2S({ t: 'room.chat', text }), null);
      await error(host, { t: 'room.chat', text }, ERR.BAD_MSG);
    }
    assert.equal(srv.lobby.getRoom(r.code).chatSeq, 0);
    await ok(host, { t: 'room.chat', text: '😀'.repeat(200) });
    const first = await host.waitFor('room.chat'); assert.equal([...first.text].length, 200);
    clock += 999;
    await error(host, { t: 'room.chat', text: 'too soon' }, ERR.RATE);
    assert.equal(srv.lobby.getRoom(r.code).chatSeq, 1);
    clock++;
    await ok(host, { t: 'room.chat', text: '<img src=x onerror=alert(1)>' });
    const second = await host.waitFor('room.chat'); assert.equal(second.seq, 2);
    assert.equal(second.text, '<img src=x onerror=alert(1)>', 'plain text is never HTML interpreted by the server');
    await host.expectNone('room.chat');
    const solo = await player('Solo'); await room(solo, 'solo');
    await error(solo, { t: 'room.chat', text: 'solo' }, ERR.NOT_IN_ROOM);
  });

  test('cooldown follows the session across repeated hello, socket resume and room changes', async () => {
    const host = await player('Host'); const r = await room(host);
    await ok(host, { t: 'room.chat', text: 'first' }); await host.waitFor('room.chat');
    clock += 100;
    await host.hello('Renamed', host.token);
    await error(host, { t: 'room.chat', text: 'hello bypass' }, ERR.RATE);
    await host.terminate();
    const resumed = await player('Renamed', host.token);
    assert.equal(resumed.id, host.id);
    await resumed.waitFor('room.state', (s) => s.code === r.code);
    await error(resumed, { t: 'room.chat', text: 'reconnect bypass' }, ERR.RATE);
    const other = await player('Other'); const next = await room(other);
    await join(resumed, next.code);
    await error(resumed, { t: 'room.chat', text: 'room bypass' }, ERR.RATE);
    clock += 900;
    await ok(resumed, { t: 'room.chat', text: 'allowed' });
    const msg = await other.waitFor('room.chat'); assert.equal(msg.code, next.code); assert.equal(msg.seq, 1);
  });

  test('chat does not change match state, emote cooldown, or final replay and is never replayed', async () => {
    const host = await player('Host'); const r = await room(host);
    const guest = await player('Guest'); await join(guest, r.code);
    await ok(guest, { t: 'room.ready', ready: true });
    await ok(host, { t: 'room.start' });
    await host.waitFor('m.public');
    const live = srv.lobby.getRoom(r.code);
    const match = live.match;
    const beforeState = JSON.stringify([...match.players]);
    const beforePublic = JSON.stringify(match.publicView());
    const originalHandle = match.handle;
    let calls = 0;
    match.handle = function (...args) { calls++; return originalHandle.apply(this, args); };
    await ok(host, { t: 'room.chat', text: 'during match' }); await host.waitFor('room.chat');
    assert.equal(calls, 0);
    assert.equal(JSON.stringify([...match.players]), beforeState);
    assert.equal(JSON.stringify(match.publicView()), beforePublic);
    await ok(host, { t: 'g.emote', id: EMOTES[0] });
    await host.waitFor('m.emote');
    await ok(host, { t: 'g.infoReady' }); await ok(guest, { t: 'g.infoReady' });
    await host.waitFor('m.result');
    const replay = live.replay;
    const pending = [...replay.pending];
    const replayFrames = [...replay.frames];
    const pendingResult = srv.registry.byId(host.id).pendingResult;
    clock += 1000;
    await ok(host, { t: 'room.chat', text: 'after result' }); await host.waitFor('room.chat');
    assert.equal(live.replay, replay); assert.deepEqual([...replay.pending], pending); assert.deepEqual([...replay.frames], replayFrames);
    assert.equal(srv.registry.byId(host.id).pendingResult, pendingResult);
    assert.ok(!JSON.stringify(replayFrames).includes('room.chat'));
    await host.terminate();
    const resumed = await player('Host', host.token);
    await resumed.waitFor('m.result'); await resumed.expectNone('room.chat');
  });

  test('departed match seats cannot send or receive chat', async () => {
    const host = await player('Host'); const r = await room(host);
    const guest = await player('Guest'); await join(guest, r.code);
    await ok(guest, { t: 'room.ready', ready: true }); await ok(host, { t: 'room.start' });
    await host.waitFor('m.public'); await ok(guest, { t: 'g.leave' });
    assert.equal(srv.lobby.getRoom(r.code).seatOf(guest.id).left, true);
    await error(guest, { t: 'room.chat', text: 'left' }, ERR.NOT_IN_ROOM);
    await ok(host, { t: 'room.chat', text: 'remaining' }); await host.waitFor('room.chat');
    await guest.expectNone('room.chat');
  });
});
