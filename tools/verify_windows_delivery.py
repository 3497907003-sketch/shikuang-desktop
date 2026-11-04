"""Verify a tested Windows artifact, then optionally upload only to a draft.

The uploader's own commit is distinct from the explicitly reviewed build.
No installer is executed here; source/run/attempt and artifact are bound by
a caller-supplied evidence file and its independently supplied SHA256.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import struct
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

REPOSITORY = '3497907003-sketch/shikuang-desktop'
ARTIFACT_NAME = 'windows-x64-build-and-runtime'
ORIGIN = 'http://127.0.0.1:8000'
SHA = re.compile(r'^[a-f0-9]{64}$')
COMMIT = re.compile(r'^[a-f0-9]{40}$')
_V2_SPEC = importlib.util.spec_from_file_location('windows_v2_delivery', Path(__file__).with_name('windows_v2_delivery.py'))
v2 = importlib.util.module_from_spec(_V2_SPEC)
_V2_SPEC.loader.exec_module(v2)
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
CHECKS = {
    'original-stable-origin', 'secure-local-context', 'manifest-bound-models',
    'cancel-active-worker', 'real-quartz-recognition', 'real-dog-rejection',
    'complete-local-reference-library', 'full-117-photo-manifest',
    'quartz-three-native-gallery-decodes', 'gallery-does-not-write-specimens',
    'backup-retains-model-identity', 'existing-records-retained', 'bundled-resources-only',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api(path, method='GET', data=None, missing=False):
    command = ['gh', 'api', '-X', method, path]
    if data is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        if missing and '(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError('GitHub API request failed: ' + path + ' (exit ' + str(result.returncode) + ')')
    return json.loads(result.stdout) if result.stdout else None


def checked_evidence(raw, expected_sha256):
    require(isinstance(raw, bytes) and 0 < len(raw) <= 65536 and
            isinstance(expected_sha256, str) and SHA.fullmatch(expected_sha256) and
            hashlib.sha256(raw).hexdigest() == expected_sha256, 'Reviewed build evidence SHA256 mismatch')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate evidence field')
            value[key] = item
        return value
    evidence = json.loads(raw, object_pairs_hook=unique)
    fields = {'schema_version', 'repository', 'run_id', 'run_attempt', 'source_commit',
              'web_commit', 'app_version', 'tag', 'artifact', 'asset_index_sha256'}
    require(isinstance(evidence, dict) and set(evidence) == fields and
            type(evidence['schema_version']) is int and evidence['schema_version'] == 1 and
            evidence['repository'] == REPOSITORY, 'Invalid reviewed build evidence schema/repository')
    require(all(type(evidence[k]) is int and evidence[k] > 0 for k in ('run_id', 'run_attempt')) and
            all(isinstance(evidence[k], str) and COMMIT.fullmatch(evidence[k])
                for k in ('source_commit', 'web_commit')), 'Immutable run/attempt/source pins required')
    require(isinstance(evidence['app_version'], str) and
            re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-(?:preview|rc)\.[0-9]+)?', evidence['app_version']) and
            evidence['tag'] == 'v' + evidence['app_version'], 'Draft tag and app version differ')
    require(isinstance(evidence['asset_index_sha256'], str) and SHA.fullmatch(evidence['asset_index_sha256']),
            'Missing reviewed asset-index SHA256')
    artifact = evidence['artifact']
    require(isinstance(artifact, dict) and set(artifact) == {'id', 'size_in_bytes', 'digest'} and
            type(artifact['id']) is int and artifact['id'] > 0 and
            type(artifact['size_in_bytes']) is int and 0 < artifact['size_in_bytes'] < MAX_FILE and
            isinstance(artifact['digest'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', artifact['digest']),
            'Invalid reviewed artifact identity')
    return evidence


def validate_run(run, evidence):
    repository, source_commit = evidence['repository'], evidence['source_commit']
    require(repository == REPOSITORY and COMMIT.fullmatch(source_commit), 'Only the reviewed repository and immutable commit are allowed')
    require(type(run.get('id')) is int and run['id'] == evidence['run_id'] and
            type(run.get('run_attempt')) is int and run['run_attempt'] == evidence['run_attempt'],
            'Build run/attempt differs from reviewed evidence')
    require(run.get('repository', {}).get('full_name') == repository and
            run.get('head_repository', {}).get('full_name') == repository, 'Build repository/fork mismatch')
    require(run.get('head_sha') == source_commit, 'Build was not made from the reviewed commit')
    require(run.get('path') == '.github/workflows/desktop.yml', 'Run is not the desktop build workflow')
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'Build and real runtime gates must have succeeded')
    require(run.get('event') in ('push', 'workflow_dispatch'), 'Unsupported build event')


def select_artifact(artifacts, evidence):
    matches = [a for a in artifacts if a.get('name') == ARTIFACT_NAME]
    require(len(matches) == 1, 'Exactly one Windows build artifact is required')
    artifact = matches[0]
    require(artifact.get('expired') is False, 'Windows artifact has expired')
    require(artifact.get('workflow_run', {}).get('id') == evidence['run_id'] and
            artifact.get('workflow_run', {}).get('head_sha') == evidence['source_commit'], 'Artifact run/source mismatch')
    require(type(artifact.get('id')) is int and type(artifact.get('size_in_bytes')) is int and
            {key: artifact.get(key) for key in ('id', 'size_in_bytes', 'digest')} == evidence['artifact'],
            'Artifact ID/digest/size differs from reviewed evidence')
    return artifact


def load_context(path, expected_sha256):
    require(path.is_file() and path.stat().st_size <= 16 * 1024 * 1024, 'Missing or oversized guarded context')
    context = json.loads(path.read_text(encoding='utf-8'))
    require(set(context) == {'schema_version', 'evidence_json', 'run', 'artifact'} and
            context['schema_version'] == 2 and isinstance(context['evidence_json'], str), 'Invalid guarded context schema')
    evidence = checked_evidence(context['evidence_json'].encode('utf-8'), expected_sha256)
    validate_run(context['run'], evidence)
    select_artifact([context['artifact']], evidence)
    # API metadata was verified above; downstream keeps the exact reviewed
    # ID/size/digest pin instead of replacing it with the larger API response.
    return {**evidence, 'run': context['run']}


def package_name(evidence):
    return 'Shikuang-' + evidence['app_version'] + '-windows-x64.zip'



def matching_release(repository, tag):
    matches = []
    # The by-tag endpoint can omit a draft. Enumerate without exposing signed URLs.
    for page in range(1, 101):
        rows = api(f'repos/{repository}/releases?per_page=100&page={page}')
        matches.extend(r for r in rows if r.get('tag_name') == tag)
        if len(rows) < 100:
            break
    require(len(matches) <= 1, 'Ambiguous release tag')
    return matches[0] if matches else None


def validate_release(release, tag, source_commit):
    require(isinstance(tag, str) and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:preview|rc)\.[0-9]+)?', tag) and
            isinstance(source_commit, str) and COMMIT.fullmatch(source_commit), 'Immutable draft tag/source required')
    if release is None:
        return
    require(release.get('tag_name') == tag, 'Release tag mismatch')
    require(release.get('draft') is True and release.get('prerelease') is True,
            'Refusing to change a public release or a non-preview draft')
    require(release.get('target_commitish') == source_commit, 'Existing draft targets another commit')


def validate_tag(repository, tag, source_commit):
    ref = api(f'repos/{repository}/git/ref/tags/{tag}', missing=True)
    if ref is None:
        return
    obj = ref['object']
    for _ in range(8):
        if obj.get('type') == 'commit':
            require(obj.get('sha') == source_commit, 'Existing Git tag targets another commit')
            return
        require(obj.get('type') == 'tag', 'Unsupported Git tag object')
        obj = api(f'repos/{repository}/git/tags/{obj["sha"]}')['object']
    raise ValueError('Git tag chain is too deep')


def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def valid_relative(name):
    path = PurePosixPath(name)
    return bool(name) and '\\' not in name and ':' not in name and not path.is_absolute() and \
        all(p not in ('', '.', '..') for p in name.rstrip('/').split('/'))


def inspect_zip(archive):
    names, folded, total = {}, set(), 0
    require(len(archive.infolist()) < 10000, 'Too many ZIP entries')
    for info in archive.infolist():
        # ZipInfo normalizes host separators on Windows and truncates NULs.
        # Validate the original archive spelling before using its parsed name.
        require(valid_relative(info.orig_filename) and info.filename == info.orig_filename,
                'Unsafe or normalized ZIP path: ' + info.orig_filename)
        require(valid_relative(info.filename), 'Unsafe ZIP path: ' + info.filename)
        require(info.filename not in names and info.filename.casefold() not in folded,
                'Duplicate or case-colliding ZIP path: ' + info.filename)
        require(not stat.S_ISLNK(info.external_attr >> 16), 'ZIP symlink is forbidden')
        require(not (info.flag_bits & 1), 'Encrypted ZIP is forbidden')
        require(0 <= info.file_size <= MAX_FILE, 'Oversized ZIP entry')
        total += info.file_size
        require(total <= MAX_TOTAL, 'Oversized expanded ZIP')
        names[info.filename] = info
        folded.add(info.filename.casefold())
    return names


def read_json(archive, name):
    info = archive.getinfo(name)
    require(info.file_size <= 16 * 1024 * 1024, 'Oversized JSON metadata')
    return json.loads(archive.read(name).decode('utf-8-sig'))


def stream_identity(archive, name):
    digest, length = hashlib.sha256(), 0
    with archive.open(name) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            length += len(chunk)
            require(length <= MAX_FILE, 'Oversized streamed ZIP member')
            digest.update(chunk)
    return {'bytes': length, 'sha256': digest.hexdigest()}


def verify_seal(archive, prefix, files):
    require(isinstance(files, dict) and bool(files), 'Missing resource seal')
    actual = {n[len(prefix):] for n in archive.namelist() if n.startswith(prefix) and not n.endswith('/')}
    require(actual == set(files), 'Sealed directory file set differs from package')
    total = 0
    for name, identity in files.items():
        require(valid_relative(name) and isinstance(identity, dict) and SHA.fullmatch(identity.get('sha256', '')),
                'Invalid sealed resource identity')
        require(stream_identity(archive, prefix + name) == identity, 'Resource SHA/size mismatch: ' + name)
        total += identity['bytes']
    return total


def checksum_from_text(text):
    hashes = re.findall(r'(?im)^Hash\s*:\s*([a-f0-9]{64})\s*$', text)
    require(len(hashes) == 1, 'Builder checksum must contain exactly one SHA256')
    return hashes[0].lower()


def validate_runtime(report, phase, identity):
    require(report.get('passed') is True and report.get('errors') == [], 'Runtime phase failed')
    require(report.get('phase') == phase and report.get('web_commit') == identity['web_commit'] and
            report.get('model_manifest_sha256') == identity['model_manifest_sha256'] and
            report.get('app_version') == identity['app_version'], 'Runtime source identity mismatch')
    require(report.get('architecture') == 'AMD64' and report.get('platform') == 'Windows-10-10.0.20348-SP0',
            'Expected the actual Windows Server 2022 x64 runtime')
    require(report.get('physicalNetworkDisabled') is True and report.get('online') is False and
            report.get('accuracy_claim') is False and report.get('origin') == ORIGIN and
            report.get('secureContext') is True, 'Strict offline/origin/accuracy boundary failed')
    rows = report.get('checks', [])
    names = [r.get('name') for r in rows]
    require(report.get('externalHttpRuleInstalled') is False and
            tuple(names) == v2.native.expected_names(phase, False) and
            all(r.get('passed') is True for r in rows), 'Invalid ordered native-v2 runtime checks')
    required = CHECKS | ({'history-survives-restart', 'smoke-record-cleanup'} if phase == 'restart' else set())
    require(required <= set(names), 'Missing real runtime gate')
    checks = {r['name']: r.get('details') for r in rows}
    require(checks['original-stable-origin'] == ORIGIN, 'Origin changed')
    config = checks['manifest-bound-models']
    require(config.get('version') == identity['model_version'] and config.get('labels') == 98 and
            config.get('modelIds') == ['mobilenet', 'efficientnet', 'efficientnet-legacy'], 'Model list changed')
    quartz = report.get('quartz', {})
    require(quartz.get('key') == 'quartz' and quartz.get('engine') == 'onnx-worker-wasm' and
            quartz.get('modelVersion') == identity['model_version'] and
            quartz.get('manifestSha256') == identity['model_manifest_sha256'] and quartz.get('modelCount') == 3,
            'Quartz did not use the bound packaged model ensemble')
    score = quartz.get('score')
    require(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1, 'Invalid model score')
    require(checks['real-quartz-recognition'].get('key') == 'quartz' and
            checks['real-dog-rejection'].get('message') == '这不是矿物，请您放入清晰的矿物照片。', 'Recognition/rejection gate mismatch')
    require(checks['complete-local-reference-library'].get('species') == 6239 and
            checks['full-117-photo-manifest'] == {'assets': 117, 'records': 117}, 'Catalog/gallery coverage gate mismatch')
    images = checks['quartz-three-native-gallery-decodes']
    expected_ids = {'gallery-image-commons-10156983', 'gallery-image-commons-10122617', 'gallery-image-commons-10166671'}
    require(isinstance(images, list) and len(images) == 3 and {i.get('id') for i in images} == expected_ids and
            all(isinstance(i.get('width'), int) and isinstance(i.get('height'), int) and
                i['width'] >= 224 and i['height'] >= 224 and i.get('src', '').startswith('blob:' + ORIGIN + '/') for i in images),
            'Real native JPEG decode gate mismatch')
    gallery = checks['gallery-does-not-write-specimens']
    require(gallery.get('before') == gallery.get('after'), 'Gallery changed stored records')
    require(checks['backup-retains-model-identity'].get('count') == 1, 'Backup model identity gate failed')
    require(report.get('galleryFrameSynchronized') is True, 'Native frame synchronization failed')
    resources = report.get('resources', [])
    require(bool(resources), 'No local resource evidence')
    for row in resources:
        name = row.get('name', '')
        require(name.startswith('blob:' + ORIGIN + '/') or
                (urlparse(name).scheme == 'http' and urlparse(name).netloc == '127.0.0.1:8000'),
                'External resource request in offline phase')
    return {'passed': True, 'checks': names, 'physicalNetworkDisabled': True,
            'online': False, 'screenshotsPresent': bool(report.get('nativeScreenshot'))}


def validate_network(proof):
    require(proof.get('physical_network_disabled') is True and proof.get('active_adapter_count') == 0 and
            proof.get('default_route_count') == 0 and isinstance(proof.get('disabled_adapter_names'), list) and
            bool(proof['disabled_adapter_names']), 'Physical network/route gate failed')
    require(proof.get('platform') == 'Microsoft Windows NT 10.0.20348.0', 'Network evidence platform mismatch')


def validate_build_provenance(proof, evidence):
    expected = {'schema_version': 1, 'repository': evidence['repository'], 'run_id': evidence['run_id'],
                'run_attempt': evidence['run_attempt'], 'source_commit': evidence['source_commit'],
                'web_commit': evidence['web_commit'], 'app_version': evidence['app_version'],
                'asset_index_sha256': evidence['asset_index_sha256']}
    require(isinstance(proof, dict) and proof == expected and
            all(type(proof.get(key)) is int for key in ('schema_version', 'run_id', 'run_attempt')),
            'Packaged build provenance does not bind this reviewed run/attempt/source/index')


def verify_web_assets(archive, prefix, dist, identity, asset_index_sha256):
    seal_name = prefix + '.web-assets.json'
    require(isinstance(asset_index_sha256, str) and SHA.fullmatch(asset_index_sha256) and
            stream_identity(archive, seal_name)['sha256'] == asset_index_sha256,
            'Bundled asset index differs from explicitly reviewed build')
    seal = read_json(archive, seal_name)
    require(isinstance(seal, dict) and {k: v for k, v in seal.items() if k != 'files'} == identity,
            'Bundled Web identity differs from reviewed source')
    expected = {}
    for path in dist.rglob('*'):
        require(not path.is_symlink(), 'Canonical Web build cannot contain symlinks')
        if path.is_file():
            name = path.relative_to(dist).as_posix()
            require(valid_relative(name) and name != '.web-assets.json', 'Invalid canonical payload path')
            expected[name] = {'bytes': path.stat().st_size, 'sha256': digest_file(path)}
    files = seal.get('files')
    require(isinstance(files, dict) and all(isinstance(value, dict) and
            set(value) == {'bytes', 'sha256'} and type(value['bytes']) is int and value['bytes'] >= 0 and
            isinstance(value['sha256'], str) and SHA.fullmatch(value['sha256']) for value in files.values()),
            'Invalid pinned asset index entries')
    require(bool(expected) and len(expected) < 10000 and files == expected,
            'Asset index differs from exact reviewed Web build')
    packaged = {n[len(prefix):] for n in archive.namelist() if n.startswith(prefix) and not n.endswith('/')}
    require(packaged == set(expected) | {'.web-assets.json'}, 'Unexpected bundled Web files')
    for name, asset in expected.items():
        require(stream_identity(archive, prefix + name) == asset, 'Bundled Web SHA/size mismatch: ' + name)
    return expected


def verify_package(path, dist, identity, runtime_source, asset_index_sha256):
    with zipfile.ZipFile(path) as archive:
        names = inspect_zip(archive)
        require(archive.testzip() is None, 'Windows package CRC failed')
        exe = 'Shikuang/Shikuang.exe'
        require(exe in names, 'Missing Windows launcher')
        with archive.open(exe) as stream:
            header = stream.read(4096)
        require(header[:2] == b'MZ' and len(header) >= 64, 'Launcher is not PE')
        offset = struct.unpack_from('<I', header, 60)[0]
        require(offset + 6 <= len(header) and header[offset:offset + 4] == b'PE\0\0' and
                struct.unpack_from('<H', header, offset + 4)[0] == 0x8664, 'Launcher is not Windows x64')
        base = 'Shikuang/_internal/'
        expected = verify_web_assets(archive, base + 'web/', dist, identity, asset_index_sha256)
        model = read_json(archive, base + 'web/model-manifest.json')
        require(model['version'] == identity['model_version'], 'Model version mismatch')
        for row in model['models']:
            require(expected.get(row['path']) == {'bytes': row['bytes'], 'sha256': row['sha256']}, 'Model seal mismatch')
        refs = read_json(archive, base + 'web/data/reference-manifest.json')
        for row in refs['files']:
            require(expected.get(row['path']) == {'bytes': row['bytes'], 'sha256': row['sha256']}, 'Reference seal mismatch')
        gallery = read_json(archive, base + 'web/data/reviewed-photo-assets.json')
        require(len(gallery['assets']) == 117, 'Missing reviewed photo assets')
        for row in gallery['assets']:
            require(expected.get(row['path']) == {'bytes': row['bytes'], 'sha256': row['sha256']}, 'Gallery seal mismatch')
        runtime = read_json(archive, base + 'vendor/runtime-assets.json')
        require(runtime.get('schema_version') == 1 and runtime.get('version') == runtime_source['version'] and
                runtime.get('architecture') == runtime_source['architecture'] == 'x64' and
                runtime.get('archive_sha256') == runtime_source['sha256'], 'Fixed runtime source identity mismatch')
        require(len(runtime.get('files', {})) == 256 and 'msedgewebview2.exe' in runtime['files'], 'Incomplete fixed runtime')
        runtime_bytes = verify_seal(archive, base + 'vendor/webview2/', runtime['files'])
        return {'webAssets': len(expected), 'launcherSha256': stream_identity(archive, exe)['sha256'], 'reviewedPhotos': 117, 'zipEntries': len(names), 'crcPassed': True,
                'launcherMachine': 'PE-x64', 'fixedRuntime': {
                    'version': runtime['version'], 'files': len(runtime['files']), 'expandedBytes': runtime_bytes,
                    'archiveBytes': runtime_source['bytes'], 'archiveSha256': runtime_source['sha256'],
                    'sourceURL': runtime_source['url'], 'license': runtime_source['license'],
                    'verification': 'All packaged runtime bytes match the seal from the guarded successful build; the build verified official CAB hash and Microsoft executable signature.'}}


def guard(args):
    require(args.evidence.is_file() and 0 < args.evidence.stat().st_size <= 65536, 'Missing or oversized reviewed build evidence')
    raw = args.evidence.read_bytes()
    evidence = checked_evidence(raw, args.expected_evidence_sha256)
    repository, run_id = evidence['repository'], evidence['run_id']
    run = api(f'repos/{repository}/actions/runs/{run_id}')
    validate_run(run, evidence)
    artifacts = api(f'repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100')
    require(artifacts['total_count'] < 100, 'Too many build artifacts')
    artifact = select_artifact(artifacts['artifacts'], evidence)
    validate_release(matching_release(repository, evidence['tag']), evidence['tag'], evidence['source_commit'])
    validate_tag(repository, evidence['tag'], evidence['source_commit'])
    context = {'schema_version': 2, 'evidence_json': raw.decode('utf-8'), 'run': run, 'artifact': artifact}
    args.output.write_text(json.dumps(context, indent=2) + '\n', encoding='utf-8')
    if args.github_output:
        with args.github_output.open('a') as output:
            output.write('source_commit=' + evidence['source_commit'] + '\nweb_commit=' + evidence['web_commit'] + '\n')
    print('Guarded reviewed successful build ' + str(run_id) + '; no release mutation')



def verify_download(args):
    context = load_context(args.context, args.expected_evidence_sha256)
    validate_run(context['run'], context)
    for path, commit in ((args.desktop_source, context['source_commit']), (args.web_dist.parent, context['web_commit'])):
        actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        require(actual == commit, 'Verifier checkout is not the exact guarded source')
        require(subprocess.run(['git', '-C', str(path), 'diff', '--quiet', 'HEAD', '--'],
                               check=False).returncode == 0, 'Guarded tracked source has local changes')
    identity = json.loads((args.desktop_source / 'web-source.json').read_text())
    require(identity.get('schema_version') == 1 and identity.get('web_commit') == context['web_commit'] and identity.get('app_version') == context['app_version'], 'Unexpected source pin')
    runtime_source = json.loads((args.desktop_source / 'vendor-sources.json').read_text())['webview2']
    zip_name = package_name(context)
    artifact = context['artifact']
    args.output.mkdir(parents=True, exist_ok=True)
    outer = args.output / 'actions-artifact.zip'
    with outer.open('wb') as output:
        subprocess.run(['gh', 'api', f'repos/{context["repository"]}/actions/artifacts/{artifact["id"]}/zip'],
                       stdout=output, check=True, timeout=1200)
    require(outer.stat().st_size == artifact['size_in_bytes'] and
            'sha256:' + digest_file(outer) == artifact['digest'], 'Downloaded artifact digest/size mismatch')
    with zipfile.ZipFile(outer) as archive:
        names = inspect_zip(archive)
        require('artifacts/' + zip_name in names, 'Expected unique Windows package is absent')
        package = args.output / zip_name
        with archive.open('artifacts/' + zip_name) as source, package.open('wb') as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                target.write(chunk)
        builder_sha = checksum_from_text(archive.read('artifacts/' + zip_name + '.sha256.txt').decode('utf-8-sig'))
        require(digest_file(package) == builder_sha, 'Windows ZIP differs from builder checksum')
        require(read_json(archive, 'web-source.json') == identity, 'Artifact Web pin differs from tested source')
        provenance = read_json(archive, 'artifacts/windows-build-provenance.json')
        validate_build_provenance(provenance, context)
        package_report = verify_package(package, args.web_dist, identity, runtime_source, context['asset_index_sha256'])
        native_v2 = v2.validate_bundle(archive, identity, args.desktop_source, read_json, stream_identity, package_report['launcherSha256'], validate_network)
        proof = read_json(archive, 'artifacts/windows-runtime/network-proof.json')
        validate_network(proof)
        runtime = {phase: {**validate_runtime(read_json(archive, 'artifacts/windows-runtime/' + phase + '.json'), phase, identity),
                           **native_v2[phase]} for phase in ('write', 'restart')}
        # The actual artifact has no PNG: dimensions/decode gates are not screenshots.
        screenshots = [n for n in names if n.lower().endswith(('.png', '.jpg'))]
        require(not screenshots and not any(p['screenshotsPresent'] for p in runtime.values()),
                'This reviewed build has no screenshot artifacts; do not imply visual evidence')
    report = {'schema_version': 2, 'passed': True, 'sourceCommit': context['source_commit'],
              'appVersion': context['app_version'], 'runAttempt': context['run_attempt'],
              'reviewedEvidenceSha256': args.expected_evidence_sha256, 'assetIndexSha256': context['asset_index_sha256'],
              'webCommit': context['web_commit'], 'tag': context['tag'], 'buildRun': context['run']['html_url'],
              'runId': context['run']['id'], 'artifactId': artifact['id'], 'artifactDigest': artifact['digest'],
              'package': {'name': zip_name, 'bytes': package.stat().st_size, 'sha256': builder_sha},
              'assets': package_report, 'runtime': runtime, 'network': proof,
              'screenshotsPresent': False, 'accuracyClaim': False,
              'platformScope': 'Actual two-process packaged launch on GitHub Windows Server 2022 x64, fixed WebView2 155. No manual camera/save-dialog or Windows 10/11 machine test; not a blind accuracy/Kappa result.',
              'publication': 'draft prerelease only; existing macOS assets are preserved'}
    (args.output / 'windows-gate-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'SHA256SUMS-windows.txt').write_text(builder_sha + '  ' + zip_name + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'sha256': builder_sha, 'bytes': package.stat().st_size, 'screenshotsPresent': False}))


def upload(args):
    context = load_context(args.context, args.expected_evidence_sha256)
    run = api(f'repos/{context["repository"]}/actions/runs/{context["run"]["id"]}')
    validate_run(run, context)
    artifact = api(f'repos/{context["repository"]}/actions/artifacts/{context["artifact"]["id"]}')
    select_artifact([artifact], context)
    validate_tag(context['repository'], context['tag'], context['source_commit'])
    report = json.loads((args.directory / 'windows-gate-report.json').read_text())
    zip_name = package_name(context)
    package = args.directory / zip_name
    require(report.get('schema_version') == 2 and report.get('passed') is True and
            report.get('appVersion') == context['app_version'] and report.get('runAttempt') == context['run_attempt'] and
            report.get('reviewedEvidenceSha256') == args.expected_evidence_sha256 and
            report.get('assetIndexSha256') == context['asset_index_sha256'] and
            report.get('artifactDigest') == context['artifact']['digest'] and
            report.get('sourceCommit') == context['source_commit'] and
            report.get('webCommit') == context['web_commit'] and report.get('runId') == run['id'] and
            report.get('artifactId') == context['artifact']['id'] and
            report.get('package') == {'name': zip_name, 'bytes': package.stat().st_size, 'sha256': digest_file(package)},
            'Verification report/package identity changed before upload')
    require((args.directory / 'SHA256SUMS-windows.txt').read_text() ==
            digest_file(package) + '  ' + zip_name + '\n', 'Windows checksum file changed before upload')
    repository, tag = context['repository'], context['tag']
    release = matching_release(repository, tag)
    validate_release(release, tag, context['source_commit'])
    if release is None:
        release = api(f'repos/{repository}/releases', 'POST', {
            'tag_name': tag, 'target_commitish': context['source_commit'], 'name': '识矿桌面版 2.5 预览',
            'draft': True, 'prerelease': True, 'body': 'Windows verified artifact staging. Publication requires separate release review.'})
        validate_release(release, tag, context['source_commit'])
    preserved_assets = {a['id']: (a['name'], a['size'], a.get('digest')) for a in release.get('assets', [])}
    for name in (zip_name, 'SHA256SUMS-windows.txt', 'windows-gate-report.json'):
        current = api(f'repos/{repository}/releases/{release["id"]}')
        validate_release(current, tag, context['source_commit'])
        local = args.directory / name
        existing = [a for a in current.get('assets', []) if a.get('name') == name]
        require(len(existing) <= 1, 'Ambiguous existing Windows asset')
        if existing:
            require(existing[0].get('size') == local.stat().st_size and
                    existing[0].get('digest') == 'sha256:' + digest_file(local), 'Existing Windows asset differs; refusing to overwrite')
            continue
        # Address the validated release ID directly; draft by-tag lookup is unreliable.
        # No clobber, body update, delete, or publication API is ever used.
        upload_url = f'https://uploads.github.com/repos/{repository}/releases/{release["id"]}/assets?name={quote(name)}'
        result = subprocess.run(['gh', 'api', '-X', 'POST', upload_url, '-H',
                                 'Content-Type: application/octet-stream', '--input', str(local)],
                                capture_output=True, text=True, check=True, timeout=1200)
        asset = json.loads(result.stdout)
        require(asset.get('name') == name and asset.get('size') == local.stat().st_size and
                asset.get('digest') == 'sha256:' + digest_file(local), 'Uploaded asset identity mismatch')
    final = api(f'repos/{repository}/releases/{release["id"]}')
    validate_release(final, tag, context['source_commit'])
    assets = {a['id']: (a['name'], a['size'], a.get('digest')) for a in final.get('assets', [])}
    require(all(assets.get(asset_id) == value for asset_id, value in preserved_assets.items()),
            'An existing platform asset changed during staging')
    for name in (zip_name, 'SHA256SUMS-windows.txt', 'windows-gate-report.json'):
        local = args.directory / name
        matching = [a for a in final.get('assets', []) if a.get('name') == name]
        require(len(matching) == 1 and matching[0].get('size') == local.stat().st_size and
                matching[0].get('digest') == 'sha256:' + digest_file(local), 'Final Windows asset identity mismatch')
    print('Uploaded Windows assets to draft release ' + str(release['id']) + '; not published')


def record_build(args):
    """Record only this future actual CI build; never retrofit an old artifact."""
    run_id, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    commit, repository = os.environ.get('GITHUB_SHA', ''), os.environ.get('GITHUB_REPOSITORY', '')
    require(run_id.isdigit() and int(run_id) > 0 and attempt.isdigit() and int(attempt) > 0 and
            repository == REPOSITORY and COMMIT.fullmatch(commit), 'Actual CI run identity is required')
    actual = subprocess.check_output(['git', '-C', str(args.source_root), 'rev-parse', 'HEAD'], text=True).strip()
    require(actual == commit, 'Build provenance source differs from actual CI checkout')
    identity = json.loads((args.source_root / 'web-source.json').read_text(encoding='utf-8'))
    index = args.source_root / 'web/.web-assets.json'
    seal = json.loads(index.read_text(encoding='utf-8'))
    require(identity.get('schema_version') == 1 and
            {key: value for key, value in seal.items() if key != 'files'} == identity and
            isinstance(seal.get('files'), dict) and bool(seal['files']), 'Build resource identity/index mismatch')
    proof = {'schema_version': 1, 'repository': repository, 'run_id': int(run_id),
             'run_attempt': int(attempt), 'source_commit': commit, 'web_commit': identity['web_commit'],
             'app_version': identity['app_version'], 'asset_index_sha256': digest_file(index)}
    with args.output.open('x', encoding='utf-8') as output:
        output.write(json.dumps(proof, indent=2) + '\n')
    print('Recorded current CI build provenance; no release mutation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('record-build')
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.set_defaults(action=record_build)
    p = sub.add_parser('guard')
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--expected-evidence-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--github-output', type=Path)
    p.set_defaults(action=guard)
    p = sub.add_parser('verify-download')
    p.add_argument('--context', type=Path, required=True)
    p.add_argument('--expected-evidence-sha256', required=True)
    p.add_argument('--desktop-source', type=Path, required=True)
    p.add_argument('--web-dist', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.set_defaults(action=verify_download)
    p = sub.add_parser('upload')
    p.add_argument('--context', type=Path, required=True)
    p.add_argument('--expected-evidence-sha256', required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.set_defaults(action=upload)
    args = parser.parse_args()
    args.action(args)


if __name__ == '__main__':
    main()
