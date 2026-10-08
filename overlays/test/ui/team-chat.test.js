import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { CHAT_CLOSED_PREVIEW_MS } from '../../shared/chat.js';
import {
  TeamChatFeed, FactionTag, chatSendState, chatErrorLabel, makeClosedChatPreview, closedChatPreviewMessages, factionLabel,
} from '../../public/js/ui/teamChat.js';

describe('team chat UI: retained history and frozen closed preview', () => {
  test('a close-time snapshot ignores later messages and disappears exactly ten seconds later', () => {
    const messages = [{ id: 'first' }, { id: 'second' }];
    const factions = { peer: 'kjeragShip' };
    const preview = makeClosedChatPreview(messages, 1000, factions, 123);
    messages.push({ id: 'later' });
    factions.peer = 'egirShip';
    assert.deepEqual(preview.factions, { peer: 'kjeragShip' }, 'closed nickname markers also stay frozen');
    assert.equal(preview.scrollTop, 123, 'the close-time visible scroll position is preserved');
    assert.deepEqual(closedChatPreviewMessages(preview, 1000 + CHAT_CLOSED_PREVIEW_MS - 1).map((message) => message.id), ['first', 'second']);
    assert.deepEqual(closedChatPreviewMessages(preview, 1000 + CHAT_CLOSED_PREVIEW_MS), []);
    assert.deepEqual(closedChatPreviewMessages(null, 1000), []);
  });

  test('names, faction events and hostile message markup stay literal Preact text children', () => {
    const name = '<img src=x onerror=alert(1)>';
    const text = '<script>window.compromised=true</script><b>hello & "world"</b>';
    const tree = TeamChatFeed({ playerId: 'me', factions: { me: 'kazimierzShip' }, messages: [
      { id: 'a', kind: 'chat', playerId: 'me', name, text },
      { id: 'b', kind: 'faction', playerId: 'peer', name, faction: 'kjeragShip' },
    ] });
    const nodes = [];
    const strings = [];
    const walk = (v) => {
      if (Array.isArray(v)) { v.forEach(walk); return; }
      if (typeof v === 'string') { strings.push(v); return; }
      if (!v || typeof v !== 'object') return;
      nodes.push(v);
      walk(v.props?.children);
    };
    walk(tree);
    assert.ok(strings.includes(name));
    assert.ok(strings.includes(text));
    assert.ok(nodes.every((v) => !v.props?.dangerouslySetInnerHTML));
    assert.ok(nodes.every((v) => !['img', 'script', 'b'].includes(v.type)));
    assert.equal(tree.props.role, 'log');
    assert.equal(tree.props['aria-live'], 'polite');
    assert.ok(nodes.some((v) => v.props?.class === 'team-chat__message is-self'));
    assert.ok(nodes.some((v) => v.type === FactionTag));
    assert.ok(nodes.some((v) => v.props?.class === 'team-chat__message is-system'));
    assert.ok(factionLabel('egirShip'));
    assert.equal(factionLabel('forged'), '');
  });
});

describe('team chat UI: send availability', () => {
  test('offline, sending, cooldown, empty and malformed drafts cannot submit', () => {
    const draft = { text: 'hello', online: true };
    assert.deepEqual(chatSendState({ ...draft, online: false }, 1000), { disabled: true, reason: 'OFFLINE', text: 'hello' });
    assert.equal(chatSendState({ ...draft, sending: true }, 1000).reason, 'SENDING');
    assert.equal(chatSendState({ ...draft, lastSentAt: 1000 }, 1999).reason, 'RATE');
    assert.equal(chatSendState({ ...draft, lastSentAt: 1000 }, 2000).disabled, false);
    assert.equal(chatSendState({ ...draft, lastSentAt: 1000 }, 999).disabled, false, 'a clock adjustment does not lock input');
    assert.equal(chatSendState({ online: true, text: '   ' }, 1000).disabled, true);
    assert.equal(chatSendState({ ...draft, text: '\ud800' }, 1000).reason, 'BAD_MSG');
    assert.equal(chatSendState({ ...draft, text: 'x'.repeat(201) }, 1000).reason, 'BAD_MSG');
  });

  test('counts Unicode characters and passes normalized plain text', () => {
    const result = chatSendState({ online: true, text: '🙂'.repeat(200) });
    assert.equal(result.disabled, false);
    assert.equal([...result.text].length, 200);
    assert.equal(chatSendState({ online: true, text: '🙂'.repeat(201) }).reason, 'BAD_MSG');
    assert.equal(chatSendState({ online: true, text: '  hi\nthere\u202e  ' }).text, 'hi there');
    assert.equal(chatSendState({ online: true, text: '<b>hello</b>' }).text, '<b>hello</b>');
  });

  test('known and unknown transport failures get readable labels without exposing server text', () => {
    assert.equal(chatErrorLabel('OFFLINE'), chatErrorLabel('CLOSED'));
    assert.equal(chatErrorLabel('NOT_IN_ROOM'), chatErrorLabel('SPECTATOR'));
    assert.ok(chatErrorLabel('RATE'));
    assert.ok(chatErrorLabel('BAD_MSG'));
    assert.equal(chatErrorLabel(null), '');
    assert.equal(chatErrorLabel('<script>server internals</script>'), chatErrorLabel('FAILED'));
  });
});
