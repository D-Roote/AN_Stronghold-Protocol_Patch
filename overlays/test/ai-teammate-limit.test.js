import { describe, test, before, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { startServer } from '../server/index.js';
import { lobbyOptionsFrom } from '../server/http/config.js';
import { Lobby } from '../server/lobby.js';
import { TestClient } from './helpers/wsClient.js';
import { ERR } from '../shared/constants.js';

describe('deployment AI teammate limit', () => {
  let srv;
  const clients = new Set();
  before(async () => { srv = await startServer({ port: 0, host: '127.0.0.1', quiet: true, maxBots: 1 }); });
  afterEach(async () => { await Promise.all([...clients].map((client) => client.terminate())); clients.clear(); });
  after(async () => { await srv.close(); });

  async function host(name) {
    const client = await TestClient.connect(`ws://127.0.0.1:${srv.port}/ws`);
    clients.add(client);
    const welcome = await client.hello(name);
    const created = await client.request({ t: 'room.create', mode: 'coop', difficulty: 'NORMAL' });
    assert.equal(created.t, 'ok');
    const room = await client.waitFor('room.state', (state) => state.hostId === welcome.playerId);
    assert.equal(room.maxBots, 1, 'the client receives the authoritative room limit');
    return { client, code: room.code };
  }

  test('server refuses a second AI even while seats are free; removing one permits replacement', async () => {
    const { client, code } = await host('Host');
    assert.equal((await client.request({ t: 'room.addBot' })).t, 'ok');
    const state = await client.waitFor('room.state', (room) => room.seats.some((seat) => seat?.isBot));
    assert.equal(state.seats.filter(Boolean).length, 2);
    const refused = await client.request({ t: 'room.addBot' });
    assert.equal(refused.t, 'error');
    assert.equal(refused.code, ERR.ROOM_FULL);
    assert.equal(srv.lobby.getRoom(code).seats.filter((seat) => seat?.isBot).length, 1);
    assert.equal((await client.request({ t: 'room.removeBot', seat: state.seats.findIndex((seat) => seat?.isBot) })).t, 'ok');
    assert.equal((await client.request({ t: 'room.addBot' })).t, 'ok');
    assert.equal(srv.lobby.getRoom(code).seats.filter((seat) => seat?.isBot).length, 1);
  });

  test('the limit is independently enforced in every room', async () => {
    const first = await host('First');
    const second = await host('Second');
    for (const { client } of [first, second]) {
      assert.equal((await client.request({ t: 'room.addBot' })).t, 'ok');
      assert.equal((await client.request({ t: 'room.addBot' })).code, ERR.ROOM_FULL);
    }
    assert.notEqual(first.code, second.code);
  });
});

test('SP_MAX_BOTS parsing rejects invalid configuration and explicit options take precedence', () => {
  const previous = process.env.SP_MAX_BOTS;
  try {
    process.env.SP_MAX_BOTS = ' 1 ';
    assert.deepEqual(lobbyOptionsFrom({}), { maxBots: 1 });
    assert.deepEqual(lobbyOptionsFrom({ maxBots: 0 }), { maxBots: 0 });
    for (const value of ['', '-1', '1.5', 'invalid']) {
      process.env.SP_MAX_BOTS = value;
      assert.throws(() => lobbyOptionsFrom({}), RangeError);
      assert.deepEqual(lobbyOptionsFrom({ maxBots: 1 }), { maxBots: 1 });
    }
    process.env.SP_MAX_BOTS = '4';
    assert.throws(() => new Lobby({ registry: {}, options: lobbyOptionsFrom({}) }), RangeError);
  } finally {
    if (previous === undefined) delete process.env.SP_MAX_BOTS;
    else process.env.SP_MAX_BOTS = previous;
  }
});
