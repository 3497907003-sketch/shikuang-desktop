"""Seal an existing validated web dist. Never train models or alter the web repo."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def committed_build_tools(source_root: Path, web_commit: str) -> list[str]:
    """Read the build-only dependency closure from the same immutable commit."""
    manifest = 'tools/build-inputs.json'
    exists = subprocess.run(['git', '-C', str(source_root), 'cat-file', '-e', web_commit + ':' + manifest],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if exists:
        raw = subprocess.check_output(['git', '-C', str(source_root), 'show', web_commit + ':' + manifest])
        if len(raw) > 128 * 1024:
            raise ValueError('Shared build input manifest is too large')
        try:
            tools = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError('Invalid shared build input manifest') from error
        if not isinstance(tools, list) or not 3 <= len(tools) <= 256:
            raise ValueError('Shared build input manifest must list its complete tools')
        if any(not isinstance(name, str) or not re.fullmatch(r'tools/[A-Za-z0-9._/-]+', name)
               or any(part in ('', '.', '..') for part in name.split('/')) for name in tools):
            raise ValueError('Unsafe shared build input path')
        if len(tools) != len(set(tools)):
            raise ValueError('Duplicate shared build input path')
        if not {'tools/build.cjs', 'tools/onnx-contract.cjs', manifest}.issubset(tools):
            raise ValueError('Shared build input manifest omits required inputs')
        return tools
    # Reproduce older commits with their original, dependency-free builder.
    tools = ['tools/build.cjs', 'tools/onnx-contract.cjs']
    helper = 'tools/shell-release.cjs'
    if subprocess.run(['git', '-C', str(source_root), 'cat-file', '-e', web_commit + ':' + helper],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        tools.append(helper)
    return tools


def verify_committed_build(dist: Path, source_root: Path, web_commit: str) -> dict:
    """Reproduce committed build transforms; never trust a rendered dist alone."""
    names = []
    for path in sorted(dist.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in bundle: ' + str(path))
        if not path.is_file():
            continue
        name = path.relative_to(dist).as_posix()
        if any(part.startswith('.') for part in Path(name).parts) or name.endswith(('.pt', '.pkl', '.safetensors')):
            raise ValueError('Unexpected asset: ' + name)
        names.append(name)
    # Compare both inventories as POSIX paths. Path's component ordering puts
    # research/ before research-input.js; string ordering does the reverse.
    names.sort()
    tools = committed_build_tools(source_root, web_commit)
    build_inputs = {}
    # The canonical builder validates its complete asset/model/reference set.
    # Recreating its immutable inputs also detects a missing dist member: the
    # builder cannot succeed with that required source file absent.
    with tempfile.TemporaryDirectory(prefix='shikuang-build-') as temporary:
        staging = Path(temporary)
        for name in names + tools:
            committed = subprocess.check_output(['git', '-C', str(source_root), 'show', web_commit + ':' + name])
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(committed)
            if name in tools:
                build_inputs[name] = {'bytes': len(committed), 'sha256': hashlib.sha256(committed).hexdigest()}
        result = subprocess.run(['node', 'tools/build.cjs'], cwd=staging, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError('Immutable shared Web build failed: ' + (result.stderr or result.stdout)[-4000:])
        expected = staging / 'dist'
        built_names = sorted(path.relative_to(expected).as_posix() for path in expected.rglob('*') if path.is_file())
        if built_names != names:
            raise ValueError('Dist asset set differs from immutable shared Web build')
        for name in names:
            if digest(expected / name) != digest(dist / name):
                raise ValueError('Stale dist asset: ' + name)
    return build_inputs


def sync(dist: Path, target: Path, web_ref: str = 'HEAD', source_repo: Path | None = None) -> dict:
    dist = dist.resolve()
    root = target.resolve().parent
    if dist == target.resolve() or target.resolve().is_relative_to(dist):
        raise ValueError('Destination must be outside source dist')
    source_root = source_repo.resolve() if source_repo else dist.parent
    web_commit = subprocess.check_output(['git', '-C', str(source_root), 'rev-parse', '--verify', web_ref + '^{commit}'], text=True).strip()
    build_inputs = verify_committed_build(dist, source_root, web_commit)
    model = json.loads((dist / 'model-manifest.json').read_text(encoding='utf-8'))
    files = {}
    for path in sorted(dist.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in bundle: ' + str(path))
        if not path.is_file():
            continue
        name = path.relative_to(dist).as_posix()
        if name.startswith('.') or name.endswith(('.pt', '.pkl', '.safetensors')):
            raise ValueError('Unexpected asset: ' + name)
        files[name] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    model_paths = {row['path'] for row in model['models']}
    if {name for name in files if name.startswith('models/')} != model_paths:
        raise ValueError('Bundle model files must exactly match the active manifest')
    for row in model['models']:
        if files.get(row['path']) != {'bytes': row['bytes'], 'sha256': row['sha256']}:
            raise ValueError('Model identity mismatch: ' + row['path'])
    reference = json.loads((dist / 'data/reference-manifest.json').read_text(encoding='utf-8'))
    for row in reference['files']:
        identity = files.get(row['path'])
        if identity != {'bytes': row['bytes'], 'sha256': row['sha256']}:
            raise ValueError('Reference identity mismatch: ' + row['path'])
    manifest = {'schema_version': 1, 'app_version': json.loads((dist / 'version.json').read_text())['version'],
                'web_commit': web_commit, 'model_version': model['version'],
                'model_manifest_sha256': digest(dist / 'model-manifest.json'),
                'web_build_inputs': build_inputs, 'files': files}
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shikuang-web-', dir=root) as temporary:
        staging = Path(temporary) / 'web'
        shutil.copytree(dist, staging)
        (staging / '.web-assets.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Verify the complete staged bundle before replacing a known-good local one.
        import sys
        sys.path.insert(0, str(root))
        from desktop.runtime import verify_bundle
        verify_bundle(Path(temporary))
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))
    (root / 'web-source.json').write_text(json.dumps({k: v for k, v in manifest.items() if k != 'files'}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync validated shared web build into existing desktop shell')
    parser.add_argument('dist', type=Path)
    parser.add_argument('--web-commit', default='HEAD', help='Exact source commit whose dist is being sealed')
    parser.add_argument('--source-repo', type=Path, help='Repository containing the immutable source commit, if dist was built elsewhere')
    parser.add_argument('--target', type=Path, default=Path(__file__).resolve().parents[1] / 'web')
    args = parser.parse_args()
    result = sync(args.dist, args.target, args.web_commit, args.source_repo)
    print(json.dumps({'assets': len(result['files']), 'web_commit': result['web_commit'], 'app_version': result['app_version'], 'model_version': result['model_version']}, ensure_ascii=False))

