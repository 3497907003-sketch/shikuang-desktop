"""Replay public native-v2 bindings; private baseline validation stays in CI.

The approved successful CI run and artifact digest authenticate the producer's
validate_content result. This adapter never opens a private-backup path.
"""
from datetime import datetime, timedelta
import hashlib
from pathlib import Path, PureWindowsPath
import re
import sys

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import verify_native_smoke_report as native

ORIGIN = native.ORIGIN


def need(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc(value):
    need(isinstance(value, str), 'Missing UTC lifecycle time')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(result.utcoffset() is not None and result.utcoffset().total_seconds() == 0,
         'Lifecycle time is not UTC')
    return result


def offline(snapshot):
    need(isinstance(snapshot, dict) and snapshot.get('query_succeeded') is True and
         type(snapshot.get('active_adapter_count')) is int and snapshot['active_adapter_count'] == 0 and
         type(snapshot.get('default_route_count')) is int and snapshot['default_route_count'] == 0,
         'Incomplete or online phase network snapshot')


def empty_port(snapshot):
    need(isinstance(snapshot, dict) and snapshot.get('query_succeeded') is True and
         snapshot.get('listeners') == [], 'Port cleanup observation failed')


def validate_bundle(archive, identity, desktop_source, read_json, stream_identity, executable_sha256, validate_network):
    # Reuse the exact source that performed private validation in the build.
    for relative in ('tools/verify_native_smoke_report.py', 'desktop/smoke.py'):
        need(digest(TOOLS.parent / relative) == digest(desktop_source / relative),
             'Uploader and approved native validation SOURCE differ: ' + relative)
    prefix = 'artifacts/windows-runtime/'
    names = archive.namelist()
    need(not any('private-backup' in tuple(p.lower() for p in name.split('/')) for name in names),
         'Private baseline content must not be transported in a delivery artifact')
    receipt = read_json(archive, prefix + 'windows-smoke-receipt.json')
    network = read_json(archive, prefix + 'network-proof.json')
    validate_network(network)
    nonce = receipt.get('run_id')
    owned = native.record_id(nonce)
    need(receipt.get('schema') == 'shikuang-windows-smoke-receipt-v1' and receipt.get('passed') is True and
         not receipt.get('failure') and network.get('schema') == 'shikuang-windows-offline-snapshots-v1' and
         network.get('passed') is True and network.get('run_id') == nonce,
         'Smoke/network receipt schema, nonce or completion mismatch')
    restoration = receipt.get('restoration', {})
    need(restoration.get('attempted') is True and restoration.get('passed') is True and
         restoration.get('enable_failures') == [] and network.get('restoration') == restoration,
         'Network restoration proof failed')
    initial = receipt.get('initial_adapters', [])
    restored = restoration.get('adapters', [])
    need(isinstance(initial, list) and bool(initial) and isinstance(restored, list) and
         network.get('disabled_adapter_names') == [row.get('Name') for row in initial] and
         len({row.get('InterfaceGuid') for row in initial}) == len(initial) and
         all(row.get('InterfaceGuid') and sum(other.get('InterfaceGuid') == row['InterfaceGuid'] and
             other.get('Status') == 'Up' for other in restored) == 1 for row in initial),
         'Initially active adapters were not all restored')
    empty_port(receipt.get('cleanup_port'))
    offline(network.get('initial_disabled_snapshot'))
    phases = receipt.get('phases', [])
    network_phases = network.get('phases', [])
    need([p.get('phase') for p in phases] == ['write', 'restart'] and
         [p.get('phase') for p in network_phases] == ['write', 'restart'],
         'Two ordered actual process phases required')
    need(receipt.get('executable_sha256') == executable_sha256, 'Smoke executable differs from packaged PE')
    results, finals, launches = {}, {}, set()
    for row, netrow in zip(phases, network_phases):
        phase = row['phase']
        need(row.get('passed') is True and row.get('timed_out') is False and
             row.get('host_exit_code') == 0 and row.get('launcher', {}).get('exit_code') == 0,
             'Phase execution or host exit failed')
        for point in ('before', 'after'):
            snapshot = row.get('network_' + point)
            offline(snapshot)
            need(netrow.get(point) == snapshot, 'Network phase observations were mixed')
        empty_port(row.get('port_before'))
        empty_port(row.get('port_after'))
        start_name, stop_name = (prefix + phase + '.json.host-' + suffix + '.json' for suffix in ('start', 'stop'))
        started, stopped = read_json(archive, start_name), read_json(archive, stop_name)
        need(row.get('host_start') == started and row.get('host_stop') == stopped and
             stream_identity(archive, start_name)['sha256'] == row.get('host_start_sha256') and
             stream_identity(archive, stop_name)['sha256'] == row.get('host_stop_sha256'),
             'Saved host lifecycle SHA or observation differs')
        need(started.get('schema') == 'shikuang-smoke-host-start-v1' and
             stopped.get('schema') == 'shikuang-smoke-host-stop-v1', 'Wrong host lifecycle schema')
        fields = ('run_id', 'phase', 'launch_id', 'pid', 'parent_pid', 'executable',
                  'python_entry_utc', 'server_started_utc', 'origin')
        need(all(stopped.get(key) == started.get(key) and key in started for key in fields) and
             started['run_id'] == nonce and started['phase'] == phase and started['origin'] == ORIGIN and
             type(started['pid']) is int and started['pid'] > 0 and
             type(started['parent_pid']) is int and started['parent_pid'] > 0 and
             isinstance(started['launch_id'], str) and re.fullmatch(r'[a-f0-9]{32}', started['launch_id']) and
             started['launch_id'] not in launches,
             'Wrong or reused phase/nonce/host identity')
        launches.add(started['launch_id'])
        launcher = row['launcher']
        need(type(launcher.get('pid')) is int and
             ((row.get('host_relation') == 'launcher-is-python-host' and launcher['pid'] == started['pid']) or
              (row.get('host_relation') == 'direct-child-python-host' and launcher['pid'] == started['parent_pid'])),
             'Host is not the observed launcher or direct child')
        need(started['executable'] == receipt.get('executable') and stopped.get('exit_code') == 0 and
             stopped.get('server_stop_completed') is True and stopped.get('instance_lock_closed') is True,
             'Host executable/cleanup differs')
        need(utc(row['network_before']['sampled_at_utc']) <= utc(launcher['requested_at_utc']) and
             utc(launcher['exit_observed_at_utc']) <= utc(row['network_after']['sampled_at_utc']),
             'Network observations do not bracket this process phase')
        need(utc(launcher['requested_at_utc']) - timedelta(seconds=2) <= utc(started['python_entry_utc']) <=
             utc(started['server_started_utc']) <= utc(stopped['shutdown_at_utc']) <=
             utc(launcher['exit_observed_at_utc']), 'Lifecycle times are not ordered')
        during = row.get('port_during', {})
        need(during.get('query_succeeded') is True and during.get('listeners') == [
            {'LocalAddress': '127.0.0.1', 'LocalPort': 8000, 'OwningProcess': started['pid']}],
            'Observed host did not exclusively own the stable origin')
        final_name, raw_name = prefix + phase + '.json', prefix + phase + '.native.json'
        final, raw = read_json(archive, final_name), read_json(archive, raw_name)
        raw_pin, final_pin = stream_identity(archive, raw_name), stream_identity(archive, final_name)
        need(row.get('native_report') == {'file': phase + '.native.json', **raw_pin} and
             raw_pin['sha256'] == row.get('python_report_sha256') and
             final_pin['sha256'] == row.get('report_sha256'), 'Original/annotated report pins differ')
        need(raw.get('physicalNetworkDisabled') is False and final.get('physicalNetworkDisabled') is True and
             final == {**raw, 'physicalNetworkDisabled': True} and raw.get('externalHttpRuleInstalled') is False,
             'Host annotation changed more than its network observation')
        public = native.validate_public_content(raw, phase, started, identity, nonce)
        baseline = raw.get('baseline', {})
        native.validate_snapshot(raw.get('persistence', {}).get('snapshot'), nonce, baseline)
        contract = row.get('native_content_contract', {})
        original = contract.get('report', {})
        need(original.get('bytes') == raw_pin['bytes'] and original.get('sha256') == raw_pin['sha256'] and
             PureWindowsPath(original.get('path', '')).name == phase + '.json' and
             contract.get('verifier_source_sha256') == digest(desktop_source / 'tools/verify_native_smoke_report.py') and
             contract.get('contract') == native.source_identity(identity)['contract'] and
             contract.get('native_smoke_script_sha256') == native.source_identity(identity)['script_sha256'] and
             contract.get('run_id') == nonce and contract.get('phase') == phase and
             contract.get('record_id') == owned and contract.get('host_launch_id') == started['launch_id'] and
             contract.get('check_count') == public['check_count'] and
             type(contract.get('baseline_record_count')) is int and
             contract['baseline_record_count'] == baseline.get('record_count'),
             'CI private-content validation result is not bound to this raw report/source')
        need(row.get('persistence_mode') == raw['persistence'].get('mode'), 'Persistence mode receipt differs')
        results[phase] = {'nativeCheckCount': public['check_count'], 'privateBaselineValidatedInBuild': True,
                          'privateBaselineReopenedByDownloader': False, 'smokeNonce': nonce,
                          'hostLaunchId': started['launch_id'], 'rawReportSha256': raw_pin['sha256'],
                          'annotatedReportSha256': final_pin['sha256']}
        finals[phase] = (final, final_pin, original)
    write, write_pin, write_original = finals['write']
    restart = finals['restart'][0]
    need(restart.get('baseline') == write.get('baseline') and restart.get('private_backup') == write.get('private_backup') and
         restart['persistence']['snapshot'] == write['persistence']['snapshot'] and
         restart['persistence'].get('expected_write_report') == {'path': write_original['path'], **write_pin} and
         restart['persistence'].get('verified_before_any_put') is True and
         restart['persistence'].get('cleanup_verified') is True,
         'Restart did not bind the exact annotated write/content proof')
    return results
