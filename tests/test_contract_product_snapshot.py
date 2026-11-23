import json
import sqlite3
import unittest
import picking

class ContractProductSnapshotTest(unittest.TestCase):
    def test_instruction_reference_is_copied_to_picking_snapshot(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        self.addCleanup(c.close)
        c.executescript('CREATE TABLE orders(id INTEGER,version INTEGER,payload TEXT);CREATE TABLE users(id INTEGER,name TEXT);INSERT INTO users VALUES(1,\'担当者\');')
        item=dict(name='作業服',sku='P-1',size='M',quantity=3,stock='石狩倉庫',product_id='15',customer_product_id='25',contract_customer_id='5',contract_id='contract-a',contract_uniform_id='uniform-a',department_id='dept-a',contract_scope_id='',contract_version='7',contract_name='レンタル契約',department_name='製造',service_type='レンタル',color='青',embroidery='社名刺繍')
        order=dict(status='未着手',assigneeId=1,customer='顧客',items=[item])
        c.execute('INSERT INTO orders VALUES(1,1,?)',(json.dumps(order),))
        source=picking.imported(c,dict(id=1),dict(id=1,version=1),lambda u,d:True)
        row=source['rows'][0]
        for field in picking.REFERENCE_FIELDS:self.assertEqual(row[field],item[field])
        picking.validate_rows(source['rows'],True)
        stored=json.loads(json.dumps(source))
        order['items'][0].update(name='変更後商品',contract_name='変更後契約',color='赤')
        c.execute('UPDATE orders SET version=2,payload=?',(json.dumps(order),))
        self.assertEqual(stored['rows'][0]['name'],'作業服')
        self.assertEqual(stored['rows'][0]['contract_name'],'レンタル契約')
        self.assertEqual(stored['rows'][0]['color'],'青')
        self.assertEqual(stored['rows'][0]['customer_product_id'],'25')

if __name__=='__main__':unittest.main()
