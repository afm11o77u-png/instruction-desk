import re, unittest
from pathlib import Path
S=Path(__file__).resolve().parents[1]/'static'
APP=(S/'app.js').read_text(encoding='utf-8')
PO=(S/'purchase_orders.js').read_text(encoding='utf-8')
class RowOrderButtonTest(unittest.TestCase):
    def test_row_buttons_order_not_stocktake(self):
        m=re.search(r'function stockActionButtonsHtml\(.*?\n}\n',APP,re.S).group(0)
        self.assertIn('>発注</button>',m)
        self.assertNotIn('棚卸',m)
        self.assertIn("if(!stockCanWrite())return '—'",m)
    def test_order_click_opens_prefilled_new_po(self):
        f=re.search(r'async function openOrderFromStockRow\(row\)\{.*?\n}\n',APP,re.S).group(0)
        self.assertIn("setStockSection('orders')",f)
        self.assertIn('PurchaseOrders.openEditor(null,{customerId:row.customerId,supplierId:product&&product.supplierId||null,customerProductId:row.customerProductId,size:row.size',f)
    def test_stocktake_code_kept(self):
        self.assertIn('function openStocktakeDialog',APP)
    def test_prefill_only_for_new(self):
        self.assertIn('openEditor(existing,prefill){',PO)
        self.assertIn('const pre=!existing&&prefill?prefill:null',PO)
        self.assertIn('customerId:existing?existing.customerId:(pre&&pre.customerId)||null',PO)
        self.assertIn('forceShow=true',PO)
        self.assertIn('候補がありません',PO)
if __name__=='__main__':unittest.main()
