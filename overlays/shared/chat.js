// Team text chat is transient room traffic, separate from matches, emotes and replays.
export const CHAT_MAX_LENGTH = 200;
export const CHAT_COOLDOWN_MS = 1000;
export const CHAT_TTL_MS = 10_000;
export const CHAT_HISTORY_LIMIT = 50;

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
