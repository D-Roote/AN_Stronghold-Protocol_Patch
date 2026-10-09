import { describe, test, before, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { startServer } from '../server/index.js';
import { TestClient } from './helpers/wsClient.js';
import { ERR } from '../shared/constants.js';
import { validateC2S } from '../shared/protocol.js';
import { drawRestartBans } from '../server/match/restartBans.js';

describe('briefing restart: authoritative, reversible, unanimous human votes', () => {
  let srv;
  const clients = new Set();
  const errors = [];
  before(async () => {
    srv = await startServer({ port: 0, host: '127.0.0.1', seedFn: () => 69,
      log: { info() {}, warn() {}, debug() {}, error: (...args) => errors.push(args.map(String).join(' ')) } });
  });
  afterEach(async () => { await Promise.all([...clients].map((c) => c.terminate())); clients.clear(); });
  after(async () => { await srv.close(); assert.deepEqual(errors, []); });
  async function player(name) {
    const c = await TestClient.connect(`ws://127.0.0.1:${srv.port}/ws`);
    clients.add(c);
    const welcome = await c.hello(name);
    c.id = welcome.playerId;
    return c;
  }
  const ok = async (c, msg) => { const reply = await c.request(msg); assert.equal(reply.t, 'ok', JSON.stringify(reply)); };
  const vote = (c, matchNo, agree = true) => ok(c, { t: 'room.restartVote', matchNo, agree });
  async function game(size = 2, bot = false) {
    const host = await player('호스트');
    await ok(host, { t: 'room.create', mode: 'coop', difficulty: 'NORMAL' });
    const initial = await host.waitFor('room.state', (s) => s.hostId === host.id);
    const people = [host];
    for (let i = 1; i < size; i++) {
      const peer = await player(`팀원${i}`);
      await ok(peer, { t: 'room.join', code: initial.code });
      await peer.waitFor('room.state', (s) => s.code === initial.code);
      await ok(peer, { t: 'room.ready', ready: true });
      people.push(peer);
    }
    if (bot) await ok(host, { t: 'room.addBot' });
    await ok(host, { t: 'room.start' });
    const publicState = await host.waitFor('m.public', (s) => s.phase === 'INFO_CHECK');
    const room = srv.lobby.getRoom(initial.code);
    return { host, people, room, publicState, matchNo: room.matchCount };
  }

  test('vote schema rejects missing numbers, non-booleans and malformed match identity', () => {
    assert.equal(validateC2S({ t: 'room.restartVote', agree: true, matchNo: 1 }), null);
    for (const msg of [{ agree: true }, { agree: 1, matchNo: 1 }, { agree: true, matchNo: 0 }, { agree: true, matchNo: 1.5 }]) {
      assert.notEqual(validateC2S({ t: 'room.restartVote', ...msg }), null);
    }
  });

  test('four people see one request, votes are idempotent, cancellable and scoped to this match', async () => {
    const { host, people, room, matchNo } = await game(4);
    await vote(host, matchNo);
    const event = await people[1].waitFor('room.restart');
    assert.equal(event.playerId, host.id);
    assert.equal(event.name, '호스트');
    assert.equal(event.seq, 1);
    await vote(host, matchNo);
    await vote(people[1], matchNo);
    let state = await people[2].waitFor('room.state', (s) => s.restartVote?.voters.length === 2);
    assert.equal(state.restartVote.total, 4);
    await vote(people[1], matchNo, false);
    state = await host.waitFor('room.state', (s) => s.restartVote?.requested && s.restartVote.voters.length === 1);
    assert.deepEqual(state.restartVote.voters, [host.id]);
    assert.equal(room.matchCount, matchNo);
    assert.equal(room.chatSeq, 1, 'cancellation and repeat voting do not announce a second request');
    const stale = await people[2].request({ t: 'room.restartVote', agree: true, matchNo: matchNo + 1 });
    assert.equal(stale.code, ERR.WRONG_PHASE);
  });

  test('all humans agree: old match stops, bans change, AI is excluded, room and chat sequence remain', async () => {
    const { host, people, room, matchNo, publicState } = await game(2, true);
    const old = room.match;
    await vote(host, matchNo);
    const state = await host.waitFor('room.state', (s) => s.restartVote?.voters.length === 1);
    assert.equal(state.restartVote.total, 2);
    await vote(people[1], matchNo);
    await host.waitFor('room.state', (s) => s.restartVote?.pending);
    assert.equal(old.restartPending, true);
    assert.equal(old.deadline, 0);
    const ready = await host.request({ t: 'g.infoReady' });
    assert.equal(ready.code, ERR.WRONG_PHASE, 'an old ready cannot skip the restart transition');
    await host.waitFor('room.state', (s) => s.matchNo === matchNo + 1);
    const resetVote = await host.waitFor('room.state', (s) => s.matchNo === matchNo + 1 && s.restartVote?.requested === false);
    assert.equal(resetVote.restartVote.total, 2);
    assert.deepEqual(resetVote.restartVote.voters, []);
    assert.ok(resetVote.seats.filter((s) => s && !s.isBot).every((s) => !s.ready));
    const fresh = await host.waitFor('m.public', (s) => s.phase === 'INFO_CHECK' && JSON.stringify(s.drawnDisabledBonds) !== JSON.stringify(publicState.drawnDisabledBonds));
    assert.ok(old.disposed);
    assert.notEqual(room.match, old);
    assert.notDeepEqual(fresh.drawnDisabledBonds, publicState.drawnDisabledBonds);
    assert.ok(fresh.players.filter((p) => !p.isBot).every((p) => !p.ready));
    assert.equal(room.chatSeq, 1);
    await ok(host, { t: 'room.chat', text: '다시 시작해도 같은 채팅' });
    const chat = await people[1].waitFor('room.chat');
    assert.equal(chat.seq, 2);
    const stale = await host.request({ t: 'room.restartVote', agree: true, matchNo });
    assert.equal(stale.code, ERR.WRONG_PHASE);
  });

  test('a withdrawal during the blackout cancels the deferred replacement and restores its deadline', async () => {
    const { host, people, room, matchNo } = await game();
    const old = room.match;
    const deadline = old.deadline;
    await vote(host, matchNo);
    await vote(people[1], matchNo);
    await host.waitFor('room.state', (s) => s.restartVote?.pending);
    await vote(host, matchNo, false);
    await host.waitFor('room.state', (s) => s.restartVote?.pending === false);
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(room.match, old);
    assert.equal(room.matchCount, matchNo);
    assert.equal(old.restartPending, false);
    assert.ok(Math.abs(old.deadline - deadline) < 10);
  });

  test('disconnect removes that vote and cannot manufacture unanimous approval', async () => {
    const { host, people, room, matchNo } = await game();
    const old = room.match;
    await vote(host, matchNo);
    await vote(people[1], matchNo);
    await host.waitFor('room.state', (s) => s.restartVote?.pending);
    await people[1].terminate();
    const state = await host.waitFor('room.state', (s) => s.restartVote?.pending === false && s.seats.some((p) => p?.playerId === people[1].id && !p.connected));
    assert.equal(state.restartVote.total, 2, 'a temporarily disconnected teammate still belongs to the simulation');
    assert.deepEqual(state.restartVote.voters, [host.id]);
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(room.match, old);
    assert.equal(old.restartPending, false);
  });

  test('spectators, roomless users and post-briefing votes cannot restart a match', async () => {
    const { host, people, room, matchNo } = await game();
    const outsider = await player('다른 사용자');
    let reply = await outsider.request({ t: 'room.restartVote', agree: true, matchNo });
    assert.equal(reply.code, ERR.NOT_IN_ROOM);
    await ok(outsider, { t: 'room.spectate', code: room.code });
    reply = await outsider.request({ t: 'room.restartVote', agree: true, matchNo });
    assert.equal(reply.code, ERR.SPECTATOR);
    await vote(host, matchNo);
    await ok(host, { t: 'g.infoReady' });
    await ok(people[1], { t: 'g.infoReady' });
    await host.waitFor('m.public', (s) => s.phase === 'BAND_DRAFT');
    reply = await host.request({ t: 'room.restartVote', agree: true, matchNo });
    assert.equal(reply.code, ERR.WRONG_PHASE);
    assert.equal(room.toState().restartVote, undefined);
  });
});

test('repeated RNG draws still replace one eligible bond and recompute banned operators', () => {
  const bonds = { a: { weight: 1, isCore: true }, b: { weight: 1, isCore: true }, off: { weight: 0, isCore: true } };
  const gd = { bans: () => ({ core: 1, addon: 0 }), difficulty: 'NORMAL',
    modeInactiveBonds: new Set(), bondIds: Object.keys(bonds), bond: (id) => bonds[id],
    visibleChess: ['old', 'new'], chess: (id) => ({ bonds: [id === 'old' ? 'a' : 'b'] }) };
  const result = drawRestartBans(gd, { shuffle: () => {} }, ['a']);
  assert.deepEqual(result.drawn, ['b']);
  assert.deepEqual(result.banned, ['new']);
  assert.deepEqual(drawRestartBans({ ...gd, bans: () => ({ core: 0, addon: 0 }) }, { shuffle: () => {} }, []).drawn, []);
});
