"""Synthetic persistence/lifecycle contracts; no WebView, photographs or model loads."""
from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from desktop import run_app
from desktop.smoke import Smoke, expected_write_snapshot, record_id, source_identity, private_backup_path, validate_private_backup, CONTRACT

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = '01234567-89ab-cdef-0123-456789abcdef'
MANIFEST = {'app_version': '2.5-fixture', 'model_manifest_sha256': 'a' * 64,
            'web_commit': 'b' * 40, 'model_version': 'fixture-not-inference', 'files': {}}


def write_document():
    baseline = synthetic_private_backup(None)['baseline']
    snapshot = {'schema': 'shikuang-persistence-snapshot-v2', 'record_id': record_id(RUN_ID),
                'photo_count': 1, 'sample_id': 'desktop-smoke-' + RUN_ID, 'baseline': baseline,
                **{key: 'c' * 64 for key in ('record_sha256', 'photos_sha256')}}
    return {'passed': True, 'errors': [], 'phase': 'write', 'run_id': RUN_ID,
            **{key: MANIFEST[key] for key in ('app_version', 'model_manifest_sha256', 'web_commit')},
            'host': {'run_id': RUN_ID, 'phase': 'write', 'pid': 1234, 'launch_id': 'd' * 32},
            'smoke_source_identity': source_identity(MANIFEST), 'baseline': baseline,
            'persistence': {'mode': 'write-content-snapshot', 'verified_before_any_put': False, 'snapshot': snapshot}}


def synthetic_private_backup(path):
    # Fixture text deliberately differs from Python's usual float formatting.
    records = '[{"id":"old-fixed-id","photos":["SYNTHETIC-NOT-A-PHOTO"],"score":0.000001,"whole":1}]'
    local = '[["other","KEEP"],["shikuang-searches-v2"," raw history "]]'
    search = '" raw history "'
    baseline = {'schema': 'shikuang-user-baseline-v2', 'record_count': 1,
                'records_sha256': hashlib.sha256(records.encode()).hexdigest(),
                'local_storage_sha256': hashlib.sha256(local.encode()).hexdigest(),
                'search_raw_sha256': hashlib.sha256(search.encode()).hexdigest()}
    return {'schema': CONTRACT + '-private-backup-v2', 'encoding': 'canonical-json-utf8-v1',
            'run_id': RUN_ID, 'record_id': record_id(RUN_ID), 'source_identity': source_identity(MANIFEST),
            'baseline': baseline, 'records': records, 'local_storage': local, 'search_raw': search}


class PriorWriteContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='识矿-收据-')
        self.path = Path(self.temp.name).resolve() / 'write report.json'
        self.host = {'launch_id': 'e' * 32}

    def tearDown(self):
        self.temp.cleanup()

    def read(self, document):
        private = private_backup_path(self.path, RUN_ID)
        private.parent.mkdir(mode=0o700, exist_ok=True)
        raw = json.dumps(synthetic_private_backup(private)).encode()
        private.write_bytes(raw)
        private.chmod(0o600)
        if isinstance(document, dict):
            document['private_backup'] = {'path': str(private), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8-sig')
        return expected_write_snapshot(self.path, MANIFEST, RUN_ID, self.host)

    def test_exact_report_bytes_and_populated_snapshot_are_bound(self):
        document = write_document()
        result = self.read(document)
        self.assertEqual(result['snapshot'], document['persistence']['snapshot'])
        body = self.path.read_bytes()
        self.assertEqual(result['report'], {'path': str(self.path.resolve()), 'bytes': len(body),
                                          'sha256': hashlib.sha256(body).hexdigest()})

    def test_wrong_run_phase_package_errors_or_host_cannot_be_reused(self):
        changes = [('passed', False), ('phase', 'restart'), ('run_id', 'other'), ('errors', ['late page error']),
                   ('app_version', 'other'), ('model_manifest_sha256', '0' * 64), ('web_commit', '0' * 40)]
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.read(dict(write_document(), **{key: value}))
        for change in ({'run_id': 'other'}, {'phase': 'restart'}, {'pid': True}, {'pid': 0},
                       {'launch_id': self.host['launch_id']}, {'launch_id': None}, {'launch_id': 'short'}):
            document = write_document()
            document['host'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.read(document)

    def test_missing_populated_photo_search_or_malformed_snapshot_fails_closed(self):
        changes = {'schema': 'old', 'record_id': 'another-id', 'photo_count': 0,
                   'sample_id': 'ordinary-query', 'record_sha256': 'x' * 64,
                   'photos_sha256': None}
        for key, value in changes.items():
            document = write_document()
            document['persistence']['snapshot'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.read(document)
        for document in ([], {'passed': True}, dict(write_document(), host=[]), dict(write_document(), persistence=[])):
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.read(document)
        document = write_document()
        document['persistence']['snapshot'] = []
        with self.assertRaises(ValueError):
            self.read(document)

    def test_source_nonce_private_backup_and_baseline_are_bound(self):
        for change in ('source', 'nonce', 'baseline'):
            doc = write_document()
            if change == 'source': doc['smoke_source_identity']['script_sha256'] = '0' * 64
            if change == 'nonce': doc['persistence']['snapshot']['record_id'] = 'desktop-runtime-smoke-record'
            if change == 'baseline': doc['baseline']['records_sha256'] = 'broken'
            with self.subTest(change=change), self.assertRaises(ValueError): self.read(doc)
        self.read(write_document())
        private = private_backup_path(self.path, RUN_ID)
        private.write_bytes(b'changed synthetic backup')
        with self.assertRaisesRegex(ValueError, 'backup bytes changed'):
            expected_write_snapshot(self.path, MANIFEST, RUN_ID, self.host)

    def test_content_mismatch_with_recomputed_file_pin_still_refuses_restart(self):
        changes = [lambda p:p.update(records=p['records'].replace('old-fixed-id', 'changed-id')),
                   lambda p:p.update(local_storage=p['local_storage'].replace('KEEP', 'changed')),
                   lambda p:p.update(local_storage=json.dumps(list(reversed(json.loads(p['local_storage']))))),
                   lambda p:p.update(search_raw='"changed history"'),
                   lambda p:p['baseline'].update(record_count=2)]
        for change in changes:
            self.read(write_document())
            private = private_backup_path(self.path, RUN_ID)
            payload = json.loads(private.read_bytes()); change(payload)
            raw = json.dumps(payload).encode(); private.write_bytes(raw)
            report = json.loads(self.path.read_text(encoding='utf-8-sig'))
            report['private_backup'].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            self.path.write_text(json.dumps(report))
            with self.subTest(change=change), self.assertRaises(ValueError):
                expected_write_snapshot(self.path, MANIFEST, RUN_ID, self.host)

    def test_validator_hashes_saved_JS_text_and_rejects_bad_shape_or_owned_id(self):
        payload = synthetic_private_backup(None)
        self.assertEqual(validate_private_backup(payload, MANIFEST, RUN_ID), payload['baseline'])
        for key, value in [('records', '[null]'), ('records', json.dumps([{'id':record_id(RUN_ID)}])),
                           ('local_storage', '[["x","1"],["x","2"]]')]:
            altered = dict(payload, **{key:value})
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                validate_private_backup(altered, MANIFEST, RUN_ID)

    def test_missing_or_oversized_report_is_not_read_as_backup(self):
        with self.assertRaises(ValueError):
            expected_write_snapshot(self.path, MANIFEST, RUN_ID, self.host)
        with self.path.open('wb') as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            expected_write_snapshot(self.path, MANIFEST, RUN_ID, self.host)


class FinishContract(unittest.TestCase):
    def make_smoke(self, path, expected):
        smoke = Smoke.__new__(Smoke)  # Deliberately no fixture reads or renderer.
        smoke.finished = threading.Event()
        smoke.expected_persistence = expected
        smoke.private_backup = None
        smoke.baseline = None
        smoke.manifest = MANIFEST
        smoke.phase = 'restart'
        smoke.run_id = RUN_ID
        smoke.host = {'launch_id': 'e' * 32}
        smoke.report = path
        smoke.success = False
        smoke.window = types.SimpleNamespace(destroy=lambda: None)
        return smoke

    def test_callback_requires_full_matching_snapshot_and_no_page_errors(self):
        expected = {'snapshot': write_document()['persistence']['snapshot']}
        valid = {'passed': True, 'errors': [], 'persistence': {
            'mode': 'full-content-sha256-before-any-put', 'verified_before_any_put': True,
            'cleanup_verified': True,
            'snapshot': expected['snapshot']}}
        cases = [valid, dict(valid, errors=['late page error']), dict(valid, persistence=[]),
                 dict(valid, persistence={'mode': 'legacy-id-only'}), dict(valid, persistence={
                     **valid['persistence'], 'snapshot': dict(expected['snapshot'], photos_sha256='0' * 64)})]
        with tempfile.TemporaryDirectory() as directory, patch('desktop.smoke.sys.platform', 'synthetic-no-webview'):
            for index, result in enumerate(cases):
                smoke = self.make_smoke(Path(directory) / (str(index) + '.json'), expected)
                smoke.finish(copy.deepcopy(result))
                saved = json.loads(smoke.report.read_text())
                self.assertEqual(saved['passed'], index == 0)
                self.assertEqual(smoke.success, index == 0)
                self.assertEqual(saved['run_id'], RUN_ID)
                self.assertFalse(saved['accuracy_claim'])


class PrivateBackupIOContract(unittest.TestCase):
    def invoke(self, directory, collision=False, change_payload=None):
        # Real backup IO uses only this temporary synthetic payload. No fixture
        # constructor, AppKit, real WebView, photos or userdata is accessed.
        smoke = Smoke.__new__(Smoke)
        smoke.report = Path(directory).resolve() / 'write.json'
        smoke.phase = 'write'; smoke.manifest = MANIFEST; smoke.run_id = RUN_ID
        smoke.record_id = record_id(RUN_ID); smoke.expected_persistence = None
        smoke.private_backup = None; smoke.baseline = None
        smoke.fixtures = {'quartz': 'SYNTHETIC', 'dog': 'SYNTHETIC'}
        smoke.finished = threading.Event(); captured = []
        def finish(result): captured.append(result); smoke.finished.set()
        smoke.finish = finish
        class Window:
            def evaluate_js(self, statement, callback=None):
                if callback is None: return True
                args = json.loads(statement.rsplit(')(', 1)[1][:-1])
                if args['operation'] == 'backup':
                    payload = synthetic_private_backup(smoke.report)
                    if change_payload: change_payload(payload)
                    callback(payload)
                else:
                    captured.append({'controls': args})
                    callback({'passed': False, 'failure': 'synthetic-stop-before-any-inference'})
        smoke.window = Window()
        target = private_backup_path(smoke.report, RUN_ID)
        if collision:
            target.parent.mkdir(mode=0o700)
            target.write_bytes(b'SYNTHETIC PRIOR BACKUP'); target.chmod(0o600)
        with patch('desktop.smoke.sys', types.SimpleNamespace(platform='synthetic-no-WebView')):
            smoke.run()
        return smoke, target, captured

    def test_private_backup_is_exclusive_platform_permissions_and_only_pin_enters_run(self):
        with tempfile.TemporaryDirectory() as directory:
            smoke, target, captured = self.invoke(directory)
            # Windows stat exposes DOS read/write attributes, not POSIX ACLs.
            # Match the production platform guard; do not pretend 0600 proves
            # Windows owner-only access. Exclusive creation and content proof
            # remain required on both platforms (including the collision test).
            if os.name != 'nt':
                self.assertEqual(target.stat().st_mode & 0o077, 0)
            self.assertTrue(target.is_file())
            self.assertFalse(target.is_symlink())
            raw = target.read_bytes()
            self.assertEqual(smoke.private_backup['sha256'], hashlib.sha256(raw).hexdigest())
            controls = next(r['controls'] for r in captured if 'controls' in r)
            self.assertNotIn('records', controls); self.assertNotIn('local_storage', controls)
            self.assertEqual(controls['baseline'], write_document()['baseline'])

    def test_existing_private_backup_is_not_overwritten_and_run_does_not_start(self):
        with tempfile.TemporaryDirectory() as directory:
            _, target, captured = self.invoke(directory, collision=True)
            self.assertEqual(target.read_bytes(), b'SYNTHETIC PRIOR BACKUP')
            self.assertFalse(any('controls' in r for r in captured))
            self.assertFalse(captured[-1]['passed'])

    def test_torn_payload_is_rejected_before_private_file_or_run(self):
        with tempfile.TemporaryDirectory() as directory:
            _, target, captured = self.invoke(directory, change_payload=lambda p:p.update(records=p['records'].replace('old-fixed-id','changed-id')))
            self.assertFalse(target.exists()); self.assertFalse(any('controls' in r for r in captured))
            self.assertFalse(captured[-1]['passed'])


class HostLifecycleContract(unittest.TestCase):
    def test_restart_requires_nonce_and_write_proof_before_any_runtime(self):
        for extra in ([], ['--smoke-run-id', RUN_ID], ['--smoke-write-report', '/SYNTHETIC/write.json']):
            with patch.object(run_app, 'resource_root') as root, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    run_app.main(['--smoke-report', '/SYNTHETIC/restart.json', '--smoke-phase', 'restart', *extra])
                self.assertEqual(error.exception.code, 2); root.assert_not_called()

    def invoke(self, directory, *, phase='write', expect_write=False, stop_error=False, smoke_success=True,
               platform_name=None):
        report = Path(directory) / (phase + '.json')
        calls = []
        runtime_preparations = []
        host_platform = platform_name or sys.platform
        fixed_runtime = Path(directory) / 'synthetic-fixed-WebView2'
        parent = self
        def prepare_runtime(root):
            runtime_preparations.append(root)
            return fixed_runtime
        class Lock:
            def __init__(self, path):
                pass
            def acquire(self):
                calls.append('lock-acquired')
                return self
            def close(self):
                calls.append('lock-closed')
        class Server:
            origin = 'http://127.0.0.1:8000'
            bridge = 'synthetic trusted bridge'
            def __init__(self, root, manifest):
                pass
            def start(self):
                calls.append('server-started')
                return self
            def stop(self):
                calls.append('server-stopped')
                if stop_error:
                    raise RuntimeError('synthetic close failure')
        class EventHook:
            def __iadd__(self, callback):
                return self
        window = types.SimpleNamespace(events=types.SimpleNamespace(loaded=EventHook()))
        class SyntheticSmoke:
            def __init__(self, window, report_path, fixtures, actual_phase, manifest, **kwargs):
                self.success = False
                self.kwargs = kwargs
                parent.assertEqual(actual_phase, phase)
                parent.assertEqual(kwargs['host']['pid'], os.getpid())
                parent.assertEqual(kwargs['host']['parent_pid'], os.getppid())
                parent.assertEqual(kwargs['write_report'], (Path(directory) / 'write.json').resolve() if expect_write else None)
            def run(self):
                calls.append('synthetic-smoke-callback')
                self.success = smoke_success
        def webview_start(**kwargs):
            calls.append('webview-start')
            parent.assertEqual(kwargs['gui'], 'edgechromium' if host_platform == 'win32' else None)
            if host_platform == 'win32':
                parent.assertEqual(fake_webview.settings['WEBVIEW2_RUNTIME_PATH'], str(fixed_runtime))
            started = json.loads(Path(str(report) + '.host-start.json').read_text())
            parent.assertEqual(started['schema'], 'shikuang-smoke-host-start-v1')
            parent.assertEqual(started['origin'], Server.origin)
            parent.assertEqual(started['pid'], os.getpid())
            parent.assertFalse(Path(str(report) + '.host-stop.json').exists())
            kwargs['func']()
        fake_webview = types.SimpleNamespace(settings={}, create_window=lambda *a, **kw: window, start=webview_start)
        argv = ['--smoke-report', str(report), '--smoke-fixtures', str(Path(directory) / 'no-photos-opened'), '--smoke-phase', phase]
        if expect_write:
            argv += ['--smoke-run-id', RUN_ID, '--smoke-write-report', str(Path(directory) / 'write.json')]
        # Isolate this module's platform selection, not global sys.platform:
        # changing the latter also changes pathlib's concrete Path class.
        host_sys = types.SimpleNamespace(platform=host_platform, executable=sys.executable, stderr=sys.stderr)
        with patch.dict(sys.modules, {'webview': fake_webview}), patch('desktop.smoke.Smoke', SyntheticSmoke), \
                patch.object(run_app, 'sys', host_sys), \
                patch.multiple(run_app, resource_root=lambda: Path(directory), verify_bundle=lambda root: MANIFEST,
                               InstanceLock=Lock, AssetServer=Server, require_writable=lambda path: None,
                               prepare_fixed_runtime=prepare_runtime,
                               state_directory=lambda: Path(directory), persistent_profile=lambda: Path(directory)):
            if stop_error:
                with self.assertRaisesRegex(run_app.DesktopError, 'cleanup failed'):
                    run_app.main(argv)
                code = 1
            else:
                code = run_app.main(argv)
        started = json.loads(Path(str(report) + '.host-start.json').read_text())
        stopped = json.loads(Path(str(report) + '.host-stop.json').read_text())
        self.assertEqual(calls, ['lock-acquired', 'server-started', 'webview-start',
                                 'synthetic-smoke-callback', 'server-stopped', 'lock-closed'])
        self.assertEqual(runtime_preparations, [Path(directory)] if host_platform == 'win32' else [])
        self.assertEqual(stopped['schema'], 'shikuang-smoke-host-stop-v1')
        self.assertEqual(stopped['exit_code'], code)
        self.assertEqual(stopped['server_stop_completed'], not stop_error)
        self.assertTrue(stopped['instance_lock_closed'])
        for key in ('run_id', 'phase', 'launch_id', 'pid', 'parent_pid', 'executable', 'python_entry_utc', 'server_started_utc', 'origin'):
            self.assertEqual(started[key], stopped[key])
        return started, stopped

    def test_legacy_mac_cli_keeps_working_and_receipts_bracket_real_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            self.invoke(directory, platform_name='darwin')

    def test_windows_lifecycle_injects_fixed_runtime_and_selects_edgechromium(self):
        for options in ({}, {'smoke_success': False}, {'stop_error': True},
                        {'phase': 'restart', 'expect_write': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                self.invoke(directory, platform_name='win32', **options)

    def test_write_restart_share_run_id_but_use_fresh_host_launch_id(self):
        with tempfile.TemporaryDirectory() as directory:
            first, _ = self.invoke(directory, phase='restart', expect_write=True)
            # Remove only synthetic receipts; a fresh main() must make its own identity.
            for path in Path(directory).glob('restart.json.host-*.json'):
                path.unlink()
            second, _ = self.invoke(directory, phase='restart', expect_write=True)
            self.assertEqual(first['run_id'], second['run_id'])
            self.assertNotEqual(first['launch_id'], second['launch_id'])
            self.assertEqual(first['pid'], second['pid'])  # PID reuse is not a persistence failure.

    def test_cleanup_failure_is_recorded_and_cannot_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            _, stopped = self.invoke(directory, stop_error=True)
            self.assertEqual(len(stopped['cleanup_errors']), 1)

    def test_failed_smoke_still_closes_host_with_nonzero_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            _, stopped = self.invoke(directory, smoke_success=False)
            self.assertEqual(stopped['exit_code'], 1)


class JavascriptPersistenceContract(unittest.TestCase):
    def test_actual_embedded_script_with_synthetic_storage(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node required for embedded JavaScript behavior contracts')
        result = subprocess.run([node, '--test', str(ROOT / 'tests/smoke-persistence.test.cjs')],
                                cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
