import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setupRerollControl } from '../../public/js/setupRerollControl.js';

const host = { playerId: 'host', connected: true }, peer = { playerId: 'peer', connected: true };
const bot = { playerId: 'bot', isBot: true };
const opening = { setupRevision: 3, players: [host, peer, bot] };
const voting = { ...opening, rerollVote: { id: 7, voters: ['host', 'peer'], agreed: ['host'] } };
const control = (pub, playerId = 'host', online = true) => setupRerollControl(pub, { playerId, hostId: 'host', online });

test('only a connected host can request a native reroll, including after an earlier reroll', () => {
  assert.equal(control(opening).label, '重启投票');
  assert.equal(control(opening).disabled, false);
  assert.equal(control(opening).action, 'start');
  assert.equal(control(opening).revision, 3);
  assert.equal(control(opening, 'peer').disabled, true);
  assert.equal(control(opening, 'spectator'), null);
  assert.equal(control({ ...opening, players: [host, { ...peer, connected: false }] }).disabled, true);
  assert.equal(control({ ...opening, players: [host, { ...peer, connected: false, status: 'left' }, bot] }).disabled, false);
  assert.equal(control(opening, 'host', false).disabled, true);
});

test('compact count and click intents keep native agree, reject and host cancel distinct', () => {
  const proposer = control(voting), waiting = control(voting, 'peer');
  assert.equal(proposer.selected, true);
  assert.equal(proposer.action, 'cancel');
  assert.equal(proposer.canReject, false);
  assert.equal(waiting.action, 'agree');
  assert.equal(waiting.canReject, true, 'an undecided teammate can reject without agreeing first');
  assert.equal(waiting.voteId, 7);
  assert.deepEqual(waiting.params, { n: 1, total: 2 }, 'AI is excluded by the native voter list');
  assert.equal(waiting.label, '重启 {n} / {total}');
  const agreed = control({ ...voting, rerollVote: { ...voting.rerollVote, agreed: ['host', 'peer'] } }, 'peer');
  assert.equal(agreed.selected, true);
  assert.equal(agreed.action, 'reject');
  assert.equal(agreed.canReject, false);
  assert.equal(control(voting, 'peer', false).disabled, true);
  assert.equal(control({ ...voting, rerollVote: { ...voting.rerollVote, agreed: [] } }).label, '重启投票');
  assert.equal(control({ ...voting, rerollVote: null }).label, '重启投票');
});
