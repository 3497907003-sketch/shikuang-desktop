"""Delivery guard fixtures are metadata/bytes, not model training samples."""
import copy
import hashlib
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

# These identities describe temporary synthetic fixtures only. Tests never read
# a real release, staged model, production resource seal, or current build pin.
SYNTHETIC_SOURCE = 'a' * 40
SYNTHETIC_WEB_SOURCE = 'b' * 40
SYNTHETIC_VERSION = '2.5.0-preview.999'
SYNTHETIC_TAG = 'v' + SYNTHETIC_VERSION
SYNTHETIC_IDENTITY = {
    'schema_version': 1, 'app_version': SYNTHETIC_VERSION,
    'web_commit': SYNTHETIC_WEB_SOURCE, 'model_version': 'synthetic-test-model',
    'model_manifest_sha256': '2' * 64, 'web_build_inputs': {},
}


def json_bytes(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode('utf-8')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def evidence():
    return {
        'schema_version': 1, 'repository': d.REPOSITORY, 'run_id': 42,
        'run_attempt': 2, 'source_commit': SYNTHETIC_SOURCE,
        'web_commit': SYNTHETIC_WEB_SOURCE, 'app_version': SYNTHETIC_VERSION,
        'tag': SYNTHETIC_TAG,
        'artifact': {'id': 12, 'size_in_bytes': 100, 'digest': 'sha256:' + '1' * 64},
        'asset_index_sha256': '3' * 64,
    }


def runtime(phase='write'):
    identity = copy.deepcopy(SYNTHETIC_IDENTITY)
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
    names = d.v2.native.expected_names(phase, False)
    return {'passed': True, 'errors': [], 'phase': phase, 'web_commit': identity['web_commit'],
            'model_manifest_sha256': identity['model_manifest_sha256'], 'app_version': identity['app_version'],
            'architecture': 'AMD64', 'platform': 'Windows-10-10.0.20348-SP0',
            'physicalNetworkDisabled': True, 'online': False, 'accuracy_claim': False,
            'origin': d.ORIGIN, 'secureContext': True, 'galleryFrameSynchronized': True,
            'quartz': {'key': 'quartz', 'engine': 'onnx-worker-wasm', 'modelVersion': identity['model_version'],
                       'manifestSha256': identity['model_manifest_sha256'], 'modelCount': 3, 'score': .5},
            'resources': [{'name': d.ORIGIN + '/model-manifest.json'}],
            'externalHttpRuleInstalled': False,
            'checks': [{'name': name, 'passed': True, 'details': details.get(name, {})} for name in names]}


class DeliveryGuards(unittest.TestCase):
    def setUp(self):
        self.evidence = evidence()
        self.run = {'id': 42, 'repository': {'full_name': d.REPOSITORY},
                    'run_attempt': 2,
                    'head_repository': {'full_name': d.REPOSITORY}, 'head_sha': SYNTHETIC_SOURCE,
                    'path': '.github/workflows/desktop.yml', 'status': 'completed',
                    'conclusion': 'success', 'event': 'push'}
        self.release = {'id': 1, 'tag_name': SYNTHETIC_TAG, 'target_commitish': SYNTHETIC_SOURCE,
                        'draft': True, 'prerelease': True, 'assets': [{'name': 'mac.zip', 'id': 99}]}
        self.identity = copy.deepcopy(SYNTHETIC_IDENTITY)

    def test_actual_source_guards_reject_success_from_another_head_or_workflow(self):
        d.validate_run(self.run, self.evidence)
        d.validate_run(dict(self.run, event='workflow_dispatch'), self.evidence)
        for field, value in [('head_sha', '0' * 40), ('path', '.github/workflows/fake.yml'),
                             ('conclusion', 'failure'), ('event', 'pull_request'),
                             ('id', 43), ('run_attempt', 1), ('id', '42'),
                             ('run_attempt', '2'), ('status', 'in_progress')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.validate_run(dict(self.run, **{field: value}), self.evidence)
        for field in ('repository', 'head_repository'):
            fork = copy.deepcopy(self.run)
            fork[field]['full_name'] = 'unknown/fork'
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.validate_run(fork, self.evidence)

    def test_artifact_unique_unexpired_and_same_run(self):
        a = {'name': d.ARTIFACT_NAME, 'id': 12, 'expired': False, 'size_in_bytes': 100,
             'digest': 'sha256:' + '1' * 64, 'workflow_run': {'id': 42, 'head_sha': SYNTHETIC_SOURCE}}
        self.assertEqual(d.select_artifact([a], self.evidence), a)
        for rows in ([], [a, a], [dict(a, expired=True)], [dict(a, digest='')],
                     [dict(a, id=13)], [dict(a, size_in_bytes=101)],
                     [dict(a, id='12')], [dict(a, size_in_bytes=100.0)],
                     [dict(a, digest='sha256:' + '4' * 64)],
                     [dict(a, workflow_run={'id': 43, 'head_sha': SYNTHETIC_SOURCE})],
                     [dict(a, workflow_run={'id': 42, 'head_sha': 'c' * 40})]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                d.select_artifact(rows, self.evidence)

    def test_public_wrong_target_or_non_preview_release_is_never_modified(self):
        d.validate_release(self.release, SYNTHETIC_TAG, SYNTHETIC_SOURCE)
        for field, value in [('draft', False), ('prerelease', False), ('target_commitish', 'main')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                d.validate_release(dict(self.release, **{field: value}), SYNTHETIC_TAG, SYNTHETIC_SOURCE)

    def test_draft_lookup_enumerates_releases_not_by_tag_endpoint(self):
        with patch.object(d, 'api', return_value=[self.release]) as api:
            self.assertEqual(d.matching_release(d.REPOSITORY, SYNTHETIC_TAG)['id'], 1)
            self.assertIn('releases?per_page=100', api.call_args.args[0])

    def test_annotated_tag_dereferences_to_tested_commit(self):
        with patch.object(d, 'api', side_effect=[{'object': {'type': 'tag', 'sha': 'a' * 40}},
                                               {'object': {'type': 'commit', 'sha': SYNTHETIC_SOURCE}}]):
            d.validate_tag(d.REPOSITORY, SYNTHETIC_TAG, SYNTHETIC_SOURCE)
        with patch.object(d, 'api', return_value={'object': {'type': 'commit', 'sha': '0' * 40}}):
            with self.assertRaises(ValueError):
                d.validate_tag(d.REPOSITORY, SYNTHETIC_TAG, SYNTHETIC_SOURCE)

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


class ReviewedEvidenceFixtures(unittest.TestCase):
    def checked(self, value):
        raw = json_bytes(value)
        return d.checked_evidence(raw, sha256(raw))

    def test_complete_synthetic_evidence_requires_its_exact_byte_digest(self):
        value = evidence()
        raw = json_bytes(value)
        self.assertEqual(d.checked_evidence(raw, sha256(raw)), value)
        for body, expected in ((raw, '0' * 64), (raw + b' ', sha256(raw)),
                               (raw, ''), (b'', sha256(b'')),
                               (b' ' * 65537, sha256(b' ' * 65537))):
            with self.subTest(size=len(body), expected=expected), self.assertRaises(ValueError):
                d.checked_evidence(body, expected)

    def test_evidence_root_and_artifact_fields_are_closed(self):
        original = evidence()
        for field in original:
            missing = copy.deepcopy(original)
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.checked(missing)
        for value in (dict(original, unreviewed=True),
                      dict(original, artifact=dict(original['artifact'], run_id=42)),
                      dict(original, artifact={'id': 12, 'digest': 'sha256:' + '1' * 64})):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.checked(value)
        raw = json_bytes(original).rstrip()[:-1] + b', "run_id": 42}\n'
        with self.assertRaises(ValueError):
            d.checked_evidence(raw, sha256(raw))
        raw = json_bytes(original).replace(b'"id": 12', b'"id": 12, "id": 12')
        with self.assertRaises(ValueError):
            d.checked_evidence(raw, sha256(raw))

    def test_evidence_numbers_are_positive_integers_not_booleans_or_strings(self):
        for field in ('run_id', 'run_attempt'):
            for value in (True, False, '42', 42.0, 0, -1):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.checked(dict(evidence(), **{field: value}))
        for field in ('id', 'size_in_bytes'):
            for value in (True, False, '12', 12.0, 0, -1):
                changed = evidence()
                changed['artifact'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.checked(changed)
        for value in (dict(evidence(), schema_version=True),
                      dict(evidence(), artifact=dict(evidence()['artifact'], size_in_bytes=d.MAX_FILE))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.checked(value)

    def test_candidate_repository_commits_tag_and_asset_index_are_immutable(self):
        for field, value in (('repository', 'unknown/fork'), ('source_commit', 'main'),
                             ('web_commit', 'b' * 39), ('app_version', '../../package'),
                             ('tag', 'v2.5.0-preview.998'), ('asset_index_sha256', 'short')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.checked(dict(evidence(), **{field: value}))
        changed = evidence()
        changed['artifact']['digest'] = '1' * 64
        with self.assertRaises(ValueError):
            self.checked(changed)

    def test_context_cannot_swap_the_approved_attempt_or_artifact(self):
        candidate = evidence()
        raw = json_bytes(candidate)
        run = {'id': 42, 'run_attempt': 2,
               'repository': {'full_name': d.REPOSITORY},
               'head_repository': {'full_name': d.REPOSITORY},
               'head_sha': SYNTHETIC_SOURCE, 'path': '.github/workflows/desktop.yml',
               'status': 'completed', 'conclusion': 'success', 'event': 'workflow_dispatch'}
        artifact = {'name': d.ARTIFACT_NAME, 'expired': False,
                    **candidate['artifact'],
                    'workflow_run': {'id': 42, 'head_sha': SYNTHETIC_SOURCE}}
        context = {'schema_version': 2, 'evidence_json': raw.decode('utf-8'),
                   'run': run, 'artifact': artifact}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-context.json'
            path.write_bytes(json_bytes(context))
            self.assertEqual(d.load_context(path, sha256(raw))['run_attempt'], 2)
            changed = copy.deepcopy(context)
            changed['run']['run_attempt'] = 3
            path.write_bytes(json_bytes(changed))
            with self.assertRaises(ValueError):
                d.load_context(path, sha256(raw))
            changed = copy.deepcopy(context)
            changed['artifact']['digest'] = 'sha256:' + '9' * 64
            path.write_bytes(json_bytes(changed))
            with self.assertRaises(ValueError):
                d.load_context(path, sha256(raw))
            path.write_bytes(json_bytes(context))
            with self.assertRaises(ValueError):
                d.load_context(path, '0' * 64)


class CompleteWebAssetFixtures(unittest.TestCase):
    PREFIX = 'Shikuang/_internal/web/'

    def synthetic_build(self, count=168):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        dist = Path(temporary.name) / 'dist'
        payload = {f'assets/synthetic-{i:03}.txt': f'fixture-{i:03}'.encode('ascii')
                   for i in range(count)}
        for name, body in payload.items():
            path = dist / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        files = {name: {'bytes': len(body), 'sha256': sha256(body)}
                 for name, body in payload.items()}
        seal = json_bytes({**copy.deepcopy(SYNTHETIC_IDENTITY), 'files': files})
        return dist, payload, seal

    def verify(self, dist, payload, seal, approved_sha=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(self.PREFIX + '.web-assets.json', seal)
            for name, body in payload.items():
                archive.writestr(self.PREFIX + name, body)
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            d.inspect_zip(archive)
            return d.verify_web_assets(archive, self.PREFIX, dist,
                                       SYNTHETIC_IDENTITY, approved_sha or sha256(seal))

    def test_complete_157_and_168_file_builds_use_exact_index_not_fixed_count(self):
        for count in (157, 168):
            with self.subTest(count=count):
                dist, payload, seal = self.synthetic_build(count)
                self.assertEqual(len(self.verify(dist, payload, seal)), count)

    def test_missing_extra_same_size_mutation_and_larger_payload_are_rejected(self):
        dist, payload, seal = self.synthetic_build()
        first = sorted(payload)[0]
        missing = dict(payload)
        del missing[first]
        extra = dict(payload, **{'assets/unreviewed.txt': b'extra'})
        same_size = dict(payload, **{first: b'x' * len(payload[first])})
        larger = dict(payload, **{first: payload[first] + b'extra'})
        for changed in (missing, extra, same_size, larger):
            with self.subTest(paths=len(changed), first=changed.get(first)), self.assertRaises(ValueError):
                self.verify(dist, changed, seal)

    def test_forged_index_sha_size_file_set_or_source_identity_is_rejected(self):
        dist, payload, seal = self.synthetic_build()
        first = sorted(payload)[0]
        for change in ('sha', 'bytes', 'missing', 'extra', 'source'):
            value = json.loads(seal)
            if change == 'sha':
                value['files'][first]['sha256'] = '0' * 64
            elif change == 'bytes':
                value['files'][first]['bytes'] += 1
            elif change == 'missing':
                del value['files'][first]
            elif change == 'extra':
                value['files']['assets/unreviewed.txt'] = {'bytes': 1, 'sha256': '0' * 64}
            else:
                value['web_commit'] = 'c' * 40
            forged = json_bytes(value)
            # The forged index has a matching own hash, but must still fail the
            # independent immutable-dist and identity comparisons.
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.verify(dist, payload, forged)

    def test_reviewed_asset_index_hash_and_canonical_build_bytes_are_bound(self):
        dist, payload, seal = self.synthetic_build()
        with self.assertRaises(ValueError):
            self.verify(dist, payload, seal, '0' * 64)
        # Equivalent JSON with different bytes cannot replace the reviewed seal.
        with self.assertRaises(ValueError):
            self.verify(dist, payload, seal + b' ', sha256(seal))
        first = sorted(payload)[0]
        (dist / first).write_bytes(b'x' * len(payload[first]))
        with self.assertRaises(ValueError):
            self.verify(dist, payload, seal)


if __name__ == '__main__':
    unittest.main()
