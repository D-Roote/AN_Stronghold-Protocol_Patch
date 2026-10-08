// Optional asset download consent and controls, independent of the current game screen.
import { useEffect, useState } from '../../vendor/hooks.module.js';
import { html, Modal, Button } from './components.js';
import { t, N_ } from '../../../shared/i18n.js';
import { assetPrefetch } from '../assetPrefetch.js';

const STATUS = {
  idle: N_('尚未开始'), preparing: N_('正在准备'), downloading: N_('正在下载'),
  paused: N_('已暂停'), complete: N_('下载完成'), error: N_('部分资源未完成'), unsupported: N_('此浏览器无法保存资源缓存'),
};

export function formatAssetBytes(bytes) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(1)} KB`;
  return `${Math.max(0, Math.round(bytes))} B`;
}

export function AssetPrefetchHost() {
  const [state, setState] = useState(() => assetPrefetch.getState());
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const unsubscribe = assetPrefetch.subscribe(setState);
    const showControls = () => setOpen(true);
    globalThis.addEventListener('sp:asset-prefetch-open', showControls);
    assetPrefetch.init().then(() => setReady(true));
    return () => { unsubscribe(); globalThis.removeEventListener('sp:asset-prefetch-open', showControls); };
  }, []);
  const firstVisit = ready && state.decision === null;
  const active = state.status === 'downloading' || state.status === 'preparing';
  const accepted = state.decision === 'accepted';
  const close = () => { if (firstVisit) assetPrefetch.decline(); setOpen(false); };
  const actions = firstVisit || !accepted
    ? html`<${Button} variant="secondary" onClick=${close}>${t('按需加载')}<//>
        <${Button} variant="primary" disabled=${!state.supported || active} onClick=${() => { assetPrefetch.accept(); setOpen(false); }}>${t('同意并下载')}<//>`
    : html`<${Button} variant="ghost" onClick=${close}>${t('关闭')}<//>
        <${Button} variant="secondary" disabled=${!state.supported || active} onClick=${() => assetPrefetch.clearCache()}>${t('清除资源缓存')}<//>
        ${active
          ? html`<${Button} variant="primary" disabled=${state.clearing} onClick=${() => assetPrefetch.pause()}>${t('暂停下载')}<//>`
          : html`<${Button} variant="primary" disabled=${!state.supported} onClick=${() => assetPrefetch.resume()}>${t(state.status === 'error' ? '重试下载' : '继续下载')}<//>`}`;
  return html`
    <${Modal} open=${firstVisit || open} onClose=${close} title=${t('提前下载游戏资源')} micro="ASSET CACHE"
      width="6.8rem" class="asset-prefetch-modal" actions=${actions}>
      <div class="asset-prefetch" data-testid="asset-prefetch-dialog">
        <p>${t('是否提前下载游戏资源？')}</p>
        <p>${t('预计约 {size}，包含图片、模型、语音和本地字体。', { size: formatAssetBytes(state.estimatedBytes) })}</p>
        <p class="asset-prefetch__hint">${t('同意后会在后台下载，游戏仍可照常使用。选择按需加载也能正常游戏。')}</p>
        <p class="asset-prefetch__hint">${t('资源保存在此浏览器中，可暂停、继续或清除。浏览器清理缓存后需要重新下载。')}</p>
        ${!state.supported ? html`<p class="asset-prefetch__notice" role="status">${t('此浏览器无法保存资源缓存，游戏将继续按需加载。')}</p>` : null}
        ${accepted ? html`<section class="asset-prefetch__progress" aria-label=${t('下载进度')}>
          <div class="asset-prefetch__status" role="status">${t(STATUS[state.status] || STATUS.idle)}</div>
          <progress max=${state.total || 1} value=${state.completed} aria-label=${t('下载进度')}></progress>
          <div class="asset-prefetch__counts">${t('已保存 {done} / {total} 个文件', { done: state.completed, total: state.total })}</div>
          <div class="asset-prefetch__hint">${t('本次下载 {size} · 已有缓存 {count} 个', { size: formatAssetBytes(state.downloadedBytes), count: state.cached })}</div>
          ${state.failed ? html`<p class="asset-prefetch__notice">${t('{count} 个文件下载失败，可稍后重试。', { count: state.failed })}</p>` : null}
          ${state.errorCode === 'quota' ? html`<p class="asset-prefetch__notice" role="alert">${t('浏览器存储空间不足，下载已停止。可清除资源缓存后重试，或继续按需加载。')}</p>` : null}
          ${state.status === 'error' && state.errorCode !== 'quota' ? html`<p class="asset-prefetch__notice">${t('下载暂时未完成。已保存的资源会保留，游戏仍可使用。')}</p>` : null}
        </section>` : null}
      </div>
    <//>`;
}
