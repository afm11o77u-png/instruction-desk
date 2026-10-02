"""Validation applied when sales posts or updates a posted instruction."""
REQUIRED = {
    'customer':'顧客名', 'owner':'受付者', 'request':'受付内容',
    'contract':'契約区分', 'employee':'従業員名',
    'barcodeColor':'バーコード色',
    'nameRequired':'ネームの有無', 'markShipDate':'マーク出荷日',
    'method':'納品方法',
}
STOCKS = ('石狩倉庫', '発寒支店', '管理担当持込')
METHODS = ('車両便', '送り', 'オリコン', '担当者へ', 'その他')

def check_posting(d):
    missing=[label for key,label in REQUIRED.items()
             if not isinstance(d.get(key),str) or not d[key].strip() or d[key].strip()=='未指定']
    if d.get('nameRequired') not in ['あり','なし'] and 'ネームの有無' not in missing: missing.append('ネームの有無')
    advance=d.get('advance')
    advance=advance.strip() if isinstance(advance,str) else (advance or '')
    if advance and advance not in ['する','しない']: missing.append('先出しの有無')
    sex = d.get('sex')
    sex = sex.strip() if isinstance(sex, str) else (sex or '')
    if sex and sex not in ['女性', '男性', 'その他']:
        missing.append('性別')
    items=d.get('items',[])
    if d.get('method') not in METHODS and '納品方法' not in missing: missing.append('納品方法')
    if d.get('method')=='その他' and not str(d.get('methodNote') or '').strip(): missing.append('納品方法（その他の内容）')
    if not items: missing.append('商品明細（1行以上）')
    for i,x in enumerate(items,1):
        for key,label in [('name','アイテム名')]:
            if not isinstance(x.get(key),str) or not x[key].strip(): missing.append(f'商品明細{i}行目の{label}')
        if x.get('stock') not in STOCKS: missing.append(f'商品明細{i}行目の在庫場所')
        if type(x.get('quantity')) is not int or not 1<=x['quantity']<=99999: missing.append(f'商品明細{i}行目の数量（1以上の整数）')
    if missing: raise ValueError('投函・変更保存には次の項目を入力してください：\n'+'、'.join(missing))
