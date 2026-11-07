(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let csvData = '', workbookData = '', result = null, revision = 0;

  function status(message) { $('totattstatus').textContent = message; }
  function clearResult() {
    revision += 1;
    result = null;
    $('totattpreview').hidden = true;
    $('totattcopy').disabled = true;
    $('totattreview').checked = false;
    $('totattmanual').hidden = true;
    $('totattmanual').value = '';
  }
  function readFile(file, limit) {
    if (!file || file.size > limit) return Promise.reject(Error('ファイルのサイズを確認してください。'));
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(Error('ファイルを読み込めませんでした。'));
      reader.readAsDataURL(file);
    });
  }
  async function post(action, payload) {
    const response = await fetch('/api/kintai/tot-attendance/' + action, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Desk-Request': '1' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'データを確認できませんでした。');
    return data;
  }
  function parseTime(value) {
    if (value === '') return null;
    const match = String(value).trim().match(/^(\d{1,2}):(\d{2})$/);
    return match && +match[1] <= 47 && +match[2] < 60 ? +match[1] * 60 + +match[2] : NaN;
  }
  function displayTime(value) {
    return Math.floor(value / 60) + ':' + String(value % 60).padStart(2, '0');
  }
  function checkedValues(row) {
    const values = ['start', 'rest', 'end'].map(key => parseTime(row[key]));
    if (values.some(Number.isNaN)) return { error: '時刻は9:00、休憩は1:10の形式で入力してください。' };
    let [start, rest, end] = values;
    if ((start === null) !== (end === null)) return { error: '不足している出勤／退勤を確認・修正してください。' };
    if (start === null) return rest === null ? { values } : { error: '出退勤のない日の休憩を消すか、時刻を入力してください。' };
    if (rest === null) return { error: '休憩時間を入力してください。休憩なしは0:00です。' };
    if (end < start) end += 1440;
    if (rest > 1440 || end <= start || rest >= end - start) return { error: '始業・終業・休憩の長さを確認してください。' };
    return { values: [start, rest, end], overnight: end >= 1440 };
  }
  function refreshValidation() {
    if (!result) return;
    let errors = 0;
    result.rows.forEach((row, index) => {
      const checked = checkedValues(row);
      const cell = $('totattrow' + index);
      cell.textContent = [checked.error, row.note, row.edited ? '手修正済み' : '', row.filled ? '固定休憩を補完' : '', checked.overnight ? '退勤は翌日として貼付' : ''].filter(Boolean).join('／');
      cell.style.color = checked.error ? '#a32525' : '';
      if (checked.error) errors += 1;
    });
    $('totattcopy').disabled = errors > 0 || !$('totattreview').checked;
    $('totattvalidation').textContent = errors ? errors + '日分の時間を確認・修正してください。' : '時間の形式を確認しました。空欄の日と補完した休憩を確認して、下のチェックを入れてください。';
  }
  function render(data) {
    result = data;
    const body = $('totattrows');
    body.replaceChildren();
    $('totattdestination').textContent = data.name + 'さん／' + data.start + '～' + data.end +
      ' → Excel「' + data.layout.sheet + '」の ' + data.layout.range + '（開始セル ' + data.layout.cell + '）';
    if (data.layout.dateYearMismatch) $('totattdestination').textContent += ' ※出勤簿の日付セルに年のずれがあるため、対象シートと月日で照合しています。';
    data.rows.forEach((row, index) => {
      const tr = document.createElement('tr');
      const day = document.createElement('td');
      day.textContent = row.date;
      tr.appendChild(day);
      ['start', 'rest', 'end'].forEach(key => {
        const td = document.createElement('td');
        const input = document.createElement('input');
        input.type = 'text'; input.value = row[key]; input.size = 6; input.maxLength = 5;
        input.setAttribute('aria-label', row.date + ' ' + ({ start: '始業', rest: '休憩', end: '終業' })[key]);
        input.oninput = () => {
          row[key] = input.value.trim(); row.edited = true;
          $('totattreview').checked = false;
          refreshValidation();
        };
        td.appendChild(input); tr.appendChild(td);
      });
      const note = document.createElement('td'); note.id = 'totattrow' + index; tr.appendChild(note);
      body.appendChild(tr);
    });
    $('totattfillbreak').disabled = data.defaultBreak === null;
    $('totattfillbreak').textContent = data.defaultBreak === null ? '固定休憩の登録なし（手入力）' : '空欄の休憩に固定時間（' + data.defaultBreak + '分）を補う';
    $('totattreview').checked = false;
    $('totattpreview').hidden = false;
    refreshValidation();
  }
  function clipboardData(rows) {
    const values = rows.map(row => {
      const checked = checkedValues(row);
      if (checked.error) throw Error(checked.error);
      return checked.values;
    });
    return {
      text: values.map(row => row.map(value => value === null ? '' : displayTime(value)).join('\t')).join('\r\n') + '\r\n',
      html: '<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8"></head><body><table>' +
        values.map(row => '<tr>' + row.map(value => value === null ? '<td></td>' :
          '<td x:num="' + String(value / 1440) + '" style="mso-number-format:\'\\[h\\]:mm\'">' + displayTime(value) + '</td>').join('') + '</tr>').join('') +
        '</table></body></html>',
    };
  }
  async function copy() {
    if (!result || $('totattcopy').disabled) return;
    const data = clipboardData(result.rows);
    try {
      if (navigator.clipboard && navigator.clipboard.write && typeof ClipboardItem !== 'undefined') {
        try {
          await navigator.clipboard.write([new ClipboardItem({
            'text/plain': new Blob([data.text], { type: 'text/plain' }),
            'text/html': new Blob([data.html], { type: 'text/html' }),
          })]);
        } catch (_) { await navigator.clipboard.writeText(data.text); }
      } else if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(data.text);
      } else {
        const area = $('totattmanual'); area.hidden = false; area.value = data.text; area.select();
        if (!document.execCommand('copy')) throw Error('下の欄を選択してCtrl+Cでコピーしてください。');
        area.hidden = true;
      }
      status('コピーしました。Excel「' + result.layout.sheet + '」の ' + result.layout.cell + ' を選び、「値のみ」で貼り付けてください。');
    } catch (error) {
      const area = $('totattmanual'); area.hidden = false; area.value = data.text; area.select();
      status(error.message || '下の欄を選択してCtrl+Cでコピーしてください。');
    }
  }
  function bind() {
    if (!$('totattcsv')) return;
    $('totattcsv').onchange = async () => {
      clearResult(); csvData = ''; $('totattperson').replaceChildren(); $('totattmonth').replaceChildren();
      const ticket = revision;
      const file = $('totattcsv').files[0];
      if (!file) return;
      try {
        status('CSVを確認しています…');
        const encoded = await readFile(file, 3000000);
        const data = await post('parse', { csvData: encoded });
        if (ticket !== revision) return;
        csvData = encoded;
        data.people.forEach(p => $('totattperson').add(new Option(p.name, p.key)));
        data.months.forEach(m => $('totattmonth').add(new Option(m + '（前月16日～15日）', m)));
        status(data.people.length + '名分を読み込みました。従業員・締め月・本人の出勤簿Excelを選んでください。');
      } catch (error) { if (ticket === revision) status(error.message); }
    };
    $('totattbook').onchange = async () => {
      clearResult(); workbookData = '';
      const ticket = revision;
      const file = $('totattbook').files[0];
      if (!file) return;
      try { const data = await readFile(file, 5000000); if (ticket === revision) workbookData = data; }
      catch (error) { status(error.message); }
    };
    ['totattperson', 'totattmonth'].forEach(id => { $(id).onchange = clearResult; });
    $('totattprepare').onclick = async () => {
      clearResult();
      if (!csvData || !workbookData) { status('CSVと、貼り付け先の本人の出勤簿Excelを選択してください。'); return; }
      const ticket = revision;
      $('totattprepare').disabled = true;
      try {
        status('日付と貼り付け先を確認しています…');
        const data = await post('prepare', { csvData, workbookData, person: $('totattperson').value, month: $('totattmonth').value });
        if (ticket !== revision) return;
        render(data); status('読み込みました。休憩の空欄や打刻漏れを確認してください。');
      } catch (error) { if (ticket === revision) status(error.message); }
      finally { $('totattprepare').disabled = false; }
    };
    $('totattreview').onchange = refreshValidation;
    $('totattcopy').onclick = copy;
    $('totattfillbreak').onclick = () => {
      if (!result || result.defaultBreak === null) return;
      result.rows.forEach(row => {
        if (row.start && row.end && !row.rest) {
          const a = parseTime(row.start), b = parseTime(row.end);
          const duration = b - a + (b < a ? 1440 : 0);
          if (duration > result.defaultBreak) { row.rest = displayTime(result.defaultBreak); row.filled = true; }
        }
      });
      render(result);
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
})();
