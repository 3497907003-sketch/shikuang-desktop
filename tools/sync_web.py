"""Seal an existing validated web dist. Never train models or alter the web repo."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sync(dist: Path, target: Path, web_ref: str = 'HEAD', source_repo: Path | None = None) -> dict:
    dist = dist.resolve()
    root = target.resolve().parent
    if dist == target.resolve() or target.resolve().is_relative_to(dist):
        raise ValueError('Destination must be outside source dist')
    source_root = source_repo.resolve() if source_repo else dist.parent
    web_commit = subprocess.check_output(['git', '-C', str(source_root), 'rev-parse', '--verify', web_ref + '^{commit}'], text=True).strip()
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
        # Compare to the exact immutable commit, not another agent's in-progress
        # working tree. Every asset in this dist must come from the same source.
        committed = subprocess.check_output(['git', '-C', str(source_root), 'show', web_commit + ':' + name])
        if hashlib.sha256(committed).hexdigest() != digest(path):
            raise ValueError('Stale dist asset: ' + name)
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
                'model_manifest_sha256': digest(dist / 'model-manifest.json'), 'files': files}
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

