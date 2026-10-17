/* 顧客名印字＋個人名手書きの宛名書き：印刷専用。業務データへの登録・更新はしない。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  /* 見本 PDF（2238）: A4横・sourcePageRotate /Rotate 180。
   * 抽出・表示は正立として扱い、CSS では再回転しない（二重回転防止）。
   * 1ページ＝2×2の4枠。上段に顧客名、中央に手書き線、下段右に「様」のみ（個人名は空欄）。
   * レイアウトは見本に合わせて HTML で再現し、見本の印字文字は重ねない。 */
  const HANDWRITE_LABEL_CSS =
    '@page{size:A4 landscape;margin:0}' +
    '*{box-sizing:border-box}' +
    'html,body{margin:0;padding:0;background:white;color:black;' +
    'font-family:"Yu Gothic","YuGothic","Hiragino Kaku Gothic ProN",Meiryo,sans-serif}' +
    '.page{width:297mm;height:210mm;position:relative;display:grid;' +
    'grid-template-columns:148.5mm 148.5mm;grid-template-rows:105mm 105mm;' +
    'break-after:page;page-break-after:always;overflow:hidden}' +
    '.page:last-child{break-after:auto;page-break-after:auto}' +
    /* 見本どおり実線の切り取り線（縦・横） */
    '.page::before,.page::after{content:"";position:absolute;z-index:2;pointer-events:none;' +
    'border:0 solid #000}' +
    '.page::before{left:148.5mm;top:0;bottom:0;width:0;' +
    'border-left:0.35mm solid #000;transform:translateX(-0.175mm)}' +
    '.page::after{top:105mm;left:0;right:0;height:0;' +
    'border-top:0.35mm solid #000;transform:translateY(-0.175mm)}' +
    '.cell{width:148.5mm;height:105mm;padding:0;display:flex;flex-direction:column;' +
    'align-items:stretch;justify-content:flex-start;overflow:hidden;position:relative}' +
    /* 見本: 顧客名は上段中央付近（枠上端から約17–28mm）、手書き線は枠中央付近、様は右下 */
    '.customer-wrap{flex:0 0 58mm;display:flex;align-items:flex-start;justify-content:center;' +
    'padding:14mm 10mm 0;overflow:hidden}' +
    '.customer{text-align:center;font-weight:700;line-height:1.25;letter-spacing:.04em;' +
    'max-width:100%;overflow-wrap:anywhere;word-break:break-word;hyphens:none}' +
    '.guide-wrap{flex:0 0 auto;display:flex;justify-content:center;padding:0 14mm;margin-top:auto}' +
    '.guide{width:100%;max-width:118mm;border:0;border-bottom:0.35mm solid #000;height:0;margin:0}' +
    '.hand-area{flex:0 0 40mm;display:flex;align-items:flex-end;justify-content:flex-end;' +
    'padding:0 10mm 7mm;min-height:0}' +
    '.sama{font-size:34pt;font-weight:700;line-height:1;flex:0 0 auto}';

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeHandwriteCustomerName(value) {
    return String(value == null ? '' : value)
      .replace(/\u3000/g, ' ')
      .replace(/[ \t\r\n]+/g, ' ')
      .trim();
  }

  function normalizeHandwriteSheetCount(value) {
    const n = Number(value);
    if (!Number.isInteger(n) || n < 1) return null;
    if (n > 100) return null;
    return n;
  }

  function validateHandwriteLabelPrintInput(input) {
    const customerName = normalizeHandwriteCustomerName(input && input.customerName);
    if (!customerName) {
      return { ok: false, error: '顧客名を入力してください。' };
    }
    const sheets = normalizeHandwriteSheetCount(input && input.sheetCount);
    if (sheets == null) {
      return { ok: false, error: '印刷する用紙枚数は1以上の整数で入力してください。' };
    }
    return { ok: true, customerName, sheetCount: sheets };
  }

  /** 長い顧客名は枠内に収まるようサイズを下げる（見本の「日総 らくら砂川」相当を基準）。 */
  function handwriteCustomerFontSizePt(name) {
    const len = Array.from(String(name || '')).length;
    if (len <= 10) return 28;
    if (len <= 14) return 24;
    if (len <= 18) return 20;
    if (len <= 24) return 17;
    if (len <= 32) return 14;
    return 12;
  }

  function handwriteLabelCellHtml(customerName) {
    const name = normalizeHandwriteCustomerName(customerName);
    const size = handwriteCustomerFontSizePt(name);
    return (
      '<section class="cell">' +
      '<div class="customer-wrap">' +
      `<div class="customer" style="font-size:${size}pt">${escapeHtml(name)}</div>` +
      '</div>' +
      '<div class="guide-wrap"><div class="guide" aria-hidden="true"></div></div>' +
      '<div class="hand-area"><div class="sama">様</div></div>' +
      '</section>'
    );
  }

  function handwriteLabelPagesHtml(customerName, sheetCount) {
    const checked = validateHandwriteLabelPrintInput({ customerName, sheetCount });
    if (!checked.ok) throw new Error(checked.error);
    const cell = handwriteLabelCellHtml(checked.customerName);
    const page = `<div class="page">${cell}${cell}${cell}${cell}</div>`;
    return Array.from({ length: checked.sheetCount }, () => page).join('');
  }

  function handwriteLabelPreviewHtml(customerName, sheetCount, css) {
    const style = css || HANDWRITE_LABEL_CSS;
    const pages = handwriteLabelPagesHtml(customerName, sheetCount);
    return (
      '<!doctype html><html><head><meta charset="utf-8"><title>手書き宛名印刷</title><style>' +
      style +
      '@media screen{body{background:#eee;padding:12px}.page{background:white;margin:0 auto 12px;' +
      'box-shadow:0 1px 4px rgba(0,0,0,.12)}}' +
      '</style></head><body>' +
      pages +
      '</body></html>'
    );
  }

  function handwriteLabelIsPrintOnly() {
    return true;
  }

  function handwriteLabelMutatesOrders() {
    return false;
  }

  return {
    HANDWRITE_LABEL_CSS,
    normalizeHandwriteCustomerName,
    normalizeHandwriteSheetCount,
    validateHandwriteLabelPrintInput,
    handwriteCustomerFontSizePt,
    handwriteLabelCellHtml,
    handwriteLabelPagesHtml,
    handwriteLabelPreviewHtml,
    handwriteLabelIsPrintOnly,
    handwriteLabelMutatesOrders,
  };
});
