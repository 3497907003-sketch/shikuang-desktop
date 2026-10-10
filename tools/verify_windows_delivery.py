"""Verify a tested Windows artifact, then optionally upload only to a draft.

The uploader's own commit is deliberately distinct from TESTED_SOURCE. No
installer is executed here; runtime evidence comes from the guarded build run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import struct
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

REPOSITORY = '3497907003-sketch/shikuang-desktop'
TESTED_SOURCE = '3f94d9aeae55cd8c1bb342ab3e937c8f2307c262'
WEB_SOURCE = '697be109a20cf5ce2724d68efd670c74949f38b6'
TAG = 'v2.5.0-preview.1'
ARTIFACT_NAME = 'windows-x64-build-and-runtime'
ZIP_NAME = 'Shikuang-2.5.0-preview.1-windows-x64.zip'
ORIGIN = 'http://127.0.0.1:8000'
SHA = re.compile(r'^[a-f0-9]{64}$')
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


def validate_run(run, repository, source_commit):
    require(repository == REPOSITORY and source_commit == TESTED_SOURCE, 'Only the reviewed repository and tested commit are allowed')
    require(run.get('repository', {}).get('full_name') == repository and
            run.get('head_repository', {}).get('full_name') == repository, 'Build repository/fork mismatch')
    require(run.get('head_sha') == source_commit, 'Build was not made from the tested commit')
    require(run.get('path') == '.github/workflows/desktop.yml', 'Run is not the tested desktop build workflow')
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'Build and real runtime gates must have succeeded')
    require(run.get('event') in ('push', 'workflow_dispatch'), 'Unsupported build event')


def select_artifact(artifacts, run_id, source_commit):
    matches = [a for a in artifacts if a.get('name') == ARTIFACT_NAME]
    require(len(matches) == 1, 'Exactly one Windows build artifact is required')
    artifact = matches[0]
    require(artifact.get('expired') is False, 'Windows artifact has expired')
    require(artifact.get('workflow_run', {}).get('id') == run_id and
            artifact.get('workflow_run', {}).get('head_sha') == source_commit, 'Artifact run/source mismatch')
    require(0 < artifact.get('size_in_bytes', 0) < MAX_FILE, 'Unexpected artifact size')
    require(re.fullmatch(r'sha256:[a-f0-9]{64}', artifact.get('digest', '')) is not None,
            'Artifact must have an API SHA256 digest')
    return artifact


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
    require(tag == TAG, 'Only the reviewed preview tag is allowed')
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
    require(report.get('phase') == phase and report.get('web_commit') == WEB_SOURCE and
            report.get('model_manifest_sha256') == identity['model_manifest_sha256'] and
            report.get('app_version') == identity['app_version'], 'Runtime source identity mismatch')
    require(report.get('architecture') == 'AMD64' and report.get('platform') == 'Windows-10-10.0.20348-SP0',
            'Expected the actual Windows Server 2022 x64 runtime')
    require(report.get('physicalNetworkDisabled') is True and report.get('online') is False and
            report.get('accuracy_claim') is False and report.get('origin') == ORIGIN and
            report.get('secureContext') is True, 'Strict offline/origin/accuracy boundary failed')
    rows = report.get('checks', [])
    names = [r.get('name') for r in rows]
    require(len(names) == len(set(names)) and all(r.get('passed') is True for r in rows), 'Invalid runtime checks')
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
    require(isinstance(score, (int, float)) and math.isfinite(score) and 0 <= score <= 1, 'Invalid model score')
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


def verify_package(path, dist, identity, runtime_source):
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
        seal = read_json(archive, base + 'web/.web-assets.json')
        require({k: v for k, v in seal.items() if k != 'files'} == identity, 'Bundled Web identity differs from tested source')
        expected = {p.relative_to(dist).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest_file(p)}
                    for p in dist.rglob('*') if p.is_file()}
        require(len(expected) == 157 and seal.get('files') == expected, 'The 157 assets differ from exact reviewed Web build')
        # The seal file is metadata, not one of the Web build's 157 payload files.
        packaged_web = {n[len(base + 'web/'):] for n in names if n.startswith(base + 'web/') and not n.endswith('/')}
        require(packaged_web == set(expected) | {'.web-assets.json'}, 'Unexpected bundled Web files')
        for name, asset in expected.items():
            require(stream_identity(archive, base + 'web/' + name) == asset, 'Bundled Web SHA/size mismatch: ' + name)
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
        return {'webAssets': 157, 'reviewedPhotos': 117, 'zipEntries': len(names), 'crcPassed': True,
                'launcherMachine': 'PE-x64', 'fixedRuntime': {
                    'version': runtime['version'], 'files': len(runtime['files']), 'expandedBytes': runtime_bytes,
                    'archiveBytes': runtime_source['bytes'], 'archiveSha256': runtime_source['sha256'],
                    'sourceURL': runtime_source['url'], 'license': runtime_source['license'],
                    'verification': 'All packaged runtime bytes match the seal from the guarded successful build; the build verified official CAB hash and Microsoft executable signature.'}}


def guard(args):
    require(str(args.run_id).isdigit(), 'Run ID must be numeric')
    run = api(f'repos/{args.repository}/actions/runs/{args.run_id}')
    validate_run(run, args.repository, args.source_commit)
    artifacts = api(f'repos/{args.repository}/actions/runs/{args.run_id}/artifacts?per_page=100')
    require(artifacts['total_count'] < 100, 'Too many build artifacts')
    artifact = select_artifact(artifacts['artifacts'], int(args.run_id), args.source_commit)
    validate_release(matching_release(args.repository, args.tag), args.tag, args.source_commit)
    validate_tag(args.repository, args.tag, args.source_commit)
    context = {'repository': args.repository, 'source_commit': args.source_commit, 'web_commit': WEB_SOURCE,
               'tag': args.tag, 'run': run, 'artifact': artifact}
    args.output.write_text(json.dumps(context, indent=2) + '\n')
    if args.github_output:
        with args.github_output.open('a') as output:
            output.write('source_commit=' + args.source_commit + '\nweb_commit=' + WEB_SOURCE + '\n')
    print('Guarded successful build ' + str(args.run_id) + '; no release mutation')


def verify_download(args):
    context = json.loads(args.context.read_text())
    validate_run(context['run'], context['repository'], context['source_commit'])
    for path, commit in ((args.desktop_source, TESTED_SOURCE), (args.web_dist.parent, WEB_SOURCE)):
        actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        require(actual == commit, 'Verifier checkout is not the exact guarded source')
    identity = json.loads((args.desktop_source / 'web-source.json').read_text())
    require(identity.get('web_commit') == WEB_SOURCE and identity.get('app_version') == '2.5.0-preview.1', 'Unexpected source pin')
    runtime_source = json.loads((args.desktop_source / 'vendor-sources.json').read_text())['webview2']
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
        require('artifacts/' + ZIP_NAME in names, 'Expected unique Windows package is absent')
        package = args.output / ZIP_NAME
        with archive.open('artifacts/' + ZIP_NAME) as source, package.open('wb') as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                target.write(chunk)
        builder_sha = checksum_from_text(archive.read('artifacts/' + ZIP_NAME + '.sha256.txt').decode('utf-8-sig'))
        require(digest_file(package) == builder_sha, 'Windows ZIP differs from builder checksum')
        require(read_json(archive, 'web-source.json') == identity, 'Artifact Web pin differs from tested source')
        proof = read_json(archive, 'artifacts/windows-runtime/network-proof.json')
        validate_network(proof)
        runtime = {phase: validate_runtime(read_json(archive, 'artifacts/windows-runtime/' + phase + '.json'), phase, identity)
                   for phase in ('write', 'restart')}
        # The actual artifact has no PNG: dimensions/decode gates are not screenshots.
        screenshots = [n for n in names if n.lower().endswith(('.png', '.jpg'))]
        require(not screenshots and not any(p['screenshotsPresent'] for p in runtime.values()),
                'This reviewed build has no screenshot artifacts; do not imply visual evidence')
    package_report = verify_package(package, args.web_dist, identity, runtime_source)
    report = {'schema_version': 1, 'passed': True, 'sourceCommit': context['source_commit'],
              'webCommit': WEB_SOURCE, 'tag': context['tag'], 'buildRun': context['run']['html_url'],
              'runId': context['run']['id'], 'artifactId': artifact['id'], 'artifactDigest': artifact['digest'],
              'package': {'name': ZIP_NAME, 'bytes': package.stat().st_size, 'sha256': builder_sha},
              'assets': package_report, 'runtime': runtime, 'network': proof,
              'screenshotsPresent': False, 'accuracyClaim': False,
              'platformScope': 'Actual two-process packaged launch on GitHub Windows Server 2022 x64, fixed WebView2 155. No manual camera/save-dialog or Windows 10/11 machine test; not a blind accuracy/Kappa result.',
              'publication': 'draft prerelease only; existing macOS assets are preserved'}
    (args.output / 'windows-gate-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'SHA256SUMS-windows.txt').write_text(builder_sha + '  ' + ZIP_NAME + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'sha256': builder_sha, 'bytes': package.stat().st_size, 'screenshotsPresent': False}))


def upload(args):
    context = json.loads(args.context.read_text())
    run = api(f'repos/{context["repository"]}/actions/runs/{context["run"]["id"]}')
    validate_run(run, context['repository'], context['source_commit'])
    validate_tag(context['repository'], context['tag'], context['source_commit'])
    report = json.loads((args.directory / 'windows-gate-report.json').read_text())
    package = args.directory / ZIP_NAME
    require(report.get('passed') is True and report.get('sourceCommit') == context['source_commit'] and
            report.get('webCommit') == WEB_SOURCE and report.get('runId') == run['id'] and
            report.get('artifactId') == context['artifact']['id'] and
            report.get('package') == {'name': ZIP_NAME, 'bytes': package.stat().st_size, 'sha256': digest_file(package)},
            'Verification report/package identity changed before upload')
    require((args.directory / 'SHA256SUMS-windows.txt').read_text() ==
            digest_file(package) + '  ' + ZIP_NAME + '\n', 'Windows checksum file changed before upload')
    repository, tag = context['repository'], context['tag']
    release = matching_release(repository, tag)
    validate_release(release, tag, context['source_commit'])
    if release is None:
        release = api(f'repos/{repository}/releases', 'POST', {
            'tag_name': tag, 'target_commitish': context['source_commit'], 'name': '识矿桌面版 2.5 预览',
            'draft': True, 'prerelease': True, 'body': 'Windows verified artifact staging. Publication requires separate release review.'})
        validate_release(release, tag, context['source_commit'])
    preserved_assets = {a['id']: (a['name'], a['size'], a.get('digest')) for a in release.get('assets', [])}
    for name in (ZIP_NAME, 'SHA256SUMS-windows.txt', 'windows-gate-report.json'):
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
    for name in (ZIP_NAME, 'SHA256SUMS-windows.txt', 'windows-gate-report.json'):
        local = args.directory / name
        matching = [a for a in final.get('assets', []) if a.get('name') == name]
        require(len(matching) == 1 and matching[0].get('size') == local.stat().st_size and
                matching[0].get('digest') == 'sha256:' + digest_file(local), 'Final Windows asset identity mismatch')
    print('Uploaded Windows assets to draft release ' + str(release['id']) + '; not published')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('guard')
    p.add_argument('--repository', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--source-commit', required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--github-output', type=Path)
    p.set_defaults(action=guard)
    p = sub.add_parser('verify-download')
    p.add_argument('--context', type=Path, required=True)
    p.add_argument('--desktop-source', type=Path, required=True)
    p.add_argument('--web-dist', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.set_defaults(action=verify_download)
    p = sub.add_parser('upload')
    p.add_argument('--context', type=Path, required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.set_defaults(action=upload)
    args = parser.parse_args()
    args.action(args)


if __name__ == '__main__':
    main()
