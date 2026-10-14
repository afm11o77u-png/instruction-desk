/* 宛名書き：Excel（TSV）一括貼り付けの純関数。ブラウザと Node の両方で使う。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const LABEL_PASTE_KEYS = ['customer', 'management', 'department', 'employee', 'request'];
  const LABEL_PASTE_MAX_ROWS = 40;
  const LABEL_REQUEST_OPTIONS = ['新規', 'サイズ交換', '消耗・破損交換', '追加・補充', '復職', '異動・移動', 'その他'];
  const LABEL_REQUEST_DEFAULT = '消耗・破損交換';

  function parseLabelClipboard(text) {
    const raw = String(text ?? '');
    if (!raw) return null;
    let normalized = raw.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    if (normalized.endsWith('\n')) normalized = normalized.slice(0, -1);
    if (!normalized.includes('\t') && !normalized.includes('\n')) return null;
    const lines = normalized.split('\n');
    const rows = lines.map(line => line.split('\t'));
    const width = Math.max(0, ...rows.map(row => row.length));
    if (!width) return null;
    const grid = rows.map(row => {
      const cells = row.slice();
      while (cells.length < width) cells.push('');
      return cells;
    });
    if (grid.length === 1 && grid[0].length === 1) return null;
    return grid;
  }

  function planLabelPaste({
    rows,
    originRow = 0,
    originCol = 0,
    cardCount = 4,
    maxRows = LABEL_PASTE_MAX_ROWS,
    keys = LABEL_PASTE_KEYS,
    requestOptions = LABEL_REQUEST_OPTIONS,
  }) {
    const grid = Array.isArray(rows) ? rows : [];
    if (!grid.length) return { ok: false, message: '貼り付ける表がありません。' };
    const colCount = Math.max(...grid.map(row => row.length));
    const rowCount = grid.length;
    const startRow = Math.max(0, originRow | 0);
    const startCol = Math.max(0, originCol | 0);
    const keyCount = keys.length;
    const remainCols = keyCount - startCol;
    const remainRows = maxRows - startRow;

    if (startCol >= keyCount) {
      return { ok: false, message: 'この欄からは貼り付けできません。' };
    }
    if (colCount > remainCols) {
      return {
        ok: false,
        message: `列数が多すぎます。この位置からは${remainCols}列まで貼り付けできます（顧客名／管理番号／部署名／氏名／受付内容）。切り捨てずに中止しました。`,
      };
    }
    if (rowCount > remainRows) {
      return {
        ok: false,
        message: `行数が多すぎます。この位置からは${remainRows}件まで貼り付けできます（最大${maxRows}件）。切り捨てずに中止しました。`,
      };
    }

    const updates = [];
    for (let r = 0; r < rowCount; r++) {
      const row = grid[r] || [];
      for (let c = 0; c < colCount; c++) {
        const key = keys[startCol + c];
        // Keep cell text as-is (leading zeros). Empty cells stay empty so columns do not shift.
        const value = row[c] == null ? '' : String(row[c]);
        if (key === 'request' && value !== '' && !requestOptions.includes(value)) {
          return {
            ok: false,
            message: `受付内容「${value}」は選べません。新規／サイズ交換／消耗・破損交換／追加・補充／復職／異動・移動／その他のいずれかを入れてください。`,
          };
        }
        updates.push({
          row: startRow + r,
          col: startCol + c,
          key,
          value,
        });
      }
    }

    const needCards = Math.max(cardCount | 0, startRow + rowCount);
    let pages = needCards;
    while (pages % 4) pages += 1;
    if (pages > maxRows) {
      return {
        ok: false,
        message: `行数が多すぎます。この位置からは${remainRows}件まで貼り付けできます（最大${maxRows}件）。切り捨てずに中止しました。`,
      };
    }

    return { ok: true, updates, needCards: pages, rowCount, colCount };
  }

  function labelPasteNeedsConfirm(updates, getCurrentValue) {
    if (!updates || !updates.length) return false;
    const read = typeof getCurrentValue === 'function' ? getCurrentValue : () => '';
    return updates.some(item => {
      if (item.key === 'request') return false;
      const current = String(read(item.row, item.key) ?? '').trim();
      return current !== '';
    });
  }

  /** 顧客名・管理番号・部署名・氏名のいずれかに入力がある行だけ対象。受付内容だけは対象外。 */
  function labelCardHasAddress(slot) {
    if (!slot || typeof slot !== 'object') return false;
    return ['customer', 'management', 'department', 'employee'].some(
      key => String(slot[key] ?? '').trim() !== ''
    );
  }

  /**
   * 受付内容の一括設定。完全空行は除外し、request だけ変える計画を返す。
   * @param {{ request: string, slots: Array<Record<string,string>> }} args
   */
  function planLabelRequestBatch({ request, slots }) {
    const value = String(request ?? '').trim();
    if (!value) {
      return {
        ok: false,
        reason: 'empty',
        message: '受付内容を選んでから「全行に適用」を押してください。',
      };
    }
    if (!LABEL_REQUEST_OPTIONS.includes(value)) {
      return {
        ok: false,
        reason: 'invalid',
        message: `受付内容「${value}」は選べません。新規／サイズ交換／消耗・破損交換／追加・補充／復職／異動・移動／その他のいずれかを選んでください。`,
      };
    }
    const list = Array.isArray(slots) ? slots : [];
    const targets = [];
    for (let i = 0; i < list.length; i++) {
      if (labelCardHasAddress(list[i])) targets.push(i);
    }
    if (!targets.length) {
      return {
        ok: false,
        reason: 'none',
        message: '適用できる宛名がありません。顧客名・管理番号・部署名・氏名のいずれかを入力してからお試しください。',
      };
    }
    return {
      ok: true,
      value,
      targets,
      confirmMessage: `${targets.length}件の受付内容を『${value}』に変更します。よろしいですか？`,
    };
  }

  return {
    LABEL_PASTE_KEYS,
    LABEL_PASTE_MAX_ROWS,
    LABEL_REQUEST_OPTIONS,
    LABEL_REQUEST_DEFAULT,
    parseLabelClipboard,
    planLabelPaste,
    labelPasteNeedsConfirm,
    labelCardHasAddress,
    planLabelRequestBatch,
  };
});
