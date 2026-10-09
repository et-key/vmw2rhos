import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import unittest

from vmw2rhos.__main__ import make_handler
from vmw2rhos.planner import empty_inventory
from vmw2rhos.store import Store, ConflictError


class StoreTests(unittest.TestCase):
    def test_restart_retains_history_and_rejects_stale_save(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'plans.sqlite3'
            store = Store(path)
            initial = store.load()
            updated = store.save(empty_inventory(), initial['revision'])
            self.assertEqual(updated['revision'], 2)
            with self.assertRaises(ConflictError):
                store.save(empty_inventory(), 1)
            restarted = Store(path)
            self.assertEqual(restarted.load(), updated)
            self.assertEqual(restarted.load(1), initial)

    def test_invalid_import_does_not_replace_saved_data(self):
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'plans.sqlite3')
            before = store.load()
            with self.assertRaises(ValueError):
                store.save({'schema_version': 2}, 1)
            self.assertEqual(store.load(), before)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'plans.sqlite3')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, 0))
        self.port = self.server.server_port
        self.server.RequestHandlerClass = make_handler(self.store, self.port)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        request = Request(f'http://127.0.0.1:{self.port}{path}',
                          data=None if body is None else json.dumps(body).encode(), headers=request_headers)
        return urlopen(request, timeout=5)

    def test_ui_sample_check_save_and_plan(self):
        with self.request('/') as response:
            self.assertIn('移行計画', response.read().decode())
        with self.request('/api/sample') as response:
            sample = json.load(response)
        with self.request('/api/check', {'inventory': sample}) as response:
            self.assertFalse(json.load(response)['executable'])
        with self.request('/api/inventory', {'inventory': sample, 'revision': 1}) as response:
            self.assertEqual(json.load(response)['revision'], 2)
        with self.request('/api/plan') as response:
            plan = json.load(response)
            self.assertEqual(plan['revision'], 2)
            self.assertEqual(plan['order'], ['vc01:vm-101', 'vc01:vm-102'])

    def test_bad_import_and_conflict_have_correct_http_status(self):
        for body, status in (({'inventory': {}, 'revision': 1}, 400),
                             ({'inventory': empty_inventory(), 'revision': 0}, 409)):
            with self.subTest(status=status):
                with self.assertRaises(HTTPError) as context:
                    self.request('/api/inventory', body)
                self.assertEqual(context.exception.code, status)
                context.exception.close()

    def test_cross_origin_and_rebinding_are_rejected(self):
        for headers in ({'Origin': 'https://example.invalid'}, {'Host': 'example.invalid'}):
            with self.assertRaises(HTTPError) as context:
                self.request('/api/inventory', {'inventory': empty_inventory(), 'revision': 1}, headers)
            self.assertEqual(context.exception.code, 403)
            context.exception.close()
        self.assertEqual(self.store.load()['revision'], 1)

    def test_no_live_run_endpoint(self):
        with self.assertRaises(HTTPError) as context:
            self.request('/api/run', {})
        self.assertEqual(context.exception.code, 404)
        context.exception.close()

    def test_comparison_api_and_blank_observation_template(self):
        with self.request('/api/sample') as response:
            inventory = json.load(response)
        with self.request('/api/compare', {'inventory': inventory}) as response:
            report = json.load(response)
            self.assertIn('planned-change', report['counts'])
            self.assertFalse(report['migration_verified'])
        with self.request('/api/observation-template', {'inventory': inventory}) as response:
            template = json.load(response)
            self.assertEqual(template['plan_fingerprint'], report['plan_fingerprint'])
            self.assertTrue(all(v is None for r in template['resources'] for v in r['values'].values()))
        with self.assertRaises(HTTPError) as context:
            self.request('/api/compare', {'inventory': inventory, 'observation': {}})
        self.assertEqual(context.exception.code, 400)
        context.exception.close()


if __name__ == '__main__':
    unittest.main()
