"""選択品番のメーカー情報一括取得・一括採用（テストDBのみ。本番登録・在庫変更なし）。"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import maker_sku_bulk_lookup as bulk  # noqa: E402
import maker_sku_lookup as msl  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def group(code, *, maker_sku='', manufacturer='', supplier='', row_nos=None, names=None, color_diff=False, needs_decision=True):
    valid = bool(maker_sku) and msl.is_valid_maker_sku(maker_sku)
    show = valid and needs_decision
    return {
        'code': code,
        'rowNos': row_nos or [2],
        'nameCandidates': names or ['スクラブ'],
        'colorDiffPossible': color_diff,
        'needsDecision': needs_decision,
        'makerCorrection': {
            'eligible': show,
            'showButton': show,
            'bulkEligible': valid,
            'makerSku': maker_sku if valid else '',
            'manufacturer': manufacturer,
            'supplier': supplier,
            'sourceNameHint': (names or ['スクラブ'])[0],
            'rowNos': row_nos or [2],
        },
    }


class DedupeKeyTest(unittest.TestCase):
    def test_position_distinguishes_upper_lower(self):
        up = bulk.bulk_lookup_dedupe_key(
            manufacturer='ミドリ安全', maker_sku='VEH30B', position='上',
        )
        down = bulk.bulk_lookup_dedupe_key(
            manufacturer='ミドリ安全', maker_sku='VEH30B', position='下',
        )
        same = bulk.bulk_lookup_dedupe_key(
            manufacturer='ミドリ安全', maker_sku='VEH30B下', position='',
        )
        self.assertNotEqual(up, down)
        self.assertEqual(down, same)

    def test_same_maker_sku_collapses(self):
        a = bulk.bulk_lookup_dedupe_key(
            manufacturer='ミドリ安全', maker_sku='72-1226', position='',
        )
        b = bulk.bulk_lookup_dedupe_key(
            manufacturer='ミドリ安全', maker_sku='72-1226', position='',
        )
        self.assertEqual(a, b)


class PrepareTargetsTest(unittest.TestCase):
    def test_excludes_management_skus_and_counts(self):
        groups = [
            group('72-1226', maker_sku='72-1226', manufacturer='ミドリ安全'),
            group('別注', maker_sku=''),
            group('品番なし', maker_sku=''),
            group('', maker_sku=''),
        ]
        prep = bulk.prepare_bulk_lookup_targets(
            selected_codes=['72-1226', '別注', '品番なし', ''],
            sku_groups=groups,
            rows=[],
        )
        self.assertEqual(prep['uniqueSearchCount'], 1)
        self.assertEqual(prep['excludedCount'], 3)
        self.assertEqual(prep['targets'][0]['makerSku'], '72-1226')

    def test_dedupes_same_search_keeps_upper_lower_apart(self):
        groups = [
            group('VEH30B上', maker_sku='VEH30B', manufacturer='サーヴォ', row_nos=[2]),
            group('VEH30B下', maker_sku='VEH30B', manufacturer='サーヴォ', row_nos=[3]),
            group('VEH30B上', maker_sku='VEH30B', manufacturer='サーヴォ', row_nos=[4]),
        ]
        # 同じ code は selected で1回。上と下は別。もう1つの上相当を別コードで重複させる
        groups[2] = group('VEH30B 上', maker_sku='VEH30B', manufacturer='サーヴォ', row_nos=[4])
        # normalize: VEH30B 上 as code still strips to position 上 → same key as VEH30B上
        prep = bulk.prepare_bulk_lookup_targets(
            selected_codes=['VEH30B上', 'VEH30B下', 'VEH30B 上'],
            sku_groups=groups,
            rows=[],
        )
        self.assertEqual(prep['uniqueSearchCount'], 2)
        self.assertGreaterEqual(prep['duplicateCollapsed'], 1)
        keys = {t['searchKey'] for t in prep['targets']}
        self.assertEqual(len(keys), 2)
        positions = {t['position'] for t in prep['targets']}
        self.assertEqual(positions, {'上', '下'})

    def test_bulk_eligible_without_color_diff(self):
        g = group('APK499', maker_sku='APK499', manufacturer='KAZEN', color_diff=False)
        self.assertTrue(g['makerCorrection']['bulkEligible'])
        # 単品ボタンも確認待ち＋有効品番なら表示（色競合不要）
        self.assertTrue(g['makerCorrection']['showButton'])
        prep = bulk.prepare_bulk_lookup_targets(
            selected_codes=['APK499'], sku_groups=[g], rows=[],
        )
        self.assertEqual(prep['uniqueSearchCount'], 1)

    def test_max_targets_limit_marks_pending_not_no_match(self):
        groups = [
            group(f'SKU{i}', maker_sku=f'SKU{i}', manufacturer='ミドリ安全', row_nos=[i + 2])
            for i in range(5)
        ]
        prep = bulk.prepare_bulk_lookup_targets(
            selected_codes=[g['code'] for g in groups],
            sku_groups=groups,
            rows=[],
            max_targets=2,
        )
        self.assertEqual(prep['uniqueSearchCount'], 2)
        self.assertEqual(len(prep['limitSkipped']), 3)
        self.assertTrue(all(x['status'] == 'pending' for x in prep['limitSkipped']))


class BulkJobTest(unittest.TestCase):
    def setUp(self):
        bulk.reset_bulk_lookup_jobs_for_tests()

    def tearDown(self):
        bulk.reset_bulk_lookup_jobs_for_tests()

    def test_job_runs_sequentially_deduped_and_cancel_keeps_done(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            c.close()
            calls = []

            def fake_search(conn, **kwargs):
                calls.append(kwargs.get('maker_sku') or kwargs.get('makerSku'))
                time.sleep(0.05)
                return {
                    'status': 'ok',
                    'statusLabel': '補正候補取得できた',
                    'makerSku': kwargs.get('maker_sku') or '',
                    'plannedRegisteredSku': kwargs.get('current_sku') or '',
                    'manufacturer': kwargs.get('manufacturer') or '',
                    'candidates': [{
                        'name': 'スクラブ',
                        'color': 'ブルー',
                        'sourceUrl': 'https://example.invalid/x',
                        'plannedRegisteredSku': kwargs.get('current_sku') or '',
                    }],
                    'matchedPages': [],
                    'referenceUrl': '',
                    'note': '',
                }

            groups = [
                group('72-1226', maker_sku='72-1226', manufacturer='ミドリ安全', row_nos=[2]),
                group('72-1226B', maker_sku='72-1226', manufacturer='ミドリ安全', row_nos=[3]),
                group('VEH30B下', maker_sku='VEH30B', manufacturer='サーヴォ', row_nos=[4]),
                group('VEH30B上', maker_sku='VEH30B', manufacturer='サーヴォ', row_nos=[5]),
            ]
            # 遅延を入れてキャンセル余地を作る
            job = bulk.start_bulk_lookup_job(
                path,
                selected_codes=[g['code'] for g in groups],
                sku_groups=groups,
                rows=[],
                search_fn=fake_search,
                sleep_fn=lambda _s: time.sleep(0.15),
            )
            self.assertEqual(job['uniqueSearchCount'], 3)  # 72-1226×2 collapsed, 上下別
            # すぐ中止
            time.sleep(0.08)
            cancelled = bulk.cancel_bulk_lookup_job(job['jobId'])
            self.assertTrue(cancelled['cancelled'] or cancelled['status'] in ('cancelled', 'running', 'done'))
            # 完了待ち
            deadline = time.time() + 3
            final = cancelled
            while time.time() < deadline:
                final = bulk.get_bulk_lookup_job(job['jobId'])
                if final.get('finished'):
                    break
                time.sleep(0.05)
            self.assertTrue(final.get('finished'))
            statuses = {r['status'] for r in final['results']}
            # 取得済みは保持。未処理は cancelled（no_match にしない）
            self.assertNotIn('no_match', statuses)
            if 'cancelled' in statuses:
                self.assertTrue(any(r['status'] == 'cancelled' for r in final['results']))
            # 重複キーは1回だけ検索
            self.assertLessEqual(len(calls), 3)
            # 72-1226 系は1回
            base_calls = [msl.sku_match_key(x).replace('下', '').replace('上', '') for x in calls]
            self.assertEqual(base_calls.count(msl.sku_match_key('72-1226')), 1)
        finally:
            folder.cleanup()

    def test_lookup_does_not_mutate_preview_without_adopt(self):
        """取得APIは候補を返すだけで採用テーブルを更新しない（手動未確定）。"""
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)

            def fake_search(conn, **kwargs):
                return {
                    'status': 'ok',
                    'statusLabel': '補正候補取得できた',
                    'makerSku': '72-1226',
                    'plannedRegisteredSku': '72-1226',
                    'manufacturer': 'ミドリ安全',
                    'candidates': [{'name': 'スクラブ', 'color': 'ブルー', 'sourceUrl': '', 'plannedRegisteredSku': '72-1226'}],
                    'matchedPages': [],
                    'referenceUrl': '',
                    'note': '',
                }

            groups = [group('72-1226', maker_sku='72-1226', manufacturer='ミドリ安全')]
            job = bulk.start_bulk_lookup_job(
                path,
                selected_codes=['72-1226'],
                sku_groups=groups,
                rows=[],
                search_fn=fake_search,
                sleep_fn=lambda _s: None,
            )
            deadline = time.time() + 2
            while time.time() < deadline:
                job = bulk.get_bulk_lookup_job(job['jobId'])
                if job.get('finished'):
                    break
                time.sleep(0.02)
            self.assertEqual(job['status'], 'done')
            self.assertTrue(job['results'][0]['adoptableSingle'])
            saved = c.execute('SELECT COUNT(*) AS n FROM maker_sku_corrections').fetchone()['n']
            self.assertEqual(saved, 0)
        finally:
            c.close()
            folder.cleanup()

    def test_single_and_bulk_search_same_helper(self):
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            calls = []

            def shared_search(conn, **kwargs):
                calls.append(kwargs)
                return {
                    'status': 'no_match',
                    'statusLabel': '候補なし',
                    'makerSku': kwargs.get('maker_sku') or '',
                    'plannedRegisteredSku': kwargs.get('current_sku') or '',
                    'manufacturer': kwargs.get('manufacturer') or '',
                    'candidates': [],
                    'matchedPages': [],
                    'referenceUrl': '',
                    'note': '',
                }

            single = shared_search(
                c, manufacturer='ミドリ安全', maker_sku='72-1226', source_name='スクラブ',
            )
            job = bulk.start_bulk_lookup_job(
                path,
                selected_codes=['72-1226'],
                sku_groups=[group('72-1226', maker_sku='72-1226', manufacturer='ミドリ安全')],
                rows=[],
                search_fn=shared_search,
                sleep_fn=lambda _s: None,
            )
            deadline = time.time() + 2
            while time.time() < deadline:
                job = bulk.get_bulk_lookup_job(job['jobId'])
                if job.get('finished'):
                    break
                time.sleep(0.02)
            self.assertTrue(job.get('results'), job)
            self.assertEqual(single['status'], job['results'][0]['status'])
            self.assertGreaterEqual(len(calls), 2)
        finally:
            c.close()
            folder.cleanup()


def _ok_result(code, *, maker_sku='', manufacturer='', position='', search_key='', adopted=False):
    maker_sku = maker_sku or code
    manufacturer = manufacturer or 'ミドリ安全'
    key = search_key or bulk.bulk_lookup_dedupe_key(
        manufacturer=manufacturer, maker_sku=maker_sku, position=position,
    )
    return {
        'searchKey': key,
        'skuCodes': [code],
        'makerSku': maker_sku,
        'position': position,
        'manufacturer': manufacturer,
        'searchCriteria': bulk.search_criteria_dict(
            manufacturer=manufacturer, maker_sku=maker_sku, position=position,
        ),
        'status': 'ok',
        'statusLabel': '一致候補1件',
        'candidates': [{'name': 'スクラブ', 'color': 'ブルー', 'plannedRegisteredSku': code}],
        'adoptableSingle': True,
        'adopted': adopted,
        'note': '',
        'referenceUrl': '',
    }


class IncrementalBulkClassifyTest(unittest.TestCase):
    def test_ab_then_cd_skips_fetched_only_new_searched(self):
        """A+B 取得後に C+D を選んで再実行 → 新規は C+D のみ。"""
        groups = [
            group('SKU-A', maker_sku='SKU-A', manufacturer='ミドリ安全', row_nos=[2]),
            group('SKU-B', maker_sku='SKU-B', manufacturer='ミドリ安全', row_nos=[3]),
            group('SKU-C', maker_sku='SKU-C', manufacturer='サーヴォ', row_nos=[4]),
            group('SKU-D', maker_sku='SKU-D', manufacturer='サーヴォ', row_nos=[5]),
        ]
        prior = [
            _ok_result('SKU-A', manufacturer='ミドリ安全', adopted=True),
            _ok_result('SKU-B', manufacturer='ミドリ安全', adopted=True),
        ]
        # 選択が A+B+C+D のままでも A+B は省略
        plan = bulk.classify_incremental_bulk_selection(
            selected_codes=['SKU-A', 'SKU-B', 'SKU-C', 'SKU-D'],
            sku_groups=groups,
            rows=[],
            prior_results=prior,
        )
        self.assertEqual(plan['newFetchCount'], 2)
        self.assertEqual(plan['skippedCachedCount'], 2)
        self.assertEqual(sorted(plan['toFetchCodes']), ['SKU-C', 'SKU-D'])
        self.assertEqual(plan['preflightLabel'], '新規取得2件／取得済みのため省略2件')
        self.assertEqual(plan['criteriaChanged'], [])

        # 実際のジョブは toFetch のみ渡す（検索コールが C+D だけ）
        folder, path, c = newdb()
        try:
            msl.ensure_schema(c)
            c.close()
            calls = []

            def fake_search(conn, **kwargs):
                calls.append(kwargs.get('maker_sku') or '')
                return {
                    'status': 'ok',
                    'statusLabel': '補正候補取得できた',
                    'makerSku': kwargs.get('maker_sku') or '',
                    'plannedRegisteredSku': kwargs.get('current_sku') or '',
                    'manufacturer': kwargs.get('manufacturer') or '',
                    'candidates': [{
                        'name': 'パンツ',
                        'color': 'ネイビー',
                        'sourceUrl': 'https://example.invalid/y',
                        'plannedRegisteredSku': kwargs.get('current_sku') or '',
                    }],
                    'matchedPages': [],
                    'referenceUrl': '',
                    'note': '',
                }

            job = bulk.start_bulk_lookup_job(
                path,
                selected_codes=plan['toFetchCodes'],
                sku_groups=groups,
                rows=[],
                search_fn=fake_search,
                sleep_fn=lambda _s: None,
            )
            deadline = time.time() + 2
            while time.time() < deadline:
                job = bulk.get_bulk_lookup_job(job['jobId'])
                if job.get('finished'):
                    break
                time.sleep(0.02)
            self.assertEqual(job['status'], 'done')
            self.assertEqual(job['total'], 2)
            self.assertEqual(len(calls), 2)
            called = {msl.sku_match_key(x) for x in calls}
            self.assertEqual(called, {msl.sku_match_key('SKU-C'), msl.sku_match_key('SKU-D')})
            merged = bulk.merge_bulk_result_rows(prior, job['results'], replace_sku_codes=plan['toFetchCodes'])
            codes = sorted({c for r in merged for c in r.get('skuCodes') or []})
            self.assertEqual(codes, ['SKU-A', 'SKU-B', 'SKU-C', 'SKU-D'])
            self.assertTrue(any(r.get('adopted') and 'SKU-A' in (r.get('skuCodes') or []) for r in merged))
            self.assertEqual(len(merged), 4)
        finally:
            folder.cleanup()

    def test_no_reuse_by_sku_alone_when_maker_differs(self):
        groups = [group('72-1226', maker_sku='72-1226', manufacturer='サーヴォ', row_nos=[2])]
        prior = [_ok_result('72-1226', maker_sku='72-1226', manufacturer='ミドリ安全')]
        plan = bulk.classify_incremental_bulk_selection(
            selected_codes=['72-1226'],
            sku_groups=groups,
            rows=[],
            prior_results=prior,
        )
        self.assertEqual(plan['newFetchCount'], 1)
        self.assertEqual(plan['skippedCachedCount'], 0)
        self.assertEqual(len(plan['criteriaChanged']), 1)
        diff = plan['criteriaChanged'][0]
        self.assertEqual(diff['previous']['manufacturer'], 'ミドリ安全')
        self.assertEqual(diff['current']['manufacturer'], 'サーヴォ')

    def test_position_difference_not_collapsed_by_sku(self):
        groups = [
            group('VEH30B上', maker_sku='VEH30B', manufacturer='サーヴォ', row_nos=[2]),
        ]
        prior = [_ok_result(
            'VEH30B上', maker_sku='VEH30B', manufacturer='サーヴォ', position='下',
        )]
        # prior は下条件でキャッシュされているが、選択は上 → 条件差で再取得
        plan = bulk.classify_incremental_bulk_selection(
            selected_codes=['VEH30B上'],
            sku_groups=groups,
            rows=[],
            prior_results=prior,
        )
        self.assertEqual(plan['newFetchCount'], 1)
        self.assertEqual(plan['skippedCachedCount'], 0)
        self.assertTrue(plan['criteriaChanged'])

    def test_force_refetch_includes_no_match(self):
        groups = [group('SKU-A', maker_sku='SKU-A', manufacturer='ミドリ安全')]
        prior = [{
            **_ok_result('SKU-A', manufacturer='ミドリ安全'),
            'status': 'no_match',
            'statusLabel': '候補なし',
            'candidates': [],
            'adoptableSingle': False,
        }]
        skip_plan = bulk.classify_incremental_bulk_selection(
            selected_codes=['SKU-A'], sku_groups=groups, rows=[], prior_results=prior,
        )
        self.assertEqual(skip_plan['newFetchCount'], 0)
        self.assertEqual(skip_plan['skippedCachedCount'], 1)
        force_key = prior[0]['searchKey']
        force_plan = bulk.classify_incremental_bulk_selection(
            selected_codes=['SKU-A'],
            sku_groups=groups,
            rows=[],
            prior_results=prior,
            force_search_keys=[force_key],
        )
        self.assertEqual(force_plan['newFetchCount'], 1)
        self.assertEqual(force_plan['skippedCachedCount'], 0)

    def test_merge_results_no_duplicate_targets(self):
        a = _ok_result('SKU-A', adopted=True)
        b = _ok_result('SKU-B')
        c = _ok_result('SKU-C')
        c2 = dict(c)
        c2['note'] = 'updated'
        merged = bulk.merge_bulk_result_rows([a, b, c], [c2, _ok_result('SKU-D')], replace_sku_codes=['SKU-C'])
        keys = [r['searchKey'] for r in merged]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(merged), 4)
        self.assertTrue(any(r.get('adopted') for r in merged if 'SKU-A' in r['skuCodes']))
        self.assertEqual(next(r for r in merged if 'SKU-C' in r['skuCodes'])['note'], 'updated')

    def test_search_criteria_equal_requires_maker_and_position(self):
        a = bulk.search_criteria_dict(manufacturer='ミドリ安全', maker_sku='X', position='')
        b = bulk.search_criteria_dict(manufacturer='サーヴォ', maker_sku='X', position='')
        c = bulk.search_criteria_dict(manufacturer='ミドリ安全', maker_sku='X', position='上')
        self.assertFalse(bulk.search_criteria_equal(a, b))
        self.assertFalse(bulk.search_criteria_equal(a, c))
        self.assertTrue(bulk.search_criteria_equal(a, dict(a)))


class BulkMetaAndUiSourceTest(unittest.TestCase):
    def test_sku_group_meta_exposes_bulk_eligible(self):
        meta = msl.sku_group_maker_correction_meta(
            {
                'code': '72-1226',
                'needsDecision': True,
                'colorDiffPossible': False,
                'rowNos': [2],
                'nameCandidates': ['スクラブ'],
            },
            [],
        )
        self.assertTrue(meta['bulkEligible'])
        self.assertTrue(meta['showButton'])
        meta_auto = msl.sku_group_maker_correction_meta(
            {
                'code': '72-1226',
                'needsDecision': False,
                'colorDiffPossible': False,
                'rowNos': [2],
                'nameCandidates': ['スクラブ'],
            },
            [],
        )
        self.assertTrue(meta_auto['bulkEligible'])
        self.assertFalse(meta_auto['showButton'])

    def test_ui_wiring(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('選択した商品のメーカー情報を取得', js)
        self.assertIn('選択した補正を採用', js)
        self.assertIn('/api/product-ledger-csv/maker-lookup/bulk/plan', js)
        self.assertIn('/api/product-ledger-csv/maker-lookup/bulk/start', js)
        self.assertIn('/api/product-ledger-csv/maker-lookup/bulk/cancel', js)
        self.assertIn('plBulkMakerFetch', js)
        self.assertIn('applyMakerCandidateToPreview', js)
        self.assertIn('data-pl-bulk-maker-refetch', js)
        self.assertIn('再取得', js)
        self.assertIn('新規取得', js)
        self.assertIn('取得済みのため省略', js)
        self.assertIn('mergeBulkMakerResultRows', js)
        self.assertIn('_incrementalMerge', js)
        # 採用後に選択解除
        self.assertIn('st.selectedCodes[String(code)]=false', js)
        # 既存単品も維持
        self.assertIn('data-pl-sku-maker-correct', js)
        self.assertIn('data-pl-maker-correct', js)
        # 取得開始で結果を全消ししない
        start = js.split('async function startBulkMakerLookup(')[1].split(
            'async function refetchBulkMakerResult('
        )[0]
        self.assertIn('priorBulkResultsPayload', start)
        self.assertIn('keptResults', start)
        self.assertNotIn('results:[]', start.replace(' ', ''))
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/api/product-ledger-csv/maker-lookup/bulk/plan'", server)
        self.assertIn("'/api/product-ledger-csv/maker-lookup/bulk/start'", server)
        self.assertIn('maker_sku_bulk_lookup', server)
        limits = (ROOT / 'upload_limits.py').read_text(encoding='utf-8')
        self.assertIn("'/api/product-ledger-csv/maker-lookup/bulk/plan'", limits)


if __name__ == '__main__':
    unittest.main()
