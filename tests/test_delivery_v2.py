"""Synthetic current-v2 archive; actual apps, images and user data are not read."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
import tempfile
import zipfile

import test_native_smoke_report as synthetic_fixture
RUN, IDENTITY, gate = synthetic_fixture.RUN, synthetic_fixture.IDENTITY, synthetic_fixture.gate

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('delivery_current_v2', ROOT / 'tools/verify_windows_delivery.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


def raw_json(value):
    return (json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def clock(second):
    return '2026-11-04T00:00:' + str(second).zfill(2) + '+00:00'


def network(second):
    return {'sampled_at_utc': clock(second), 'query_succeeded': True,
            'active_adapter_count': 0, 'default_route_count': 0,
            'active_adapters': [], 'default_routes': []}


class DeliveryNativeV2(unittest.TestCase):
    def setUp(self):
        # This existing fixture performs the real content verifier against a
        # temporary synthetic backup. Only public reports enter the ZIP below.
        self.fixture = synthetic_fixture.NativeV2HostGate()
        self.fixture.setUp()
        self.files = {}
        self.prefix = 'artifacts/windows-runtime/'
        self.phases = []
        for phase, report in [('write', self.fixture.write), ('restart', self.fixture.restart)]:
            n = 0 if phase == 'write' else 10
            host = report['host']
            host.update(executable='C:\\SYNTHETIC\\Shikuang.exe',
                        python_entry_utc=clock(n + 2), server_started_utc=clock(n + 3))
            report.update(architecture='AMD64', platform='Windows-10-10.0.20348-SP0')
            path = self.fixture.write_path if phase == 'write' else self.fixture.restart_path
            if phase == 'restart':
                report['persistence']['expected_write_report'] = gate.pin(self.fixture.write_path)
            path.write_bytes(raw_json(report))
            verified = gate.validate_content(report, phase, host, IDENTITY, RUN, path,
                                            self.fixture.write_path if phase == 'restart' else None)
            native_raw = path.read_bytes()
            verified.update(report=gate.pin(path), verifier_source_sha256=sha((ROOT / 'tools/verify_native_smoke_report.py').read_bytes()))
            report['physicalNetworkDisabled'] = True
            path.write_bytes(raw_json(report))
            stopped = {**host, 'schema': 'shikuang-smoke-host-stop-v1',
                       'shutdown_at_utc': clock(n + 4), 'server_stop_completed': True,
                       'instance_lock_closed': True, 'exit_code': 0}
            for suffix, body in [('.native.json', native_raw), ('.json', path.read_bytes()),
                                 ('.json.host-start.json', raw_json(host)),
                                 ('.json.host-stop.json', raw_json(stopped))]:
                self.files[self.prefix + phase + suffix] = body
            self.phases.append({'phase': phase, 'passed': True, 'timed_out': False,
                'host_exit_code': 0, 'launcher': {'pid': host['parent_pid'], 'exit_code': 0,
                    'requested_at_utc': clock(n + 1), 'exit_observed_at_utc': clock(n + 5)},
                'host_relation': 'direct-child-python-host', 'host_start': copy.deepcopy(host),
                'host_stop': stopped, 'host_start_sha256': sha(raw_json(host)),
                'host_stop_sha256': sha(raw_json(stopped)), 'native_content_contract': verified,
                'python_report_sha256': sha(native_raw), 'report_sha256': sha(path.read_bytes()),
                'native_report': {'file': phase + '.native.json', 'bytes': len(native_raw), 'sha256': sha(native_raw)},
                'persistence_mode': report['persistence']['mode'], 'network_before': network(n),
                'network_after': network(n + 6), 'port_before': {'query_succeeded': True, 'listeners': []},
                'port_after': {'query_succeeded': True, 'listeners': []},
                'port_during': {'query_succeeded': True, 'listeners': [
                    {'LocalAddress': '127.0.0.1', 'LocalPort': 8000, 'OwningProcess': host['pid']}]}})
        restoration = {'attempted': True, 'passed': True, 'enable_failures': [],
                       'adapters': [{'Name': 'Ethernet', 'InterfaceGuid': 'SYNTHETIC-GUID', 'Status': 'Up'}],
                       'finished_at_utc': clock(18)}
        self.receipt = {'schema': 'shikuang-windows-smoke-receipt-v1', 'run_id': RUN, 'passed': True,
            'phases': self.phases, 'executable': 'C:\\SYNTHETIC\\Shikuang.exe', 'executable_sha256': 'a' * 64,
            'restoration': restoration, 'cleanup_port': {'query_succeeded': True, 'listeners': []},
            'initial_adapters': [{'Name': 'Ethernet', 'InterfaceGuid': 'SYNTHETIC-GUID', 'Status': 'Up'}]}
        self.proof = {'schema': 'shikuang-windows-offline-snapshots-v1', 'run_id': RUN, 'passed': True,
            'physical_network_disabled': True, 'active_adapter_count': 0, 'default_route_count': 0,
            'disabled_adapter_names': ['Ethernet'], 'platform': 'Microsoft Windows NT 10.0.20348.0',
            'initial_disabled_snapshot': network(0), 'restoration': copy.deepcopy(restoration),
            'phases': [{'phase': row['phase'], 'before': row['network_before'], 'after': row['network_after']}
                       for row in self.phases]}
        # The downloader must succeed without access to this now-absent backup.
        self.fixture.backup.unlink()

    def tearDown(self):
        self.fixture.tearDown()

    def validate(self, files=None, receipt=None, proof=None, candidate_source=None):
        files = dict(self.files if files is None else files)
        files[self.prefix + 'windows-smoke-receipt.json'] = raw_json(self.receipt if receipt is None else receipt)
        files[self.prefix + 'network-proof.json'] = raw_json(self.proof if proof is None else proof)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            for name, body in files.items():
                z.writestr(name, body)
        stream.seek(0)
        with zipfile.ZipFile(stream) as z:
            d.inspect_zip(z)
            return d.v2.validate_bundle(z, IDENTITY, ROOT if candidate_source is None else candidate_source, d.read_json, d.stream_identity, 'a' * 64, d.validate_network)

    def test_current_v2_both_phases_bind_original_and_annotated_bytes_without_private_backup(self):
        result = self.validate()
        self.assertEqual(result['write']['nativeCheckCount'], 26)
        self.assertEqual(result['restart']['nativeCheckCount'], 30)
        self.assertTrue(result['restart']['privateBaselineValidatedInBuild'])
        self.assertFalse(result['restart']['privateBaselineReopenedByDownloader'])
        self.assertNotEqual(result['write']['rawReportSha256'], result['write']['annotatedReportSha256'])
        for phase in ('write', 'restart'):
            self.assertTrue(d.validate_runtime(json.loads(self.files[self.prefix + phase + '.json']), phase, IDENTITY)['passed'])

    def test_any_extra_missing_reordered_or_unapproved_repeat_fails(self):
        for change in ('duplicate', 'missing', 'order'):
            files = dict(self.files)
            name = self.prefix + 'restart.native.json'
            value = json.loads(files[name])
            if change == 'duplicate': value['checks'].append(value['checks'][0])
            elif change == 'missing': value['checks'].pop(0)
            else: value['checks'][0], value['checks'][1] = value['checks'][1], value['checks'][0]
            # Even with recomputed transport pins, native expected_names is strict.
            raw = raw_json(value); files[name] = raw
            final = {**value, 'physicalNetworkDisabled': True}
            files[self.prefix + 'restart.json'] = raw_json(final)
            receipt = copy.deepcopy(self.receipt); row = receipt['phases'][1]
            row['native_report'].update(bytes=len(raw), sha256=sha(raw))
            row['python_report_sha256'] = sha(raw)
            row['native_content_contract']['report'].update(bytes=len(raw), sha256=sha(raw))
            row['report_sha256'] = sha(files[self.prefix + 'restart.json'])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(files=files, receipt=receipt)

    def test_wrong_phase_nonce_hash_host_and_persistence_receipts_fail(self):
        mutations = [lambda x: x['phases'][1].update(phase='write'),
                     lambda x: x.update(run_id='f' * 32),
                     lambda x: x['phases'][0].update(report_sha256='0' * 64),
                     lambda x: x['phases'][0]['host_start'].update(pid=999),
                     lambda x: x['phases'][0]['native_content_contract'].update(verifier_source_sha256='0' * 64),
                     lambda x: x['phases'][1]['native_content_contract'].update(phase='write')]
        for mutate in mutations:
            receipt = copy.deepcopy(self.receipt); mutate(receipt)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.validate(receipt=receipt)
        files = dict(self.files); files[self.prefix + 'write.json'] += b' '
        with self.assertRaises(ValueError): self.validate(files=files)

    def test_network_mixing_failed_restoration_and_port_or_exit_failures_rejected(self):
        for key in ('active_adapter_count', 'default_route_count'):
            proof = copy.deepcopy(self.proof); proof['phases'][0]['before'][key] = 1
            with self.subTest(key=key), self.assertRaises(ValueError): self.validate(proof=proof)
        for mutate in [lambda x: x['restoration'].update(passed=False),
                       lambda x: x['phases'][1].update(host_exit_code=1),
                       lambda x: x['phases'][1]['port_after'].update(listeners=[{'OwningProcess': 9}])]:
            receipt = copy.deepcopy(self.receipt); mutate(receipt)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.validate(receipt=receipt)

    def test_candidate_verifier_or_smoke_source_difference_is_rejected_before_replay(self):
        for changed in ('tools/verify_native_smoke_report.py', 'desktop/smoke.py'):
            with tempfile.TemporaryDirectory() as temp:
                candidate = Path(temp)
                for relative in ('tools/verify_native_smoke_report.py', 'desktop/smoke.py'):
                    target = candidate / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((ROOT / relative).read_bytes() +
                                       (b'\n# SYNTHETIC DIFFERENT CANDIDATE SOURCE\n' if relative == changed else b''))
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'SOURCE differ: ' + changed.replace('.', r'\.')):
                    self.validate(candidate_source=candidate)

    def test_private_backup_payload_in_archive_is_always_rejected(self):
        files = {**self.files, self.prefix + 'private-backup/fixture.json': b'SYNTHETIC-PRIVATE-NOT-PUBLIC'}
        with self.assertRaises(ValueError): self.validate(files=files)


if __name__ == '__main__':
    unittest.main()
