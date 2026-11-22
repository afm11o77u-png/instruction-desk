import json
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import test_web_request_settings as fixtures
import server
import rental_management

class RentalHTTPTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SettingsTest();self.fixture.setUp();self.root=Path(self.fixture.tmp.name)
        self.desk=self.root/'desk.sqlite3'
        target=sqlite3.connect(self.desk);self.fixture.c.backup(target);target.close()
        self.rental=self.root/'rental.sqlite3';rental_management.initialize(self.rental)
        class Handler(server.Handler):
            def session_user(self,c):
                role=self.headers.get('X-Test-Role')
                return dict(id=1,name='検証者',role=role,manage=0,must_change=0) if role else None
            def log_message(self,*args):pass
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.host='127.0.0.1:'+str(self.http.server_address[1]);self.url='http://'+self.host
        self.patches=[patch.object(server,'DB',self.desk),patch.object(server,'STOCK_DB',self.fixture.path),patch.object(server,'RENTAL_DB',self.rental),patch.object(server,'ALLOWED_HOSTS',{self.host}),patch.object(server,'ALLOWED_ORIGINS',{self.url})]
        for p in self.patches:p.start()
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        for p in reversed(self.patches):p.stop()
        self.fixture.tearDown()
    def request(self,path,role=None,body=None,desk_header=True,origin=None):
        headers={}
        if role:headers['X-Test-Role']=role
        if desk_header:headers['X-Desk-Request']='1'
        if origin:headers['Origin']=origin
        if body is not None:headers['Content-Type']='application/json'
        req=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(req,timeout=5) as response:return response.status,response.read()
        except HTTPError as e:return e.code,e.read()
    def test_page_assets_and_api_require_staff_login(self):
        for path in ['/rental','/rental/app.js','/api/rental/catalog','/api/rental/records?entity=summary']:
            self.assertEqual(self.request(path)[0],401)
            self.assertEqual(self.request(path,'floor')[0],403)
        self.assertEqual(self.request('/rental','owner')[0],200)
        code,body=self.request('/api/rental/catalog','warehouse');self.assertEqual(code,200);self.assertFalse(json.loads(body)['canWrite'])
    def test_post_rejects_readonly_and_missing_csrf_header(self):
        self.assertEqual(self.request('/api/rental/save','warehouse',{})[0],403)
        self.assertEqual(self.request('/api/rental/save','owner',{},desk_header=False)[0],403)
        self.assertEqual(self.request('/api/rental/save','owner',{},origin='http://outside.example')[0],403)
    def test_dedicated_database_persists_and_inventory_stays_unchanged(self):
        before=self.fixture.path.read_bytes()
        code,_=self.request('/api/rental/save','owner',dict(entity='unit',customer_id=1,barcode='TEST-INDIVIDUAL',customer_product_id=10,size='M',status='待機'),origin=self.url)
        self.assertEqual(code,200)
        code,body=self.request('/api/rental/records?entity=units','owner');self.assertEqual(code,200);self.assertEqual(json.loads(body)['total'],1)
        self.assertEqual(before,self.fixture.path.read_bytes())

if __name__=='__main__':unittest.main()
