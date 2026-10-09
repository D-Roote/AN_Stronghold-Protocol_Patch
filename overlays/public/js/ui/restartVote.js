import { useEffect, useLayoutEffect, useRef, useState } from '../../vendor/hooks.module.js';
import { t } from '../../../shared/i18n.js';
import { PHASE } from '../../../shared/constants.js';
import { canTeamChat } from '../chat.js';
import { net } from '../net.js';
import { useStore } from '../store.js';
import { html, Button } from './components.js';
import { toast } from './toasts.js';

export function BriefingRestartVote() {
  const room = useStore((s) => s.room);
  const myId = useStore((s) => s.me.playerId);
  const online = useStore((s) => s.connection.status === 'online');
  const [busy, setBusy] = useState(false);
  if (!canTeamChat(room, myId)) return null;
  const vote = room.restartVote;
  const selected = !!vote?.voters?.includes(myId);
  const submit = async () => {
    if (busy || !online || vote?.pending) return;
    setBusy(true);
    try { await net.request('room.restartVote', { agree: !selected, matchNo: room.matchNo }, { queue: false }); }
    catch { toast(t('重启投票发送失败，请重试'), 'warn'); }
    finally { setBusy(false); }
  };
  return html`<${Button} variant="secondary" size="lg" class="brief-restart" icon="refresh" active=${selected}
    loading=${busy} disabled=${!online || !!vote?.pending} onClick=${submit}>
    ${vote?.voters?.length ? t('重启 {n} / {total}', { n: vote.voters.length, total: vote.total }) : t('重启投票')}<//>`;
}

/** Lives above the router so replacing a match cannot remove its blackout mid-transition. */
export function RestartTransitionHost() {
  const room = useStore((s) => s.room);
  const phase = useStore((s) => s.match.public?.phase);
  const seen = useRef(null);
  const [transition, setTransition] = useState(null);
  const key = room?.restartVote?.pending ? `${room.code}:${room.matchNo}` : null;
  useLayoutEffect(() => {
    if (!key) { seen.current = null; return; }
    if (seen.current === key) return;
    seen.current = key;
    setTransition({ key, code: room.code, matchNo: room.matchNo, at: Date.now(), revealing: false });
  }, [key]);
  useEffect(() => {
    if (!transition || transition.revealing) return undefined;
    const complete = room?.code !== transition.code || (!key && (room?.matchNo === transition.matchNo
      || phase === PHASE.INFO_CHECK || !room?.inMatch));
    if (!complete) return undefined;
    const timer = setTimeout(() => setTransition((current) => current?.key === transition.key
      ? { ...current, revealing: true } : current), Math.max(0, 200 - (Date.now() - transition.at)));
    return () => clearTimeout(timer);
  }, [room?.code, room?.matchNo, room?.inMatch, key, phase, transition]);
  useEffect(() => {
    if (!transition) return undefined;
    const timer = setTimeout(() => setTransition(null), transition.revealing ? 190 : 5000);
    return () => clearTimeout(timer);
  }, [transition]);
  return transition ? html`<div key=${transition.key} class=${`restart-transition${transition.revealing ? ' is-revealing' : ''}`}
    aria-hidden="true"></div>` : null;
}
