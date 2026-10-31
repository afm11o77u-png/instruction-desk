/* 定型書式印刷：印刷専用の書式レジストリ。業務データへの登録・更新はしない。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  /* A4横 297×210mm・余白0。
   * cells: 2×2の4分割（1枠 148.5×105mm）。枚数はA4用紙枚数（全ページ4枠印字）。
   * page-image: 元PDFの1ページ（既に4面）をそのまま1枚の書式として全面配置。分割・複製しない。 */
  const FIXED_LABEL_CSS =
    '@page{size:A4 landscape;margin:0}' +
    '*{box-sizing:border-box}' +
    'html,body{margin:0;padding:0;background:white;color:black;' +
    'font-family:"Yu Gothic","YuGothic",Meiryo,"MS Gothic","Hiragino Kaku Gothic ProN","Noto Sans CJK JP","Noto Sans JP","IPAexGothic","IPAGothic",sans-serif}' +
    '.page{width:297mm;height:210mm;position:relative;display:grid;' +
    'grid-template-columns:148.5mm 148.5mm;grid-template-rows:105mm 105mm;' +
    'break-after:page;page-break-after:always;overflow:hidden}' +
    '.page:last-child{break-after:auto;page-break-after:auto}' +
    /* 中央の破線切り取り線（縦・横）— cells 書式のみ */
    '.page::before,.page::after{content:"";position:absolute;z-index:2;pointer-events:none;' +
    'border:0 solid #888}' +
    '.page::before{left:148.5mm;top:0;bottom:0;width:0;' +
    'border-left:1px dashed #888;transform:translateX(-0.5px)}' +
    '.page::after{top:105mm;left:0;right:0;height:0;' +
    'border-top:1px dashed #888;transform:translateY(-0.5px)}' +
    '.cell{width:148.5mm;height:105mm;padding:7mm 8mm 6mm;display:flex;flex-direction:column;' +
    'align-items:stretch;justify-content:flex-start;overflow:hidden;position:relative}' +
    '.title{text-align:center;font-weight:700;font-size:26pt;line-height:1.2;' +
    'letter-spacing:.04em;margin:0 0 5mm}' +
    '.checks{display:flex;flex-direction:column;gap:3.2mm;margin:0 0 5mm;flex:0 0 auto}' +
    '.checkrow{display:grid;align-items:center;column-gap:2mm;row-gap:0;width:100%}' +
    '.checkrow.four{grid-template-columns:repeat(4,minmax(0,1fr))}' +
    /* 上段4列に揃えたうえで「工場長」だけ右へ足す */
    '.checkrow.five{grid-template-columns:repeat(4,minmax(0,1fr)) auto;column-gap:1.5mm}' +
    '.check{display:flex;align-items:center;gap:.35mm;font-size:13.5pt;line-height:1.25;' +
    'font-weight:500;white-space:nowrap;min-width:0}' +
    '.check .box{flex:0 0 auto;font-size:14pt;line-height:1;font-weight:400}' +
    /* NO.＿＿＿ と「様」は縦に分離。様は右下、左に手書き氏名スペース */
    '.idblock{display:flex;flex-direction:column;flex:1 1 auto;min-height:0;' +
    'margin:2mm 0 0}' +
    '.no{display:flex;align-items:flex-end;gap:2mm;flex:0 0 auto;min-width:0;' +
    'width:42%;max-width:48%;margin-top:1mm}' +
    '.no .label{font-size:16pt;font-weight:700;letter-spacing:.06em;line-height:1;flex:0 0 auto}' +
    '.no .line{flex:1 1 auto;border-bottom:1.6px solid #000;height:0;min-width:28mm;margin-bottom:1px}' +
    '.name-row{display:flex;justify-content:flex-end;align-items:flex-end;' +
    'flex:1 1 auto;min-height:18mm;margin-top:2mm}' +
    '.sama{font-size:42pt;font-weight:700;line-height:1;flex:0 0 auto;padding:0 1mm 0 0}' +
    '.footer{margin-top:auto;text-align:center;font-size:18pt;font-weight:700;' +
    'letter-spacing:.08em;line-height:1.2;padding-top:2mm;white-space:pre}' +
    /* page-image: 元スキャン1ページをA4横全面に。追加枠・内側余白・CSS回転なし */
    '.page.page-image{display:block;padding:0;margin:0;' +
    'grid-template-columns:none;grid-template-rows:none}' +
    '.page.page-image::before,.page.page-image::after{content:none;display:none}' +
    '.page.page-image .sheetimg{display:block;width:297mm;height:210mm;margin:0;padding:0;' +
    'border:0;object-fit:contain;object-position:center;background:white}' +
    /* ひまわり：枠付きカード。店舗チェック2列→横線→氏名欄（様）→横線→【備考】枠→エンパイアー */
    '.cell.hm{padding:5mm 5.5mm}' +
    '.hm .frame{flex:1 1 auto;display:flex;flex-direction:column;border:1.2px solid #000;padding:5mm 6mm 2mm;min-height:0}' +
    '.hm .stores{display:grid;grid-template-columns:1fr 1fr;column-gap:4mm;flex:0 0 auto}' +
    '.hm .col{display:flex;flex-direction:column;gap:1.6mm}' +
    '.hm .store{display:flex;align-items:center;gap:2.5mm;font-size:11pt;line-height:1.25;font-weight:400;white-space:nowrap}' +
    '.hm .box{flex:0 0 auto;display:inline-block;width:3.2mm;height:3.2mm;border:1px solid #000;position:relative}' +
    '.hm .rule{border-top:1px solid #000;margin:3mm 0 0;flex:0 0 auto}' +
    '.hm .namearea{flex:1 1 auto;display:flex;justify-content:flex-end;align-items:flex-end;min-height:12mm;padding:0 1mm 1.5mm 0}' +
    '.hm .sama{font-size:26pt;font-weight:700;line-height:1}' +
    '.hm .memo{display:flex;align-items:flex-start;gap:1.5mm;margin-top:2.5mm;flex:0 0 auto}' +
    '.hm .memo .label{font-size:8.5pt;font-weight:700;padding-top:2mm;white-space:nowrap}' +
    '.hm .memo .memobox{flex:1 1 auto;height:12mm;border:1px solid #000}' +
    '.hm .maker{text-align:right;font-size:6pt;line-height:1;margin-top:.8mm;flex:0 0 auto}';

  function escText(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* ひまわり書式：stores を左列 leftCount 件・残りを右列。チェック欄は空の□（手書きでチェック）。 */
  function himawariCellHtml(format) {
    const item = (name) => `<div class="store"><span class="box"></span>${escText(name)}</div>`;
    const left = format.stores.slice(0, format.leftCount);
    const right = format.stores.slice(format.leftCount);
    return (
      '<section class="cell hm"><div class="frame">' +
      `<div class="stores"><div class="col">${left.map(item).join('')}</div><div class="col">${right.map(item).join('')}</div></div>` +
      '<div class="rule"></div>' +
      '<div class="namearea"><div class="sama">様</div></div>' +
      '<div class="rule" style="margin-top:0"></div>' +
      '<div class="memo"><span class="label">【備考】</span><div class="memobox"></div></div>' +
      '<div class="maker">エンパイアー</div>' +
      '</div></section>'
    );
  }

  function himawari(id, name, leftCount, stores) {
    return {
      id, name, kind: 'cells', leftCount,
      stores: stores.map((s) => 'ひまわり' + s),
      buildCellHtml() { return himawariCellHtml(this); },
    };
  }

  const FIXED_LABEL_FORMATS = [
    {
      id: 'daruma-zenibako',
      name: 'だるま食品銭函工場',
      kind: 'cells',
      buildCellHtml: function buildDarumaZenibakoCell() {
        const row = (cls, items) =>
          `<div class="checkrow ${cls}">${items
            .map((t) => `<div class="check"><span class="box">□</span>${t}</div>`)
            .join('')}</div>`;
        return (
          '<section class="cell">' +
          '<div class="title">だるま食品銭函工場</div>' +
          '<div class="checks">' +
          row('four', ['製造１課', '製造２課', '製造３課', '製造４課']) +
          row('four', ['製造部', '設備保全課', '商品部', '新規事業部']) +
          row('five', ['衛生', '清掃', '営業部', '貸出用', '工場長']) +
          '</div>' +
          '<div class="idblock">' +
          '<div class="no"><span class="label">NO.</span><span class="line"></span></div>' +
          '<div class="name-row"><div class="sama">様</div></div>' +
          '</div>' +
          '<div class="footer">新規　交換　その他</div>' +
          '</section>'
        );
      },
    },
    {
      id: 'nittetuft-2239',
      name: '日鉄FT',
      kind: 'page-image',
      imageUrl: '/fixed-label/2239-nittetuft.jpg',
      imageFile: 'fixed-label/2239-nittetuft.jpg',
    },
    {
      id: 'rose-delica-2-2240',
      name: 'ローズデリカ２課',
      kind: 'page-image',
      imageUrl: '/fixed-label/2240-rose-delica-2.jpg',
      imageFile: 'fixed-label/2240-rose-delica-2.jpg',
    },
    {
      id: 'rose-delica-1-2241',
      name: 'ローズデリカ１課',
      kind: 'page-image',
      imageUrl: '/fixed-label/2241-rose-delica-1.jpg',
      imageFile: 'fixed-label/2241-rose-delica-1.jpg',
    },
    {
      /* 元PDFは /Rotate 180。同梱 JPEG は抽出時に回転を反映済み（CSS では再回転しない）。 */
      id: 'ishiya-2242',
      name: '石屋商事',
      kind: 'page-image',
      imageUrl: '/fixed-label/2242-ishiya.jpg',
      imageFile: 'fixed-label/2242-ishiya.jpg',
      sourcePageRotate: 180,
    },
    himawari('himawari-1', 'ひまわり1', 5, ['タワー店', 'ファイターズ通り店', '豊平店', '月寒店', '清田店', '新琴似店', '篠路店', '真駒内店', 'タウン店']),
    himawari('himawari-2', 'ひまわり2', 4, ['五所川原店', '八戸店', '能代店', '大曲店', '岩沼店', '北上店', '多賀城店']),
    himawari('himawari-3', 'ひまわり3', 5, ['弟子屈店', '知床店', '美幌店', '北見店', '紋別店', '中標津店', '根室店', '釧路店', '帯広店', '音更店']),
    himawari('himawari-4', 'ひまわり4', 4, ['苫小牧店', '函館店', '日乃出店', '伊達店', '滝川店', '留萌店', '士別店', '室蘭店']),
  ];

  function listFixedLabelFormats() {
    return FIXED_LABEL_FORMATS.map((f) => {
      return { id: f.id, name: f.name, kind: f.kind || 'cells' };
    });
  }

  function getFixedLabelFormat(id) {
    return FIXED_LABEL_FORMATS.find((f) => f.id === id) || null;
  }

  function normalizeFixedLabelSheetCount(value) {
    const n = Number(value);
    if (!Number.isInteger(n) || n < 1) return null;
    if (n > 100) return null;
    return n;
  }

  function validateFixedLabelPrintInput(input) {
    const formatId = String((input && input.formatId) || '').trim();
    const format = getFixedLabelFormat(formatId);
    if (!format) return { ok: false, error: '書式を選んでください。' };
    const sheets = normalizeFixedLabelSheetCount(input && input.sheetCount);
    if (sheets == null) {
      return { ok: false, error: '印刷する用紙枚数は1以上の整数で入力してください。' };
    }
    return { ok: true, formatId: format.id, sheetCount: sheets, format };
  }

  function escapeHtmlAttr(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function resolveFixedLabelImageSrc(format, assetBase) {
    if (!format || format.kind !== 'page-image') return '';
    if (assetBase) {
      const base = String(assetBase).replace(/\/?$/, '/');
      const file = format.imageFile || String(format.imageUrl || '').replace(/^\//, '');
      return base + file.replace(/^\.\//, '');
    }
    return format.imageUrl || '';
  }

  function fixedLabelPageHtml(format, assetBase) {
    if (format.kind === 'page-image') {
      const src = resolveFixedLabelImageSrc(format, assetBase);
      const alt = escapeHtmlAttr(format.name);
      return (
        `<div class="page page-image">` +
        `<img class="sheetimg" src="${escapeHtmlAttr(src)}" alt="${alt}">` +
        `</div>`
      );
    }
    const cell = format.buildCellHtml();
    return `<div class="page">${cell}${cell}${cell}${cell}</div>`;
  }

  function fixedLabelPagesHtml(formatId, sheetCount, assetBase) {
    const checked = validateFixedLabelPrintInput({ formatId, sheetCount });
    if (!checked.ok) throw new Error(checked.error);
    const page = fixedLabelPageHtml(checked.format, assetBase);
    return Array.from({ length: checked.sheetCount }, () => page).join('');
  }

  function fixedLabelPreviewHtml(formatId, sheetCount, css, assetBase) {
    const style = css || FIXED_LABEL_CSS;
    const pages = fixedLabelPagesHtml(formatId, sheetCount, assetBase);
    return (
      '<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>定型書式印刷</title><style>' +
      style +
      '@media screen{body{background:#eee;padding:12px}.page{background:white;margin:0 auto 12px;' +
      'box-shadow:0 1px 4px rgba(0,0,0,.12)}}' +
      '</style></head><body>' +
      pages +
      '</body></html>'
    );
  }

  function fixedLabelIsPrintOnly() {
    return true;
  }

  function fixedLabelMutatesOrders() {
    return false;
  }

  return {
    FIXED_LABEL_CSS,
    FIXED_LABEL_FORMATS,
    listFixedLabelFormats,
    getFixedLabelFormat,
    normalizeFixedLabelSheetCount,
    validateFixedLabelPrintInput,
    resolveFixedLabelImageSrc,
    fixedLabelPagesHtml,
    fixedLabelPreviewHtml,
    fixedLabelIsPrintOnly,
    fixedLabelMutatesOrders,
  };
});
