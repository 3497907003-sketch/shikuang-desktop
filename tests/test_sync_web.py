from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.sync_web import verify_committed_build


@unittest.skipUnless(shutil.which('node'), 'Node is required to reproduce the shared Web build')
class ImmutableBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='shikuang-immutable-sync-')
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        (self.repo / 'tools').mkdir()
        (self.repo / 'index.html').write_text('<main>矿物识别</main>', encoding='utf-8')
        (self.repo / 'core.js').write_text('window.fixture=true;', encoding='utf-8')
        (self.repo / 'tools/onnx-contract.cjs').write_text('// Contract fixture; no executable model.', encoding='utf-8')
        (self.repo / 'tools/build.cjs').write_text("""
const fs=require('node:fs'),crypto=require('node:crypto');
const names=['index.html','core.js'];
const inputs=names.map(name=>[name,fs.readFileSync(name)]);
const id=crypto.createHash('sha256').update(Buffer.concat([...inputs.map(row=>row[1]),fs.readFileSync('tools/build.cjs')])).digest('hex');
fs.mkdirSync('dist',{recursive:true});
for(const [name,raw] of inputs)fs.writeFileSync('dist/'+name,Buffer.concat([raw,Buffer.from('\\n// shell-'+id)]));
""", encoding='utf-8')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Build fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixed source fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        subprocess.run(['node', 'tools/build.cjs'], cwd=self.repo, check=True, capture_output=True)
        self.dist = self.root / 'dist'
        shutil.copytree(self.repo / 'dist', self.dist)

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True)

    def test_exact_rendered_build_uses_commit_and_ignores_dirty_source(self):
        self.assertNotEqual((self.repo / 'index.html').read_bytes(), (self.dist / 'index.html').read_bytes())
        (self.repo / 'core.js').write_text('dirty working copy', encoding='utf-8')
        identity = verify_committed_build(self.dist, self.repo, self.commit)
        self.assertEqual(set(identity), {'tools/build.cjs', 'tools/onnx-contract.cjs'})
        self.assertEqual(len(identity['tools/build.cjs']['sha256']), 64)

    def test_tampered_rendered_asset_is_rejected(self):
        (self.dist / 'core.js').write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Stale dist asset: core.js'):
            verify_committed_build(self.dist, self.repo, self.commit)

    def test_missing_required_asset_cannot_reseal_a_partial_dist(self):
        (self.dist / 'core.js').unlink()
        with self.assertRaisesRegex(ValueError, 'Immutable shared Web build failed'):
            verify_committed_build(self.dist, self.repo, self.commit)

    def test_extra_committed_file_not_produced_by_builder_is_rejected(self):
        (self.repo / 'unused.js').write_text('unused', encoding='utf-8')
        self.git('add', 'unused.js')
        self.git('-c', 'user.name=Build fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Unused file')
        (self.dist / 'unused.js').write_text('unused', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Dist asset set differs'):
            verify_committed_build(self.dist, self.repo, self.git('rev-parse', 'HEAD').strip())

    def test_build_only_change_rejects_old_rendered_dist(self):
        with (self.repo / 'tools/build.cjs').open('a', encoding='utf-8') as stream:
            stream.write('\n// Changed immutable build inputs\n')
        self.git('add', 'tools/build.cjs')
        self.git('-c', 'user.name=Build fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Build change')
        with self.assertRaisesRegex(ValueError, 'Stale dist asset'):
            verify_committed_build(self.dist, self.repo, self.git('rev-parse', 'HEAD').strip())

    def test_symlink_cannot_substitute_a_bundle_member(self):
        (self.dist / 'core.js').unlink()
        try:
            (self.dist / 'core.js').symlink_to(self.repo / 'core.js')
        except OSError:
            self.skipTest('Creating symlinks is unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'Symlink in bundle'):
            verify_committed_build(self.dist, self.repo, self.commit)


if __name__ == '__main__':
    unittest.main()
