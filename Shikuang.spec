# Upgrade of the existing ca/Shikuang.spec; no Torch/PT model branch.
import json
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
identity = json.loads((root / 'web/.web-assets.json').read_text(encoding='utf-8'))
numeric_version = identity['app_version'].split('-', 1)[0]
datas = [(str(root / 'web'), 'web'), (str(root / 'desktop/bridge.js'), 'desktop')]
datas += collect_data_files('webview')
if sys.platform == 'win32':
    if not (root / 'vendor/webview2/msedgewebview2.exe').is_file():
        raise RuntimeError('A complete fixed WebView2 runtime must be bundled for offline Windows use')
    datas.append((str(root / 'vendor/webview2'), 'vendor/webview2'))
    datas.append((str(root / 'vendor/runtime-assets.json'), 'vendor'))

a = Analysis(
    [str(root / 'desktop/run_app.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=['desktop.smoke', 'webview.platforms.edgechromium' if sys.platform == 'win32' else 'webview.platforms.cocoa'], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['torch', 'torchvision', 'timm', 'numpy', 'pandas', 'scipy', 'sklearn',
              'fastapi', 'uvicorn', 'cv2', 'PyQt5', 'PyQt6', 'PySide6'], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Shikuang', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
    icon=str(root / ('build/icons/shikuang.ico' if sys.platform == 'win32' else 'build/icons/shikuang.icns')),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Shikuang')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Shikuang.app', icon=str(root / 'build/icons/shikuang.icns'),
        bundle_identifier='Shikuang', version=numeric_version,
        info_plist={
            'CFBundleDisplayName': '识矿', 'CFBundleName': 'Shikuang',
            'CFBundleShortVersionString': numeric_version, 'CFBundleVersion': numeric_version,
            'ShikuangBuildIdentity': identity['app_version'],
            'NSHighResolutionCapable': True,
            'NSCameraUsageDescription': '拍摄当前矿物标本，用于本机矿物候选分析。',
            'NSMicrophoneUsageDescription': '录制标本视频；识别仅在本机提取画面，声音不用于鉴定。',
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True, 'NSAllowsArbitraryLoadsInWebContent': True},
        })

