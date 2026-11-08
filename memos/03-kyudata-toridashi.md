# 旧データの取り出し方
このメモは、旧システムの画面からファイルを取り出すときの記録です。商品台帳CSVと旧在庫JSONは別の手順です。ここに載せるコードは、当時の案内の原文です。
## 用途の区別
1. 商品台帳CSVは、旧商品一覧の画面で F12 の Console にコードを貼り、表示中の表から保存します。Network の Copy response ではありません。
2. 旧在庫JSONは、開発者ツールの Network で、選択中の api を Copy response して保存します。商品台帳CSVのコードとは別です。
3. 旧顧客JSONの具体的な取り出し手順は、このメモにはまだ載せていません。対象の通信や取得コードは、原文が確認できてから足します。
## 商品台帳CSV
実行する画面は、旧システムの商品一覧（item_master）です。F12 で開発者ツールを開き、Console に貼ります。
最初の `document.querySelector('table')` は、行数1、見出しがすべて undefined で、空っぽでした。診断では、商品データは2番目の表で、当時の画面では 2128 行でした。最初の表は見出し、2番目が本文です。
修正版は、その2番目の表の5列を `商品台帳.csv` として保存するコードです。対象は、そのとき画面に読み込まれている行だけです。2128 行は当時の件数であり、今後の全件である保証はありません。実行したあと、保存されたCSVの内容と、Console に出る「保存したデータ行数」を確認してください。このメモの範囲では、修正版の保存成功を確認した記録はありません。
### 診断（表の番号と行数を見る）
```
console.table([...document.querySelectorAll('table')].map((t,i)=>({
  表番号:i,
  行数:t.rows.length,
  本文行数:t.querySelectorAll('tbody tr').length,
  先頭部分:(t.textContent||'').replace(/\s+/g,' ').trim().slice(0,180)
})));
```
### 商品台帳CSVの保存（2番目の表）
```
(() => {
  const t = document.querySelectorAll('table')[1];
  if (!t) throw new Error('商品データの表が見つかりません');
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [...t.rows]
    .filter(r => r.querySelector('td'))
    .map(r => [...r.cells].map(c => clean(c.textContent)))
    .filter(r => r.some(Boolean));
  if (!rows.length || rows.some(r => r.length < 5)) {
    throw new Error('表の列構成が異なるため、保存を中止しました');
  }
  const headers = ['顧客', '仕入先', '商品コード', '商品名', 'サイズ'];
  const quote = s => '"' + s.replace(/"/g, '""') + '"';
  const csv = [headers, ...rows.map(r => r.slice(0, 5))]
    .map(r => r.map(quote).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob(['\ufeff' + csv], {
    type: 'text/csv;charset=utf-8'
  }));
  const a = document.createElement('a');
  a.href = url;
  a.download = '商品台帳.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  console.log('保存したデータ行数:', rows.length);
})();
```
保存したファイルは、新しいシステムの商品登録で商品台帳CSVとして使います。在庫数量・単価・旧QRは、このCSVだけでは書きません。
## 旧在庫JSON
記録にある手順は、次のとおりです。
旧IDの `id` と、在庫数の `calc_stock_count` を確認します。左側で選択中の `api` を右クリックし、Copy → Copy response を選びます。メモ帳に貼り付け、`旧在庫データ.json` という名前で保存します。
この手順のあと、14775件の旧在庫JSONが添付されています。商品台帳CSVを取るコードとは別です。このメモには、旧在庫JSONを取るための Console 用コードは載せていません。
保存したファイルは、新しいシステムの初期在庫の登録で旧在庫JSONとして選びます。商品登録で選ぶ場合は、通常仕入先とメイン候補の判定だけに使い、在庫数量・単価・旧QRは書きません。
## 旧顧客JSON
旧顧客JSONの、どの通信を選ぶか、どう保存するかの原文は、まだ確認できていません。確かでない通信の指定や、取得用のコードは載せていません。
