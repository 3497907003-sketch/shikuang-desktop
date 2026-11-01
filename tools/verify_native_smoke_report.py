"""Validate the current owned-nonce native smoke contract without running an App.

PowerShell owns live Windows process, port and disabled-adapter observations.
This verifier binds the JSON it reads to the same native v2 source, private
backup and prior write receipt. It never writes storage, runs inference or
publishes artifacts. The CLI is strict about Windows offline observations;
the reusable content validator alone makes no physical-network claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop.smoke import (PRIVATE_BACKUP_LIMIT, expected_write_snapshot,
                           private_backup_path, record_id, source_identity,
                           validate_private_backup)

ORIGIN = 'http://127.0.0.1:8000'
SHA = re.compile(r'^[a-f0-9]{64}$')
SNAPSHOT_CHECKS = ('persistence-record-present', 'persistence-photos-populated',
                   'persistence-owned-nonce')
PREFIX = ('exact-owned-nonce', 'exact-smoke-source', 'original-stable-origin',
          'secure-local-context', 'private-backup-proof-present',
          'all-existing-records-and-local-storage-unchanged-before-actions')
ENGINE_GALLERY = ('manifest-bound-models', 'cancel-active-worker',
                  'real-quartz-recognition', 'real-dog-rejection',
                  'complete-local-reference-library', 'full-117-photo-manifest',
                  'quartz-three-native-gallery-decodes', 'gallery-does-not-write-specimens')
STORED_RECORD = ('smoke-record-still-present', 'backup-retains-model-identity',
                 'existing-records-retained')
SUFFIX = ('bundled-resources-only', 'all-existing-content-exact-at-completion')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pin(path: Path) -> dict:
    body = path.read_bytes()
    return {'path': str(path), 'bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest()}


def read_json(path: Path, maximum: int = 8 * 1024 * 1024) -> tuple[dict, dict]:
    path = path.resolve()
    require(path.is_file() and 0 < path.stat().st_size <= maximum, 'Missing or oversized JSON proof')
    with path.open('rb') as stream:
        body = stream.read(maximum + 1)
    require(len(body) <= maximum, 'JSON proof grew beyond its limit')
    value = json.loads(body.decode('utf-8-sig'))
    require(isinstance(value, dict), 'JSON proof must be an object')
    return value, {'path': str(path), 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


def expected_names(phase: str, external_rule: bool) -> tuple[str, ...]:
    require(phase in ('write', 'restart') and type(external_rule) is bool, 'Invalid phase/rule control')
    before = (('write-nonce-absent-before-actions',) if phase == 'write' else
              ('matching-restart-proof-required',) + SNAPSHOT_CHECKS + ('history-survives-restart',))
    rule = ('external-http-blocked',) if external_rule else ()
    write_only = (('smoke-write-does-not-overwrite-existing-fixture', 'smoke-photo-fits-record-storage',
                   'existing-content-unchanged-before-owned-put') if phase == 'write' else ())
    cleanup = (() if phase == 'write' else SNAPSHOT_CHECKS +
               ('owned-content-proof-before-cleanup', 'smoke-record-cleanup',
                'all-existing-content-restored-after-own-delete'))
    tail = SNAPSHOT_CHECKS if phase == 'write' else ()
    return PREFIX + before + rule + ENGINE_GALLERY + write_only + STORED_RECORD + cleanup + SUFFIX + tail


def validate_snapshot(snapshot: dict, run_id: str, baseline: dict):
    require(isinstance(snapshot, dict) and set(snapshot) == {
        'schema', 'record_id', 'record_sha256', 'photos_sha256', 'photo_count', 'sample_id', 'baseline'},
        'Snapshot must contain the exact v2 content proof')
    require(snapshot['schema'] == 'shikuang-persistence-snapshot-v2' and
            snapshot['record_id'] == record_id(run_id) and
            snapshot['sample_id'] == 'desktop-smoke-' + run_id and
            type(snapshot['photo_count']) is int and snapshot['photo_count'] == 1 and
            isinstance(snapshot['baseline'], dict) and
            type(snapshot['baseline'].get('record_count')) is int and
            snapshot['baseline'] == baseline, 'Snapshot nonce/photo/baseline mismatch')
    require(all(isinstance(snapshot[k], str) and SHA.fullmatch(snapshot[k])
                for k in ('record_sha256', 'photos_sha256')), 'Invalid full record/photo digest')


def validate_backup(report: dict, write_path: Path, identity: dict, run_id: str) -> dict:
    backup = report.get('private_backup')
    expected = private_backup_path(write_path.resolve(), run_id)
    require(isinstance(backup, dict) and set(backup) == {'path', 'bytes', 'sha256'} and
            backup['path'] == str(expected) and type(backup['bytes']) is int and
            0 < backup['bytes'] <= PRIVATE_BACKUP_LIMIT and
            isinstance(backup['sha256'], str) and SHA.fullmatch(backup['sha256']),
            'Missing bounded own private-backup identity')
    require(not expected.is_symlink() and expected.resolve() == expected and expected.is_file(),
            'Private backup path escaped its owned location')
    require(os.name == 'nt' or expected.stat().st_mode & 0o077 == 0, 'Private backup is not owner-only')
    payload, actual = read_json(expected, PRIVATE_BACKUP_LIMIT)
    require(actual == backup, 'Private backup bytes/SHA changed')
    baseline = validate_private_backup(payload, identity, run_id)
    require(isinstance(report.get('baseline'), dict) and
            type(report['baseline'].get('record_count')) is int and
            type(payload.get('baseline', {}).get('record_count')) is int and
            report['baseline'] == baseline, 'Native baseline does not match saved private content')
    return baseline


def validate_content(report: dict, phase: str, started: dict, identity: dict,
                     run_id: str, report_path: Path, write_path: Path | None = None) -> dict:
    """Validate native content only; live OS/network evidence stays with caller."""
    owned = record_id(run_id)
    require(report.get('passed') is True and report.get('errors') == [] and
            not report.get('failure'), 'Native phase did not complete successfully')
    require(report.get('phase') == phase and report.get('run_id') == run_id and
            report.get('host') == started, 'Native phase/nonce/host-start proof mismatch')
    require(started.get('schema') == 'shikuang-smoke-host-start-v1' and
            started.get('phase') == phase and started.get('run_id') == run_id and
            type(started.get('pid')) is int and started['pid'] > 0 and
            type(started.get('parent_pid')) is int and started['parent_pid'] > 0 and
            isinstance(started.get('launch_id'), str) and
            re.fullmatch(r'[a-f0-9]{32}', started['launch_id']) and started.get('origin') == ORIGIN,
            'Invalid native host-start identity')
    require(report.get('origin') == ORIGIN and report.get('secureContext') is True and
            report.get('accuracy_claim') is False and type(report.get('online')) is bool and
            type(report.get('physicalNetworkDisabled')) is bool, 'Native origin/accuracy/network fields invalid')
    require(all(report.get(k) == identity[k] for k in ('app_version', 'web_commit', 'model_manifest_sha256')) and
            report.get('smoke_source_identity') == source_identity(identity),
            'Native package or embedded smoke SOURCE identity changed')
    rows = report.get('checks')
    require(isinstance(rows, list) and all(isinstance(r, dict) and set(r) == {'name', 'passed', 'details'} and
            r.get('passed') is True and isinstance(r.get('name'), str) for r in rows),
            'Missing or failed native check')
    expected = expected_names(phase, report.get('externalHttpRuleInstalled'))
    require(tuple(r.get('name') for r in rows) == expected,
            'Native checks missing, reordered, duplicated or unexpected for this phase')
    # Only the known snapshot triplet repeats, at its exact two restart positions.
    checks = {r['name']: r.get('details') for r in rows}
    require(all(isinstance(r['details'], dict) for r in rows if r['name'] not in
                ('original-stable-origin', 'quartz-three-native-gallery-decodes')),
            'Native check details have the wrong shape')
    require(checks['original-stable-origin'] == ORIGIN, 'Origin check differs from host origin')
    require(checks['manifest-bound-models'] == {'version': identity['model_version'], 'labels': 98,
            'modelIds': ['mobilenet', 'efficientnet', 'efficientnet-legacy']}, 'Packaged model contract changed')
    quartz = report.get('quartz', {})
    require(isinstance(quartz, dict) and quartz.get('key') == 'quartz' and
            quartz.get('engine') == 'onnx-worker-wasm' and quartz.get('modelCount') == 3 and
            quartz.get('modelVersion') == identity['model_version'] and
            quartz.get('manifestSha256') == identity['model_manifest_sha256'], 'Quartz did not use the bound WASM ensemble')
    score = quartz.get('score')
    require(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1, 'Invalid Quartz score')
    recognition = checks['real-quartz-recognition']
    require(isinstance(recognition, dict) and recognition.get('key') == 'quartz' and
            recognition.get('engine') == quartz['engine'] and recognition.get('score') == score and
            recognition.get('modelVersion') == identity['model_version'] and recognition.get('modelCount') == 3,
            'Recognition check differs from its native result')
    require(checks['real-dog-rejection'] == {'message': '这不是矿物，请您放入清晰的矿物照片。'}, 'Dog rejection message changed')
    require(checks['complete-local-reference-library'].get('species') == 6239 and
            checks['full-117-photo-manifest'] == {'assets': 117, 'records': 117}, 'Catalog/gallery contract changed')
    images = checks['quartz-three-native-gallery-decodes']
    ids = {'gallery-image-commons-10156983', 'gallery-image-commons-10122617', 'gallery-image-commons-10166671'}
    require(isinstance(images, list) and len(images) == 3 and all(isinstance(i, dict) for i in images) and
            {i.get('id') for i in images} == ids and all(type(i.get('width')) is int and type(i.get('height')) is int and
            min(i['width'], i['height']) >= 224 and isinstance(i.get('src'), str) and
            i['src'].startswith('blob:' + ORIGIN + '/') for i in images), 'Native gallery JPEG decode evidence changed')
    gallery = checks['gallery-does-not-write-specimens']
    require(isinstance(gallery, dict) and type(gallery.get('before')) is int and gallery['before'] >= 0 and
            gallery.get('after') == gallery['before'], 'Gallery modified specimen storage')
    require(checks['backup-retains-model-identity'] == {'count': 1}, 'Backup model identity check failed')
    cancellation = checks['cancel-active-worker']
    require(isinstance(cancellation, dict) and isinstance(cancellation.get('stage'), str) and
            re.search('读取|输入筛查|分析图像', cancellation['stage']) and cancellation['stage'] != 'deadline',
            'Cancellation was not observed during active work')
    resources = report.get('resources')
    require(isinstance(resources, list) and resources and all(isinstance(r, dict) and isinstance(r.get('name'), str)
            and (r['name'].startswith(ORIGIN + '/') or r['name'].startswith('blob:' + ORIGIN + '/') or
                 (report['externalHttpRuleInstalled'] and r['name'] == 'https://example.com/')) for r in resources),
            'Unexpected external resource or missing resource evidence')
    require(type(report.get('galleryFrameSynchronized')) is bool, 'Missing native frame synchronization observation')
    actual_write = report_path if phase == 'write' else write_path
    require(actual_write is not None and (phase == 'write' or actual_write.resolve() != report_path.resolve()),
            'Restart requires a separate matching write report')
    baseline = validate_backup(report, actual_write, identity, run_id)
    existing = checks['existing-records-retained']
    require(existing == {'existingCount': baseline['record_count'], 'fullContentVerified': True},
            'Existing content preservation check differs from private baseline')
    persistence = report.get('persistence')
    require(isinstance(persistence, dict), 'Missing persistence object')
    validate_snapshot(persistence.get('snapshot'), run_id, baseline)
    if phase == 'write':
        require(set(persistence) == {'mode', 'verified_before_any_put', 'snapshot'} and
                persistence['mode'] == 'write-content-snapshot' and persistence['verified_before_any_put'] is False,
                'Write must contain only its own v2 snapshot proof')
        size = checks['smoke-photo-fits-record-storage'].get('characters')
        require(type(size) is int and 0 < size < 250000, 'Smoke photo exceeds record-storage bound')
    else:
        require(set(persistence) == {'mode', 'verified_before_any_put', 'snapshot', 'expected_write_report', 'cleanup_verified'} and
                persistence['mode'] == 'full-content-sha256-before-any-put' and
                persistence['verified_before_any_put'] is True and persistence['cleanup_verified'] is True,
                'Restart did not verify full content before writes and clean its owned nonce')
        prior = expected_write_snapshot(actual_write, identity, run_id, started)
        require(persistence['snapshot'] == prior['snapshot'] and baseline == prior['baseline'] and
                report['private_backup'] == prior['private_backup'] and
                persistence['expected_write_report'] == prior['report'], 'Restart full content/write-byte binding changed')
        previous, previous_pin = read_json(actual_write)
        require(previous_pin == prior['report'], 'Write bytes changed during restart verification')
        # Validate every old write gate too, rather than trusting passed=true.
        validate_content(previous, 'write', previous.get('host', {}), identity, run_id, actual_write)
        require(pin(actual_write.resolve()) == prior['report'], 'Write report changed after its content validation')
    return {'contract': source_identity(identity)['contract'], 'run_id': run_id, 'phase': phase,
            'check_count': len(rows), 'record_id': owned, 'baseline_record_count': baseline['record_count'],
            'host_launch_id': started['launch_id'], 'native_smoke_script_sha256': source_identity(identity)['script_sha256'],
            'scope': 'native_content_contract_only; live_network_process_and_port_proofs_are_separate'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--phase', required=True, choices=('write', 'restart'))
    parser.add_argument('--host-start', required=True, type=Path)
    parser.add_argument('--identity', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--write-report', type=Path)
    args = parser.parse_args(argv)
    try:
        report, receipt = read_json(args.report)
        started, _ = read_json(args.host_start)
        identity, _ = read_json(args.identity)
        require(report.get('online') is False and report.get('physicalNetworkDisabled') is False and
                report.get('externalHttpRuleInstalled') is False, 'Expected native Windows offline report before host annotation')
        gate = validate_content(report, args.phase, started, identity, args.run_id,
                                args.report.resolve(), args.write_report.resolve() if args.write_report else None)
        require(pin(args.report.resolve()) == receipt, 'Native report changed while validating its proof')
        gate.update(report=receipt, verifier_source_sha256=pin(Path(__file__).resolve())['sha256'])
        # Transport JSON as ASCII even when Windows stdout uses a legacy code
        # page. Escaping paths does not change the UTF-8 proof-file bytes/SHA.
        print(json.dumps(gate, ensure_ascii=True, separators=(',', ':')))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        # Never print private records, photos, backup payloads or exception repr.
        message = 'Native smoke contract verification failed: ' + str(error)
        print(message.encode('ascii', 'backslashreplace').decode('ascii'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
