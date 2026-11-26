/* WEB依頼：商品ごとのWEB表示名・写真（WEB依頼の設定 ②）と、依頼画面の「＋ 制服を追加」（手入力）。
   request-web.html は変更せず、この JS で画面を組み立てる。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WebRequestExtras = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const PHOTO_MAX = 3 * 1024 * 1024;
  const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
  const MANUAL_LIMITS = { name: 100, code: 60, color: 40, size: 40, processing: 200, note: 300 };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  /* 顧客向けの商品名：契約の表示名 → 商品のWEB表示名 → 在庫の商品名 */
  function resolveName(contractName, productName, stockName) {
    for (const v of [contractName, productName, stockName]) if (typeof v === 'string' && v.trim()) return v.trim();
    return '';
  }
  function photoUrl(productId, display) {
    return display && display.photo_version ? `/api/web-request/product-photo/${encodeURIComponent(productId)}?v=${encodeURIComponent(display.photo_version)}` : '';
  }
  /* 写真は商品ごとの共通写真（WEB表示名・写真）だけを使う。契約側の古い写真（u.photo）は使わない（データは残す） */
  function resolvePhoto(contractPhoto, productId, display) {
    return photoUrl(productId, display);
  }
  function checkPhotoFile(file) {
    if (!file) return '写真を選んでください。';
    if (!PHOTO_TYPES.includes(file.type)) return '写真はJPEG・PNG・WebPの画像を選んでください。';
    if (file.size > PHOTO_MAX) return '写真は3MB以下の画像を選んでください。';
    return '';
  }

  /* 「＋ 制服を追加」の手入力行の検査。商品名と枚数（1〜99）が必須。 */
  function validateManualItem(f) {
    const v = {};
    for (const k of Object.keys(MANUAL_LIMITS)) v[k] = String((f && f[k]) ?? '').replace(/\s+/g, ' ').trim();
    if (!v.name) return { ok: false, error: '商品名を入力してください。' };
    for (const [k, max] of Object.entries(MANUAL_LIMITS)) if (v[k].length > max) return { ok: false, error: `入力が長すぎます（${max}文字まで）。` };
    const raw = String((f && f.qty) ?? '').trim();
    const qty = Number(raw);
    if (!raw || !Number.isInteger(qty) || qty < 1 || qty > 99) return { ok: false, error: '枚数は1〜99で入力してください。' };
    return {
      ok: true,
      item: { id: 'manual-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7), manual: true, name: v.name, code: v.code, color: v.color, size: v.size, qty, processing: v.processing, note: v.note, hem_processing: false, processing_days: 0 },
    };
  }

  function extraBits(x) {
    const bits = [];
    if (x.manual && x.code) bits.push('品番 ' + esc(x.code));
    if (x.processing) bits.push('加工の希望：' + esc(x.processing));
    if (x.note) bits.push('備考：' + esc(x.note));
    return bits;
  }
  /* 依頼内容1行。顧客向けは表示名。社内向け（staff）は元の商品名＋表示名、手入力は「手入力」バッジ。 */
  function itemLineHtml(x, opts) {
    const staff = !!(opts && opts.staff);
    let name = esc(x.name);
    if (staff && x.stock_name && x.stock_name !== x.name) name = `${esc(x.stock_name)}（WEB表示名：${esc(x.name)}）`;
    const badge = staff && x.manual ? '<span class="badge manual-badge">手入力</span> ' : '';
    const extra = extraBits(x);
    return `${badge}${name} · ${esc(x.color || '')} / ${esc(x.size || '')} / ${Number(x.qty) || 0}点${x.default_qty ? `（契約 ${esc(x.default_qty)}枚）` : ''} ${esc(x.embroidery || '')}${extra.length ? '<br><small>' + extra.join('　') + '</small>' : ''}`;
  }
  function cartLineHtml(x, i) {
    const extra = extraBits(x);
    return `<div class="cart-line"><button data-remove="${i}" aria-label="${esc(x.name)}を削除">削除</button><strong>${esc(x.name)}</strong><br>${esc(x.color || '')} / ${esc(x.size || '')} / ${Number(x.qty) || 0}点${extra.length ? '<br><small>' + extra.join('　') + '</small>' : ''}</div>`;
  }

  /* 選んだ会社の契約で使われている商品ID */
  function usedProductIds(data, cid) {
    const handles = new Map((data.handles || []).filter((h) => h.customer_id === cid).map((h) => [h.id, h.product_id]));
    const ids = new Set();
    const seen = new Set();
    const walk = (v) => {
      if (!v || typeof v !== 'object' || seen.has(v)) return;
      seen.add(v);
      if (Array.isArray(v)) { v.forEach(walk); return; }
      if (Array.isArray(v.uniforms)) {
        for (const u of v.uniforms) {
          if (!u || typeof u !== 'object') continue;
          const pid = handles.get(u.customer_product_id) ?? u.product_id;
          if (Number.isInteger(pid)) ids.add(pid);
        }
      }
      for (const k of Object.keys(v)) if (k !== 'uniforms') walk(v[k]);
    };
    for (const p of [...(data.profiles || []), ...(data.scopeProfiles || [])]) if (p && p.customer_id === cid) walk(p);
    return ids;
  }

  /* ---------- 画面（ブラウザーのみ） ---------- */
  const $ = (s) => (typeof document !== 'undefined' ? document.querySelector(s) : null);
  const desk = () => (typeof window !== 'undefined' ? window.WebRequestDesk : null);
  async function postDisplay(body) {
    const r = await fetch('/api/web-request/product-display', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Desk-Request': '1' }, body: JSON.stringify(body) });
    const result = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(result.error || '保存できませんでした。');
    return result;
  }
  function readAsDataUrl(file) {
    return new Promise((resolve, reject) => { const fr = new FileReader(); fr.onload = () => resolve(fr.result); fr.onerror = () => reject(Error('写真を読み込めませんでした。')); fr.readAsDataURL(file); });
  }
  function ensureProductSection() {
    let sec = $('#web-product-display');
    if (sec) return sec;
    const anchor = $('#web-page-customer');
    if (!anchor) return null;
    sec = document.createElement('section');
    sec.id = 'web-product-display';
    sec.className = 'request-record web-product-display';
    sec.innerHTML = '<h3>② 制服の表示名・写真</h3><p class="small">お客さんの依頼画面に出す商品名と写真を、商品ごとに設定します。同じ商品を使うすべての会社で共通です。表示名が空欄のときは在庫の商品名、写真がないときは今のイラストを表示します。社内の画面・指示書・在庫は元の商品名のままです。</p><input id="web-product-display-search" type="search" placeholder="商品名・品番で絞り込み" aria-label="商品を絞り込み"><div id="web-product-display-list"></div>';
    anchor.after(sec);
    sec.querySelector('#web-product-display-search').oninput = renderProductSection;
    sec.addEventListener('click', onProductClick);
    sec.addEventListener('change', onProductChange);
    return sec;
  }
  function renderProductSection() {
    const d = desk();
    if (!d) return;
    const sec = ensureProductSection();
    if (!sec) return;
    const data = d.getData() || {};
    const cid = Number(($('#org-root-customer') || {}).value);
    const show = d.getMode() === 'web-settings' && !!cid && !!data.isAdmin && !data.previewAccountId && !data.previewPageId;
    sec.hidden = !show;
    if (!show) return;
    if (sec.querySelector('.web-product-row.busy')) return;
    const ids = usedProductIds(data, cid);
    const term = ($('#web-product-display-search').value || '').trim().toLowerCase();
    const display = data.productDisplay || {};
    const rows = (data.products || []).filter((p) => ids.has(p.id)).filter((p) => !term || (p.name + ' ' + p.code + ' ' + ((display[p.id] || {}).display_name || '')).toLowerCase().includes(term));
    const list = sec.querySelector('#web-product-display-list');
    if (!ids.size) { list.innerHTML = '<p class="small">この会社の契約に制服がまだありません。制服は「顧客・契約管理」で契約に登録します。</p>'; return; }
    list.innerHTML = rows.map((p) => {
      const pd = display[p.id] || {};
      const url = photoUrl(p.id, pd);
      return `<div class="web-product-row" data-product="${p.id}"><div class="web-product-thumb">${url ? `<img src="${esc(url)}" alt="${esc(p.name)}の写真">` : '<span>写真なし</span>'}</div><div class="web-product-main"><div class="web-product-original"><small>在庫の商品名</small><strong>${esc(p.name)}</strong><span>${esc(p.code || '品番なし')}${p.color ? ' · ' + esc(p.color) : ''}</span></div><label>WEB表示名<input data-display-name="${p.id}" maxlength="120" value="${esc(pd.display_name || '')}" placeholder="${esc(p.name)}"></label><div class="web-product-actions"><button type="button" class="primary" data-save-display="${p.id}">表示名を保存</button><label class="web-product-upload">写真を選ぶ<input type="file" accept="image/jpeg,image/png,image/webp" data-photo="${p.id}" hidden></label>${url ? `<button type="button" data-remove-photo="${p.id}">写真を外す</button>` : ''}<button type="button" data-sample-photo="${p.id}">見本画像から選ぶ</button></div></div></div>`;
    }).join('') || '<p class="small">該当する商品がありません。</p>';
  }
  async function saveDisplay(pid, body, row) {
    const d = desk();
    if (row) row.classList.add('busy');
    try {
      const result = await postDisplay({ product_id: pid, ...body });
      const data = d.getData();
      data.productDisplay = data.productDisplay || {};
      data.productDisplay[pid] = result.display;
      if (row) row.classList.remove('busy');
      renderProductSection();
      d.notify(body.remove_photo ? '写真を外しました。' : body.photo ? '写真を保存しました。' : '表示名を保存しました。');
    } catch (e) {
      if (row) row.classList.remove('busy');
      d.notify(e.message);
    }
  }
  function onProductClick(e) {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.saveDisplay) {
      const pid = Number(b.dataset.saveDisplay);
      const input = document.querySelector(`[data-display-name="${pid}"]`);
      saveDisplay(pid, { display_name: input ? input.value : '' }, b.closest('.web-product-row'));
    } else if (b.dataset.samplePhoto) {
      openSamplePicker(Number(b.dataset.samplePhoto), b.closest('.web-product-row'));
    } else if (b.dataset.removePhoto) {
      if (!confirm('この商品の写真を外しますか？')) return;
      saveDisplay(Number(b.dataset.removePhoto), { remove_photo: true }, b.closest('.web-product-row'));
    }
  }
  /* 見本画像（uniform-samples/）から選んで、写真のアップロードと同じ保存をする */
  let sampleManifest = null;
  async function loadSamples() {
    if (sampleManifest) return sampleManifest;
    const r = await fetch('/uniform-samples/manifest.json', { credentials: 'same-origin' });
    if (!r.ok) throw Error('見本画像の一覧を読み込めませんでした。');
    sampleManifest = await r.json();
    return sampleManifest;
  }
  async function openSamplePicker(pid, row) {
    let man;
    try { man = await loadSamples(); } catch (e) { desk().notify(e.message); return; }
    let dlg = $('#uniform-sample-dialog');
    if (!dlg) { dlg = document.createElement('dialog'); dlg.id = 'uniform-sample-dialog'; dlg.className = 'uniform-sample-dialog'; document.body.append(dlg); }
    let cat = '';
    const draw = () => {
      const term = (dlg.querySelector('#uniform-sample-search') || {}).value || '';
      const t = term.trim().toLowerCase();
      const items = man.items.filter((x) => (!cat || x.category === cat) && (!t || (x.category + ' ' + x.name + ' ' + x.color).toLowerCase().includes(t)));
      dlg.querySelector('#uniform-sample-tabs').innerHTML = ['', ...man.categories].map((c) => `<button type="button" data-sample-cat="${esc(c)}" class="${c === cat ? 'primary' : ''}">${esc(c || 'すべて')}</button>`).join('');
      dlg.querySelector('#uniform-sample-grid').innerHTML = items.map((x) => `<button type="button" class="uniform-sample-item" data-sample-file="${esc(x.file)}" title="${esc(x.category + ' ' + x.name + '（' + x.color + '）')}"><img src="/uniform-samples/${esc(x.file)}" alt="" loading="lazy"><span>${esc(x.name)}</span><small>${esc(x.category)}・${esc(x.color)}</small></button>`).join('') || '<p class="small">該当する見本画像がありません。</p>';
    };
    dlg.innerHTML = '<h3>見本画像から選ぶ</h3><p class="small">選んだ画像がこの商品の写真になります（今の写真は置き換わります）。</p><div id="uniform-sample-tabs" class="actions"></div><input id="uniform-sample-search" type="search" placeholder="名前・色で絞り込み（例：白衣、緑）" aria-label="見本画像を絞り込み"><div id="uniform-sample-grid" class="uniform-sample-grid"></div><div class="dialog-actions"><button type="button" id="uniform-sample-close">閉じる</button></div>';
    dlg.querySelector('#uniform-sample-search').oninput = draw;
    dlg.querySelector('#uniform-sample-close').onclick = () => dlg.close();
    dlg.onclick = async (e) => {
      const c = e.target.closest('[data-sample-cat]');
      if (c) { cat = c.dataset.sampleCat; draw(); return; }
      const it = e.target.closest('[data-sample-file]');
      if (!it || it.disabled) return;
      it.disabled = true;
      try {
        const r = await fetch('/uniform-samples/' + it.dataset.sampleFile, { credentials: 'same-origin' });
        if (!r.ok) throw Error('見本画像を読み込めませんでした。');
        const url = await readAsDataUrl(await r.blob());
        dlg.close();
        await saveDisplay(pid, { photo: url }, row);
      } catch (ex) { it.disabled = false; desk().notify(ex.message); }
    };
    draw();
    dlg.showModal();
  }
  async function onProductChange(e) {
    const input = e.target;
    if (!input.dataset || !input.dataset.photo) return;
    const file = input.files && input.files[0];
    const err = checkPhotoFile(file);
    if (err) { input.value = ''; desk().notify(err); return; }
    try {
      const url = await readAsDataUrl(file);
      await saveDisplay(Number(input.dataset.photo), { photo: url }, input.closest('.web-product-row'));
    } catch (ex) { desk().notify(ex.message); }
    input.value = '';
  }

  /* 依頼画面：「＋ 制服を追加」 */
  function ensureManualEntry() {
    if ($('#manual-entry')) return;
    const items = $('#products');
    if (!items) return;
    const box = document.createElement('div');
    box.id = 'manual-entry';
    box.className = 'manual-entry';
    box.innerHTML = '<p class="small manual-entry-hint" id="manual-entry-hint">一覧にない制服は、こちらに入力して追加できます。</p><button type="button" id="manual-entry-open" class="manual-entry-open" aria-describedby="manual-entry-hint">＋ 制服を追加</button><div id="manual-entry-form" class="manual-entry-form" hidden><div class="manual-entry-row"><label class="me-name">商品名（必須）<input data-manual="name" maxlength="100"></label><label class="me-code">品番<input data-manual="code" maxlength="60"></label><label class="me-color">色<input data-manual="color" maxlength="40"></label><label class="me-size">サイズ<input data-manual="size" maxlength="40"></label><label class="me-qty">枚数（必須）<input data-manual="qty" type="number" min="1" max="99" value="1"></label><label class="me-processing">加工の希望<input data-manual="processing" maxlength="200" placeholder="ネーム・刺繍など"></label><label class="me-note">備考<input data-manual="note" maxlength="300"></label></div><p id="manual-entry-error" role="alert"></p><div class="manual-entry-actions"><button type="button" class="primary" id="manual-entry-add">ご依頼内容に追加</button><button type="button" id="manual-entry-close">閉じる</button></div></div>';
    items.after(box);
    const form = box.querySelector('#manual-entry-form');
    box.querySelector('#manual-entry-open').onclick = () => { form.hidden = !form.hidden; if (!form.hidden) form.querySelector('[data-manual="name"]').focus(); };
    box.querySelector('#manual-entry-close').onclick = () => { form.hidden = true; box.querySelector('#manual-entry-error').textContent = ''; };
    box.querySelector('#manual-entry-add').onclick = () => {
      const d = desk();
      const data = d.getData() || {};
      if (data.previewCanRequest === false) return d.notify('この利用者は閲覧のみです。');
      const f = {};
      form.querySelectorAll('[data-manual]').forEach((el) => { f[el.dataset.manual] = el.value; });
      const r = validateManualItem(f);
      box.querySelector('#manual-entry-error').textContent = r.ok ? '' : r.error;
      if (!r.ok) return;
      d.addCartItem(r.item);
      form.querySelectorAll('[data-manual]').forEach((el) => { el.value = el.dataset.manual === 'qty' ? '1' : ''; });
      d.notify('ご依頼内容に追加しました。');
    };
  }
  function syncManualEntry() {
    const d = desk();
    const box = $('#manual-entry');
    if (!d || !box) return;
    box.hidden = d.getMode() !== 'order';
  }

  if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    const start = () => { ensureManualEntry(); syncManualEntry(); renderProductSection(); };
    window.addEventListener('web-request-data', start);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else setTimeout(start);
  }

  return { PHOTO_MAX, PHOTO_TYPES, resolveName, photoUrl, resolvePhoto, checkPhotoFile, validateManualItem, itemLineHtml, cartLineHtml, usedProductIds, renderProductSection, syncManualEntry };
});
