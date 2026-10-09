// Team text chat is transient room traffic, separate from matches, emotes and replays.
export const CHAT_MAX_LENGTH = 200;
export const CHAT_COOLDOWN_MS = 1000;
export const CHAT_CLOSED_PREVIEW_MS = 5_000;
export const CHAT_HISTORY_LIMIT = 50;

/** Core alliances available as a session-local player marker. Colors are UI constants, never client input. */
export const CHAT_FACTIONS = Object.freeze([
  Object.freeze({ id: 'yanShip', color: '#ef6a52' }),
  Object.freeze({ id: 'sargonShip', color: '#d6a84f' }),
  Object.freeze({ id: 'victoriaShip', color: '#b99ae8' }),
  Object.freeze({ id: 'kjeragShip', color: '#8fdcf4' }),
  Object.freeze({ id: 'lateranoShip', color: '#f0ca69' }),
  Object.freeze({ id: 'egirShip', color: '#4f9ee8' }),
  Object.freeze({ id: 'siracusaShip', color: '#7bc7a4' }),
  Object.freeze({ id: 'kazimierzShip', color: '#f39a3d' }),
]);

/** @type {Map<string, { id: string, color: string }>} */
const CHAT_FACTION_BY_ID = new Map(CHAT_FACTIONS.map((faction) => [faction.id, faction]));

/** Return the canonical allow-listed faction id, or null for untrusted/unknown input. */
export function normalizeChatFaction(value) {
  return typeof value === 'string' && CHAT_FACTION_BY_ID.has(value) ? value : null;
}

/** Read-only display metadata for a validated faction id. */
export function chatFaction(value) {
  const id = normalizeChatFaction(value);
  return id ? CHAT_FACTION_BY_ID.get(id) || null : null;
}

/** One bounded line of plain text. Null means empty, too long or malformed Unicode. */
export function normalizeChatText(value) {
  if (typeof value !== 'string' || value.length > CHAT_MAX_LENGTH * 2) return null;
  // Keep Unicode joiners used by emoji; remove invisible direction/control characters.
  if (/[\uD800-\uDFFF]/u.test(value)) return null;
  const text = value.replace(/[\t\r\n\u2028\u2029]+/g, ' ')
    .replace(/[\u0000-\u001F\u007F-\u009F\u00AD\u200B\u200C\u200E\u200F\u202A-\u202E\u2060-\u206F\uFEFF]/g, '')
    .trim();
  return text && [...text].length <= CHAT_MAX_LENGTH ? text : null;
}

/** Only a co-op room's active human player may use team chat. Spectators are separate seats. */
export function canTeamChat(room, playerId) {
  return !!playerId && room?.mode === 'coop' && Array.isArray(room.seats)
    && room.seats.some((seat) => seat?.playerId === playerId && !seat.isBot && !seat.left);
}
