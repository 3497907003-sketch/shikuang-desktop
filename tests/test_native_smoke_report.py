"""Executable host-gate bad fixtures; no App, network, images or model loads."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_report_gate', ROOT / 'tools/verify_native_smoke_report.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
RUN = '01234567-89ab-cdef-0123-456789abcdef'
IDENTITY = {'app_version': '2.5-synthetic-fixture', 'web_commit': 'b' * 40,
            'model_manifest_sha256': 'a' * 64, 'model_version': 'inherited-2.0-audited'}

# Independent fixture schedule transcribed from the native v2 run. Windows has
# no macOS external-content-rule probe; physical isolation stays in PowerShell.
WRITE = '''exact-owned-nonce exact-smoke-source original-stable-origin secure-local-context
private-backup-proof-present all-existing-records-and-local-storage-unchanged-before-actions
write-nonce-absent-before-actions manifest-bound-models cancel-active-worker
real-quartz-recognition real-dog-rejection complete-local-reference-library full-117-photo-manifest
quartz-three-native-gallery-decodes gallery-does-not-write-specimens
smoke-write-does-not-overwrite-existing-fixture smoke-photo-fits-record-storage
existing-content-unchanged-before-owned-put smoke-record-still-present backup-retains-model-identity
existing-records-retained bundled-resources-only all-existing-content-exact-at-completion
persistence-record-present persistence-photos-populated persistence-owned-nonce'''.split()
RESTART = '''exact-owned-nonce exact-smoke-source original-stable-origin secure-local-context
private-backup-proof-present all-existing-records-and-local-storage-unchanged-before-actions
matching-restart-proof-required persistence-record-present persistence-photos-populated persistence-owned-nonce
history-survives-restart manifest-bound-models cancel-active-worker real-quartz-recognition real-dog-rejection
complete-local-reference-library full-117-photo-manifest quartz-three-native-gallery-decodes
gallery-does-not-write-specimens smoke-record-still-present backup-retains-model-identity existing-records-retained
persistence-record-present persistence-photos-populated persistence-owned-nonce owned-content-proof-before-cleanup
smoke-record-cleanup all-existing-content-restored-after-own-delete bundled-resources-only
all-existing-content-exact-at-completion'''.split()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class NativeV2HostGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='识矿 synthetic host gate ')
        self.base = Path(self.temp.name).resolve()
        self.write_path = self.base / 'write.json'
        self.restart_path = self.base / 'restart.json'
        source = gate.source_identity(IDENTITY)
        record_text = '[{"id":"old-fixed-id","notes":"KEEP","photos":["SYNTHETIC-NOT-A-PHOTO"]}]'
        local_text = '[["other","KEEP"],["shikuang-searches-v2","raw history"]]'
        search_text = '"raw history"'
        self.baseline = {'schema': 'shikuang-user-baseline-v2', 'record_count': 1,
            **{k + '_sha256': hashlib.sha256(v.encode()).hexdigest() for k, v in
               [('records', record_text), ('local_storage', local_text), ('search_raw', search_text)]}}
        self.payload = {'schema': source['contract'] + '-private-backup-v2', 'encoding': 'canonical-json-utf8-v1',
                        'run_id': RUN, 'record_id': gate.record_id(RUN), 'source_identity': source,
                        'baseline': self.baseline, 'records': record_text,
                        'local_storage': local_text, 'search_raw': search_text}
        self.backup = gate.private_backup_path(self.write_path, RUN)
        self.backup.parent.mkdir(mode=0o700)
        self.backup.write_text(encoded(self.payload), encoding='utf-8')
        self.backup.chmod(0o600)
        self.write = self.report('write')
        self.save_write()
        self.restart = self.report('restart')
        self.restart['persistence'].update(expected_write_report=gate.pin(self.write_path), cleanup_verified=True)
        self.restart_path.write_text(encoded(self.restart), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def save_write(self):
        self.write_path.write_text(encoded(self.write), encoding='utf-8')

    def report(self, phase):
        host = {'schema': 'shikuang-smoke-host-start-v1', 'run_id': RUN, 'phase': phase,
                'launch_id': ('c' if phase == 'write' else 'd') * 32,
                'pid': 100 if phase == 'write' else 101, 'parent_pid': 50,
                'origin': gate.ORIGIN, 'executable': '/SYNTHETIC/Shikuang'}
        details = {
            'original-stable-origin': gate.ORIGIN,
            'manifest-bound-models': {'version': IDENTITY['model_version'], 'labels': 98,
                'modelIds': ['mobilenet', 'efficientnet', 'efficientnet-legacy']},
            'cancel-active-worker': {'stage': '读取 输入筛查 fixture'},
            'real-quartz-recognition': {'key': 'quartz', 'engine': 'onnx-worker-wasm', 'score': .5,
                'modelVersion': IDENTITY['model_version'], 'modelCount': 3},
            'real-dog-rejection': {'message': '这不是矿物，请您放入清晰的矿物照片。'},
            'complete-local-reference-library': {'species': 6239},
            'full-117-photo-manifest': {'assets': 117, 'records': 117},
            'quartz-three-native-gallery-decodes': [
                {'id': 'gallery-image-commons-' + str(i), 'width': 224, 'height': 224,
                 'src': 'blob:' + gate.ORIGIN + '/SYNTHETIC'} for i in (10156983, 10122617, 10166671)],
            'gallery-does-not-write-specimens': {'before': 1, 'after': 1},
            'smoke-photo-fits-record-storage': {'characters': 100},
            'backup-retains-model-identity': {'count': 1},
            'existing-records-retained': {'existingCount': 1, 'fullContentVerified': True},
        }
        snapshot = {'schema': 'shikuang-persistence-snapshot-v2', 'record_id': gate.record_id(RUN),
                    'sample_id': 'desktop-smoke-' + RUN, 'photo_count': 1,
                    'record_sha256': 'e' * 64, 'photos_sha256': 'f' * 64, 'baseline': copy.deepcopy(self.baseline)}
        return {'passed': True, 'errors': [], 'phase': phase, 'run_id': RUN, 'host': host, **IDENTITY,
                'smoke_source_identity': gate.source_identity(IDENTITY), 'origin': gate.ORIGIN,
                'secureContext': True, 'online': False, 'physicalNetworkDisabled': False, 'accuracy_claim': False,
                'platform': 'SYNTHETIC-CONTRACT-FIXTURE-NOT-WINDOWS-ACCEPTANCE',
                'externalHttpRuleInstalled': False, 'galleryFrameSynchronized': True,
                'checks': [{'name': n, 'passed': True, 'details': copy.deepcopy(details.get(n, {}))}
                           for n in (WRITE if phase == 'write' else RESTART)],
                'quartz': {'key': 'quartz', 'engine': 'onnx-worker-wasm', 'score': .5, 'modelCount': 3,
                    'modelVersion': IDENTITY['model_version'], 'manifestSha256': IDENTITY['model_manifest_sha256']},
                'resources': [{'name': gate.ORIGIN + '/model-manifest.json'}],
                'private_backup': gate.pin(self.backup), 'baseline': copy.deepcopy(self.baseline),
                'persistence': {'mode': 'write-content-snapshot' if phase == 'write' else 'full-content-sha256-before-any-put',
                    'verified_before_any_put': phase == 'restart', 'snapshot': snapshot}}

    def validate(self, report=None, phase='restart'):
        report = report if report is not None else self.restart
        return gate.validate_content(report, phase, report['host'], IDENTITY, RUN,
                                     self.write_path if phase == 'write' else self.restart_path,
                                     self.write_path if phase == 'restart' else None)

    def test_exact_v2_both_phases_with_populated_prior_data_and_known_repeated_triplet(self):
        self.assertEqual(self.validate(self.write, 'write')['check_count'], 26)
        self.assertEqual(self.validate()['check_count'], 30)
        self.assertEqual(self.validate()['baseline_record_count'], 1)
        self.assertEqual(RESTART.count('persistence-record-present'), 2)

    def test_v1_fixed_id_wrong_nonce_or_sample_no_longer_accepted(self):
        for key, value in [('schema', 'shikuang-persistence-snapshot-v1'),
                           ('record_id', 'desktop-runtime-smoke-record'), ('sample_id', 'other'),
                           ('photo_count', True), ('photo_count', 0)]:
            report = copy.deepcopy(self.restart);report['persistence']['snapshot'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.validate(report)

    def test_missing_extra_duplicate_or_reordered_checks_fail(self):
        cases = []
        a = copy.deepcopy(self.restart);a['checks'].pop(7);cases.append(a)
        a = copy.deepcopy(self.restart);a['checks'].append(a['checks'][7]);cases.append(a)
        a = copy.deepcopy(self.restart);a['checks'].append(a['checks'][0]);cases.append(a)
        a = copy.deepcopy(self.restart);a['checks'][7:10] = reversed(a['checks'][7:10]);cases.append(a)
        a = copy.deepcopy(self.restart);a['checks'][0]['passed'] = False;cases.append(a)
        a = copy.deepcopy(self.restart);a['checks'][11]['details'] = None;cases.append(a)
        for report in cases:
            with self.subTest(names=[r['name'] for r in report['checks']]), self.assertRaises(ValueError): self.validate(report)

    def test_changed_photo_record_digest_or_missing_cleanup_rejects_before_admission(self):
        for field in ('photos_sha256', 'record_sha256'):
            report = copy.deepcopy(self.restart);report['persistence']['snapshot'][field] = '0' * 64
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate(report)
        for field in ('verified_before_any_put', 'cleanup_verified'):
            report = copy.deepcopy(self.restart);report['persistence'][field] = False
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate(report)

    def test_original_full_baseline_and_private_backup_bytes_are_required(self):
        report = copy.deepcopy(self.restart);report['baseline']['records_sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.validate(report)
        raw = self.backup.read_bytes();self.backup.write_bytes(raw + b' ')
        with self.assertRaisesRegex(ValueError, 'bytes/SHA'): self.validate()
        self.backup.write_bytes(raw)
        payload = copy.deepcopy(self.payload);payload['records'] = payload['records'].replace('KEEP', 'CHANGED')
        self.backup.write_text(encoded(payload), encoding='utf-8')
        report = copy.deepcopy(self.restart);report['private_backup'] = gate.pin(self.backup)
        with self.assertRaises(ValueError): self.validate(report)

    def test_backup_cannot_point_outside_own_nonce_location(self):
        report = copy.deepcopy(self.restart);report['private_backup']['path'] = str(self.base / 'other.json')
        with self.assertRaises(ValueError): self.validate(report)

    def test_source_wrong_host_launch_and_relabelled_prior_success_fail(self):
        report = copy.deepcopy(self.restart);report['smoke_source_identity']['script_sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.validate(report)
        report = copy.deepcopy(self.restart);report['host']['launch_id'] = self.write['host']['launch_id']
        with self.assertRaises(ValueError): self.validate(report)
        self.write['checks'][0]['passed'] = False;self.save_write()
        report = copy.deepcopy(self.restart);report['persistence']['expected_write_report'] = gate.pin(self.write_path)
        # A successful-looking prior write is not enough if any original gate failed.
        with self.assertRaises(ValueError): self.validate(report)

    def test_prior_write_byte_identity_stays_bound_after_host_network_annotation(self):
        self.write['physicalNetworkDisabled'] = True;self.save_write()
        self.restart['persistence']['expected_write_report'] = gate.pin(self.write_path)
        self.assertEqual(self.validate()['phase'], 'restart')
        self.write_path.write_bytes(self.write_path.read_bytes() + b' ')
        with self.assertRaises(ValueError): self.validate()

    def test_model_gallery_resource_and_cancellation_details_cannot_be_forged(self):
        for name, details in [('manifest-bound-models', {}), ('cancel-active-worker', {'stage': 'deadline'}),
                              ('real-dog-rejection', {'message': 'wrong'}),
                              ('quartz-three-native-gallery-decodes', [])]:
            report = copy.deepcopy(self.restart)
            next(r for r in report['checks'] if r['name'] == name)['details'] = details
            with self.subTest(name=name), self.assertRaises(ValueError): self.validate(report)
        report = copy.deepcopy(self.restart);report['resources'].append({'name': 'https://external.example/model'})
        with self.assertRaises(ValueError): self.validate(report)
        report = copy.deepcopy(self.restart);report['quartz']['score'] = float('nan')
        with self.assertRaises(ValueError): self.validate(report)

    def test_ascii_stdio_Chinese_paths_cli_rejects_bad_report_and_returns_only_pins(self):
        identity = self.base / 'identity.json';identity.write_text(encoded(IDENTITY), encoding='utf-8')
        host = self.base / 'start.json';host.write_text(encoded(self.write['host']), encoding='utf-8')
        argv = [sys.executable, '-B', str(ROOT / 'tools/verify_native_smoke_report.py'),
                '--report', str(self.write_path), '--phase', 'write', '--host-start', str(host),
                '--identity', str(identity), '--run-id', RUN]
        # Exercise real CLI stdio, not a mocked print. This used to fail on the
        # Chinese temporary path with UnicodeEncodeError before emitting JSON.
        environment = dict(os.environ, PYTHONIOENCODING='ascii', PYTHONUTF8='0')
        valid = subprocess.run(argv, text=True, encoding='utf-8', capture_output=True,
                               env=environment, timeout=20)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertTrue(valid.stdout.isascii())
        self.assertTrue(any(ord(c) > 127 for c in str(self.base)))
        receipt = json.loads(valid.stdout)
        self.assertEqual(receipt['report'], gate.pin(self.write_path))
        self.assertNotIn('records', receipt);self.assertNotIn('local_storage', receipt)
        for key in ('online', 'physicalNetworkDisabled', 'externalHttpRuleInstalled'):
            report = copy.deepcopy(self.write);report[key] = True
            self.write_path.write_text(encoded(report), encoding='utf-8')
            failed = subprocess.run(argv, text=True, encoding='utf-8', capture_output=True,
                                    env=environment, timeout=20)
            self.assertNotEqual(failed.returncode, 0)
            self.assertTrue(failed.stderr.isascii())
            self.assertNotIn('SYNTHETIC-NOT-A-PHOTO', failed.stderr)
        report = copy.deepcopy(self.write)
        secret = 'PRIVATE-PAYLOAD-MUST-NOT-APPEAR-照片'
        report['persistence']['snapshot']['photos_sha256'] = secret
        self.write_path.write_text(encoded(report), encoding='utf-8')
        failed = subprocess.run(argv, text=True, encoding='utf-8', capture_output=True,
                                env=environment, timeout=20)
        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue(failed.stderr.isascii())
        self.assertNotIn('PRIVATE-PAYLOAD-MUST-NOT-APPEAR', failed.stderr)


if __name__ == '__main__':
    unittest.main()
