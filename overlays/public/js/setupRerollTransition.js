import { PHASE } from '../../shared/constants.js';

/** A completed upstream reroll increments setupRevision within the same room and INFO_CHECK phase. */
export function setupRerollTransitionKey(previous, next) {
  if (!previous || !next || !next.code || previous.code !== next.code
    || !previous.inMatch || !next.inMatch || previous.phase !== PHASE.INFO_CHECK || next.phase !== PHASE.INFO_CHECK
    || !Number.isSafeInteger(previous.revision) || !Number.isSafeInteger(next.revision)
    || previous.revision < 0 || next.revision <= previous.revision) return null;
  return `${next.code}:${next.revision}`;
}
