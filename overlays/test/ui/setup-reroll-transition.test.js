import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PHASE } from '../../shared/constants.js';
import { setupRerollTransitionKey } from '../../public/js/setupRerollTransition.js';

test('animate only completed native rerolls; ignore initial connections, cancelled votes and room/match changes', () => {
  const before = { code: 'ABCD', inMatch: true, phase: PHASE.INFO_CHECK, revision: 0 };
  const after = { ...before, revision: 1 };
  assert.equal(setupRerollTransitionKey(before, after), 'ABCD:1');
  for (const [a, b] of [[null, after], [before, before], [after, before],
    [before, { ...after, code: 'EFGH' }], [before, { ...after, inMatch: false }],
    [{ ...before, phase: PHASE.RESULT }, after], [before, { ...after, phase: PHASE.BAND_DRAFT }],
    [{ ...before, revision: undefined }, after], [before, { ...after, revision: NaN }]]) {
    assert.equal(setupRerollTransitionKey(a, b), null);
  }
});
