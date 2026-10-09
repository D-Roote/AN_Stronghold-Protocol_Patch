import { useEffect, useLayoutEffect, useRef, useState } from '../../vendor/hooks.module.js';
import { setupRerollTransitionKey } from '../setupRerollTransition.js';
import { useStore } from '../store.js';
import { html } from './components.js';

/** Above the router: show a brief blackout after the native server has committed the replacement setup. */
export function SetupRerollTransitionHost() {
  const code = useStore((s) => s.room?.code);
  const inMatch = useStore((s) => !!s.room?.inMatch);
  const phase = useStore((s) => s.match.public?.phase);
  const revision = useStore((s) => s.match.public?.setupRevision);
  const seen = useRef(null);
  const [transition, setTransition] = useState(null);
  useLayoutEffect(() => {
    const next = { code, inMatch, phase, revision };
    const key = setupRerollTransitionKey(seen.current, next);
    seen.current = next;
    if (key) setTransition({ key, revealing: false });
    else if (!inMatch) setTransition(null);
  }, [code, inMatch, phase, revision]);
  useEffect(() => {
    if (!transition) return undefined;
    const timer = setTimeout(() => setTransition((current) => current?.key === transition.key
      ? current.revealing ? null : { ...current, revealing: true } : current), transition.revealing ? 190 : 200);
    return () => clearTimeout(timer);
  }, [transition]);
  return transition ? html`<div key=${transition.key} class=${`restart-transition${transition.revealing ? ' is-revealing' : ''}`}
    aria-hidden="true"></div>` : null;
}
