import { N_ } from '../../shared/i18n.js';

/** Compact presentation of the upstream vote; all intents still use its revision/vote IDs. */
export function setupRerollControl(pub, { playerId, hostId, online }) {
  const players = pub.players || [];
  const me = players.find((p) => p.playerId === playerId && p.status !== 'left');
  if (!me) return null;
  const vote = pub.rerollVote;
  const host = playerId === hostId;
  const voter = !!vote?.voters.includes(playerId);
  const selected = !!vote?.agreed.includes(playerId);
  const allConnected = players.every((p) => p.isBot || p.status === 'left' || p.connected);
  return {
    selected,
    disabled: !online || (!vote ? !host || !allConnected : !voter),
    action: !vote ? 'start' : !selected ? 'agree' : host ? 'cancel' : 'reject',
    canReject: !!vote && voter && !selected,
    revision: pub.setupRevision ?? 0,
    voteId: vote?.id,
    label: vote?.agreed.length ? N_('重启 {n} / {total}') : N_('重启投票'),
    params: vote ? { n: vote.agreed.length, total: vote.voters.length } : {},
    title: !vote
      ? !host ? N_('仅房主可以发起重刷投票')
        : allConnected ? N_('全体真人玩家一致同意后刷新，随机结果可能重复。') : N_('请等待所有玩家连接后再发起投票')
      : selected ? host ? N_('取消投票') : N_('拒绝重刷') : N_('同意重刷'),
  };
}
