from __future__ import annotations

import hashlib
import http.client
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.runtime import AssetServer, DesktopError, InstanceLock, safe_path, verify_bundle


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='识矿-runtime-')
        self.root = Path(self.temp.name).resolve()
        (self.root / 'web/vendor').mkdir(parents=True)
        (self.root / 'desktop').mkdir()
        (self.root / 'desktop/bridge.js').write_text('window.info=__DESKTOP_METADATA__;', encoding='utf-8')
        (self.root / 'web/index.html').write_text('<html><head></head><body>矿物识别</body></html>', encoding='utf-8')
        (self.root / 'web/vendor/test.wasm').write_bytes(b'wasm-resource')
        self.manifest = {'app_version': '2.5-test', 'model_version': 'not-a-training-metric',
            'model_manifest_sha256': 'a'*64, 'web_commit': 'b'*40,
            'files': {'index.html': {}, 'vendor/test.wasm': {}}}
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.stop()
        self.temp.cleanup()

    def start(self):
        self.server = AssetServer(self.root, self.manifest, port=0).start()
        return self.server.server_address[1]

    def request(self, path, *, host=None, method='GET'):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        connection.request(method, path, headers={'Host': host or '127.0.0.1:' + str(self.server.server_address[1])})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_identity_health_and_trusted_bootstrap_same_origin(self):
        self.start()
        status, headers, data = self.request('/__shikuang_health__')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['nonce'], self.server.nonce)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        status, headers, data = self.request('/?v=old-cache')
        self.assertEqual(status, 200)
        self.assertIn('/__shikuang_desktop__.js', data.decode('utf-8'))
        self.assertIn('矿物识别', data.decode('utf-8'))
        status, _, bridge = self.request('/__shikuang_desktop__.js')
        self.assertEqual(status, 200)
        self.assertIn(self.server.origin.encode(), bridge)

    def test_only_exact_allowlisted_resources_and_no_upload_api(self):
        self.start()
        for path in ['/vendor/', '/.web-assets.json', '/../../secret', '/%2e%2e/secret', '/index.html/../vendor/test.wasm', '/vendor%5ctest.wasm']:
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request('/index.html', host='attacker.invalid')[0], 421)
        self.assertEqual(self.request('/index.html', method='POST')[0], 501)
        status, headers, data = self.request('/vendor/test.wasm?manifest=identity')
        self.assertEqual((status, headers['Content-Type'], data), (200, 'application/wasm', b'wasm-resource'))
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        status, headers, data = self.request('/vendor/test.wasm', method='HEAD')
        self.assertEqual((status, headers['Content-Length'], data), (200, '13', b''))

    def test_unknown_listener_never_contacted_and_close_releases_port(self):
        unknown = socket.socket()
        unknown.bind(('127.0.0.1', 0)); unknown.listen(); unknown.settimeout(0.1)
        try:
            with self.assertRaisesRegex(DesktopError, '未连接任何未知服务'):
                AssetServer(self.root, self.manifest, port=unknown.getsockname()[1])
            with self.assertRaises(socket.timeout):
                unknown.accept()
        finally:
            unknown.close()
        port = self.start()
        self.request('/')
        self.server.stop(); self.server = None
        self.server = AssetServer(self.root, self.manifest, port=port).start()
        self.assertEqual(self.request('/')[0], 200)

    def test_single_instance_lock_is_released_without_removing_history(self):
        path = self.root / '资料目录/desktop.lock'
        first = InstanceLock(path).acquire()
        try:
            with self.assertRaisesRegex(DesktopError, '已在运行'):
                InstanceLock(path).acquire()
        finally:
            first.close()
        InstanceLock(path).acquire().close()
        self.assertTrue(path.exists())

    def test_path_and_asset_identity_fail_closed(self):
        outside = self.root / 'secret'; outside.write_bytes(b'secret')
        (self.root / 'web/linked').symlink_to(outside)
        for name in ['../secret', 'linked', '/index.html', 'vendor\\test.wasm', 'vendor/../index.html']:
            with self.assertRaises(DesktopError):
                safe_path(self.root / 'web', name)
        with self.assertRaisesRegex(DesktopError, '资源校验失败'):
            verify_bundle(self.root)

    def test_full_bundle_detects_hash_changed_model_and_missing_data(self):
        # Validate the real staged shared build without duplicating its schema in a mock.
        package = Path(__file__).resolve().parents[1]
        if not (package / 'web/.web-assets.json').exists():
            self.skipTest('Run sync_web.py first')
        actual = verify_bundle(package)
        name = next(x for x in actual['files'] if x.startswith('models/'))
        real_sha = actual['files'][name]['sha256']
        from desktop import runtime
        original = runtime.sha256
        with patch.object(runtime, 'sha256', side_effect=lambda p: '0'*64 if p.as_posix().endswith(name) else original(p)):
            with self.assertRaisesRegex(DesktopError, '资源校验失败'):
                verify_bundle(package)
        self.assertNotEqual(real_sha, '0'*64)


if __name__ == '__main__':
    unittest.main()
