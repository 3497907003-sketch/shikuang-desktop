"""Delivery guard fixtures are metadata/bytes, not model training samples."""
import copy
import importlib.util
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('delivery', ROOT / 'tools/verify_windows_delivery.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


def runtime(phase='write'):
    identity = json.loads((ROOT / 'web-source.json').read_text())
    details = {
        'original-stable-origin': d.ORIGIN,
        'manifest-bound-models': {'version': identity['model_version'], 'labels': 98,
                                 'modelIds': ['mobilenet', 'efficientnet', 'efficientnet-legacy']},
        'real-quartz-recognition': {'key': 'quartz'},
        'real-dog-rejection': {'message': '这不是矿物，请您放入清晰的矿物照片。'},
        'complete-local-reference-library': {'species': 6239},
        'full-117-photo-manifest': {'assets': 117, 'records': 117},
        'quartz-three-native-gallery-decodes': [
            {'id': 'gallery-image-commons-' + str(i), 'width': 224, 'height': 224,
             'src': 'blob:' + d.ORIGIN + '/fixture'} for i in (10156983, 10122617, 10166671)],
        'gallery-does-not-write-specimens': {'before': 1, 'after': 1},
        'backup-retains-model-identity': {'count': 1},
    }
    names = d.CHECKS | ({'history-survives-restart', 'smoke-record-cleanup'} if phase == 'restart' else set())
    return {'passed': True, 'errors': [], 'phase': phase, 'web_commit': d.WEB_SOURCE,
            'model_manifest_sha256': identity['model_manifest_sha256'], 'app_version': identity['app_version'],
            'architecture': 'AMD64', 'platform': 'Windows-10-10.0.20348-SP0',
            'physicalNetworkDisabled': True, 'online': False, 'accuracy_claim': False,
            'origin': d.ORIGIN, 'secureContext': True, 'galleryFrameSynchronized': True,
            'quartz': {'key': 'quartz', 'engine': 'onnx-worker-wasm', 'modelVersion': identity['model_version'],
                       'manifestSha256': identity['model_manifest_sha256'], 'modelCount': 3, 'score': .5},
            'resources': [{'name': d.ORIGIN + '/model-manifest.json'}],
            'checks': [{'name': name, 'passed': True, 'details': details.get(name, {})} for name in sorted(names)]}


class DeliveryGuards(unittest.TestCase):
    def setUp(self):
        self.run = {'id': 42, 'repository': {'full_name': d.REPOSITORY},
                    'head_repository': {'full_name': d.REPOSITORY}, 'head_sha': d.TESTED_SOURCE,
                    'path': '.github/workflows/desktop.yml', 'status': 'completed',
                    'conclusion': 'success', 'event': 'push'}
        self.release = {'id': 1, 'tag_name': d.TAG, 'target_commitish': d.TESTED_SOURCE,
                        'draft': True, 'prerelease': True, 'assets': [{'name': 'mac.zip', 'id': 99}]}
        self.identity = json.loads((ROOT / 'web-source.json').read_text())

    def test_actual_source_guards_reject_success_from_another_head_or_workflow(self):
        d.validate_run(self.run, d.REPOSITORY, d.TESTED_SOURCE)
        for field, value in [('head_sha', '0' * 40), ('path', '.github/workflows/fake.yml'),
                             ('conclusion', 'failure'), ('event', 'pull_request')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.validate_run(dict(self.run, **{field: value}), d.REPOSITORY, d.TESTED_SOURCE)
        fork = copy.deepcopy(self.run)
        fork['head_repository']['full_name'] = 'unknown/fork'
        with self.assertRaises(ValueError):
            d.validate_run(fork, d.REPOSITORY, d.TESTED_SOURCE)

    def test_artifact_unique_unexpired_and_same_run(self):
        a = {'name': d.ARTIFACT_NAME, 'id': 12, 'expired': False, 'size_in_bytes': 100,
             'digest': 'sha256:' + '1' * 64, 'workflow_run': {'id': 42, 'head_sha': d.TESTED_SOURCE}}
        self.assertEqual(d.select_artifact([a], 42, d.TESTED_SOURCE), a)
        for rows in ([a, a], [dict(a, expired=True)], [dict(a, digest='')],
                     [dict(a, workflow_run={'id': 43, 'head_sha': d.TESTED_SOURCE})]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                d.select_artifact(rows, 42, d.TESTED_SOURCE)

    def test_public_wrong_target_or_non_preview_release_is_never_modified(self):
        d.validate_release(self.release, d.TAG, d.TESTED_SOURCE)
        for field, value in [('draft', False), ('prerelease', False), ('target_commitish', 'main')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.validate_release(dict(self.release, **{field: value}), d.TAG, d.TESTED_SOURCE)

    def test_draft_lookup_enumerates_releases_not_by_tag_endpoint(self):
        with patch.object(d, 'api', return_value=[self.release]) as api:
            self.assertEqual(d.matching_release(d.REPOSITORY, d.TAG)['id'], 1)
            self.assertIn('releases?per_page=100', api.call_args.args[0])

    def test_annotated_tag_dereferences_to_tested_commit(self):
        with patch.object(d, 'api', side_effect=[{'object': {'type': 'tag', 'sha': 'a' * 40}},
                                               {'object': {'type': 'commit', 'sha': d.TESTED_SOURCE}}]):
            d.validate_tag(d.REPOSITORY, d.TAG, d.TESTED_SOURCE)
        with patch.object(d, 'api', return_value={'object': {'type': 'commit', 'sha': '0' * 40}}):
            with self.assertRaises(ValueError):
                d.validate_tag(d.REPOSITORY, d.TAG, d.TESTED_SOURCE)

    def test_runtime_both_phases_require_actual_offline_history_and_model_gates(self):
        for phase in ('write', 'restart'):
            self.assertTrue(d.validate_runtime(runtime(phase), phase, self.identity)['passed'])
        r = runtime('restart')
        r['checks'] = [c for c in r['checks'] if c['name'] != 'history-survives-restart']
        with self.assertRaises(ValueError):
            d.validate_runtime(r, 'restart', self.identity)

    def test_failed_missing_or_duplicate_gate_never_passes(self):
        for change in ('failed', 'missing', 'duplicate'):
            r = runtime()
            if change == 'failed':
                r['checks'][0]['passed'] = False
            elif change == 'missing':
                r['checks'] = r['checks'][1:]
            else:
                r['checks'].append(r['checks'][0])
            with self.subTest(change=change), self.assertRaises(ValueError):
                d.validate_runtime(r, 'write', self.identity)

    def test_runtime_offline_cannot_be_replaced_by_rule_or_claim(self):
        for field, value in [('physicalNetworkDisabled', False), ('online', True), ('accuracy_claim', True),
                             ('web_commit', '0' * 40), ('model_manifest_sha256', '0' * 64)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.validate_runtime(dict(runtime(), **{field: value}), 'write', self.identity)

    def test_runtime_external_resource_invalid_score_or_placeholder_image_rejected(self):
        r = runtime()
        r['resources'].append({'name': 'https://cdn.example.test/model.onnx'})
        with self.assertRaises(ValueError):
            d.validate_runtime(r, 'write', self.identity)
        r = runtime()
        r['quartz']['score'] = float('nan')
        with self.assertRaises(ValueError):
            d.validate_runtime(r, 'write', self.identity)
        r = runtime()
        next(c for c in r['checks'] if c['name'] == 'quartz-three-native-gallery-decodes')['details'][0]['width'] = 0
        with self.assertRaises(ValueError):
            d.validate_runtime(r, 'write', self.identity)

    def test_network_requires_both_adapter_and_route_zero(self):
        p = {'physical_network_disabled': True, 'active_adapter_count': 0, 'default_route_count': 0,
             'disabled_adapter_names': ['Ethernet'], 'platform': 'Microsoft Windows NT 10.0.20348.0'}
        d.validate_network(p)
        for key in ('active_adapter_count', 'default_route_count'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                d.validate_network(dict(p, **{key: 1}))

    def test_zip_rejects_escape_symlink_and_case_collision(self):
        for rows in ([('../escape', b'x')], [('C:/x', b'x')], [('a\\b', b'x')], [('safe\x00hidden', b'x')],
                     [('Same', b'x'), ('same', b'y')]):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                for name, data in rows:
                    # ZipInfo's constructor replaces the host path separator.
                    # On Windows, passing a backslash name to writestr(str)
                    # would silently turn the hostile fixture into a safe path.
                    # Preserve the actual archive filename on every test host.
                    entry = zipfile.ZipInfo('fixture')
                    entry.filename = entry.orig_filename = name
                    archive.writestr(entry, data)
            stream.seek(0)
            with self.subTest(paths=[name for name, _ in rows]), zipfile.ZipFile(stream) as archive:
                self.assertEqual([entry.orig_filename for entry in archive.infolist()],
                                 [name for name, _ in rows])
                with self.assertRaises(ValueError):
                    d.inspect_zip(archive)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            link = zipfile.ZipInfo('link')
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, 'target')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive, self.assertRaises(ValueError):
            d.inspect_zip(archive)

    def test_seal_detects_mutated_or_unlisted_runtime_bytes(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('vendor/runtime.exe', b'fixture')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            real = d.stream_identity(archive, 'vendor/runtime.exe')
            self.assertEqual(d.verify_seal(archive, 'vendor/', {'runtime.exe': real}), 7)
            with self.assertRaises(ValueError):
                d.verify_seal(archive, 'vendor/', {'runtime.exe': dict(real, sha256='0' * 64)})
            with self.assertRaises(ValueError):
                d.verify_seal(archive, 'vendor/', {'other.exe': real})

    def test_builder_checksum_is_exact_and_not_outer_artifact_digest(self):
        self.assertEqual(d.checksum_from_text('Algorithm : SHA256\nHash : ' + 'A' * 64 + '\n'), 'a' * 64)
        for text in ('sha256:' + 'a' * 64, 'Hash : short', 'Hash : ' + 'a' * 64 + '\nHash : ' + 'b' * 64):
            with self.subTest(text=text), self.assertRaises(ValueError):
                d.checksum_from_text(text)


if __name__ == '__main__':
    unittest.main()
