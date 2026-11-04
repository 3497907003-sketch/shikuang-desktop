"""Synthetic provenance fixtures; no build, network, model or user-data reads."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('delivery_lifecycle', ROOT / 'tools/verify_windows_delivery.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


def fixture():
    evidence = {'schema_version': 1, 'repository': d.REPOSITORY, 'run_id': 42, 'run_attempt': 2,
                'source_commit': 'a' * 40, 'web_commit': 'b' * 40, 'app_version': '2.5.0-rc.1',
                'tag': 'v2.5.0-rc.1', 'asset_index_sha256': 'c' * 64,
                'artifact': {'id': 99, 'size_in_bytes': 100, 'digest': 'sha256:' + 'd' * 64}}
    run = {'id': 42, 'run_attempt': 2, 'head_sha': evidence['source_commit'],
           'repository': {'full_name': d.REPOSITORY}, 'head_repository': {'full_name': d.REPOSITORY},
           'path': '.github/workflows/desktop.yml', 'status': 'completed', 'conclusion': 'success',
           'event': 'workflow_dispatch'}
    artifact = {**evidence['artifact'], 'name': d.ARTIFACT_NAME, 'expired': False,
                'workflow_run': {'id': 42, 'head_sha': evidence['source_commit']}}
    raw = json.dumps(evidence, indent=2) + '\n'
    context = {'schema_version': 2, 'evidence_json': raw, 'run': run, 'artifact': artifact}
    return evidence, context, hashlib.sha256(raw.encode()).hexdigest()


class EvidenceLifecycle(unittest.TestCase):
    def test_guarded_context_retains_exact_reviewed_identity_and_revalidates_live_artifact(self):
        evidence, context, digest = fixture()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'context.json'
            path.write_text(json.dumps(context))
            actual = d.load_context(path, digest)
            self.assertEqual(actual['run_attempt'], 2)
            self.assertEqual(actual['artifact'], evidence['artifact'])
            self.assertEqual(set(actual['artifact']), {'id', 'size_in_bytes', 'digest'})
            api_artifact = {**context['artifact'], 'node_id': 'SYNTHETIC_NODE',
                            'created_at': '2026-11-04T00:00:00Z', 'updated_at': '2026-11-04T00:00:00Z',
                            'archive_download_url': 'https://api.github.com/repos/' + d.REPOSITORY + '/actions/artifacts/99/zip'}
            self.assertEqual(d.select_artifact([api_artifact], actual), api_artifact)
            self.assertEqual(d.package_name(actual), 'Shikuang-2.5.0-rc.1-windows-x64.zip')
            for change in ('evidence', 'run_attempt', 'artifact'):
                mixed = copy.deepcopy(context)
                if change == 'evidence':
                    mixed['evidence_json'] += ' '
                elif change == 'run_attempt':
                    mixed['run']['run_attempt'] = 3
                else:
                    mixed['artifact']['id'] = 100
                path.write_text(json.dumps(mixed))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    d.load_context(path, digest)

    def test_build_provenance_cannot_mix_attempt_source_version_or_asset_index(self):
        evidence, _, _ = fixture()
        proof = {key: evidence[key] for key in ('schema_version', 'repository', 'run_id',
                 'run_attempt', 'source_commit', 'web_commit', 'app_version', 'asset_index_sha256')}
        d.validate_build_provenance(proof, evidence)
        for key, value in [('schema_version', True), ('run_id', 43), ('run_attempt', 1),
                           ('source_commit', '0' * 40), ('web_commit', '0' * 40),
                           ('app_version', '2.5.0'), ('asset_index_sha256', '0' * 64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                d.validate_build_provenance(dict(proof, **{key: value}), evidence)

    def test_record_build_requires_real_ci_identity_and_never_overwrites_proof(self):
        evidence, _, _ = fixture()
        identity = {'schema_version': 1, 'web_commit': evidence['web_commit'],
                    'app_version': evidence['app_version'], 'model_version': 'synthetic-fixture',
                    'model_manifest_sha256': 'e' * 64}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'web').mkdir()
            (root / 'web-source.json').write_text(json.dumps(identity))
            index = root / 'web/.web-assets.json'
            index.write_text(json.dumps({**identity, 'files': {'fixture.txt': {
                'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}}}))
            args = SimpleNamespace(source_root=root, output=root / 'proof.json')
            env = {'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2',
                   'GITHUB_SHA': evidence['source_commit'], 'GITHUB_REPOSITORY': d.REPOSITORY}
            with patch.dict(os.environ, env, clear=True), patch.object(d.subprocess, 'check_output',
                    return_value=evidence['source_commit'] + '\n'):
                d.record_build(args)
                actual = json.loads(args.output.read_text())
                self.assertEqual(actual['run_attempt'], 2)
                self.assertEqual(actual['asset_index_sha256'], hashlib.sha256(index.read_bytes()).hexdigest())
                with self.assertRaises(FileExistsError):
                    d.record_build(args)
            with patch.dict(os.environ, {**env, 'GITHUB_RUN_ID': 'owned-smoke-nonce'}, clear=True), \
                    self.assertRaises(ValueError):
                d.record_build(SimpleNamespace(source_root=root, output=root / 'invalid.json'))
            self.assertFalse((root / 'invalid.json').exists())

    def test_manual_uploader_requires_reviewed_evidence_without_historical_dispatch_defaults(self):
        source = (ROOT / '.github/workflows/draft-upload.yml').read_text()
        self.assertNotIn('if: ${{ false }}', source)
        self.assertIn('workflow_dispatch:', source)
        self.assertIn('reviewed_build_evidence:', source)
        self.assertNotIn('default:', source)
        self.assertIn('--expected-evidence-sha256', source)
        self.assertNotIn('38072571180', source)


if __name__ == '__main__':
    unittest.main()
