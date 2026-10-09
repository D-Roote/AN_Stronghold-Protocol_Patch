import { drawDisabledBonds } from './pool.js';

/** Preserve normal draws; a restart chooses another valid ban set whenever the mode permits it. */
export function drawRestartBans(gd, rng, previous = null) {
  let bans = drawDisabledBonds(gd, rng);
  if (!Array.isArray(previous)) return bans;
  const key = previous.slice().sort().join('|');
  for (let i = 0; i < 32 && bans.drawn.join('|') === key; i++) bans = drawDisabledBonds(gd, rng);
  if (bans.drawn.join('|') !== key) return bans;
  // A repeated draw must not defeat a unanimous vote; swap one bond within the same category.
  const replacement = gd.bondIds.find((id) => {
    const bond = gd.bond(id);
    return bond && Number(bond.weight) > 0 && !gd.modeInactiveBonds.has(id) && !bans.drawn.includes(id)
      && bans.drawn.some((old) => !!gd.bond(old)?.isCore === !!bond.isCore);
  });
  if (!replacement) return bans; // A mode with no bans, or all eligible bonds disabled, has no different valid set.
  const index = bans.drawn.findIndex((id) => !!gd.bond(id)?.isCore === !!gd.bond(replacement).isCore);
  const drawn = bans.drawn.slice();
  drawn[index] = replacement;
  drawn.sort();
  const off = new Set([...drawn, ...bans.staticOff]);
  const banned = gd.visibleChess.filter((id) => {
    const bonds = gd.chess(id)?.bonds;
    return Array.isArray(bonds) && bonds.length > 0 && bonds.every((bond) => off.has(bond));
  });
  return { drawn, staticOff: bans.staticOff, banned };
}
