"""Upgrade of ca/desktop/run_app.py, retaining the Python pywebview shell."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop.runtime import AssetServer, DesktopError, InstanceLock, persistent_profile, prepare_fixed_runtime, require_writable, resource_root, state_directory, verify_bundle


def show_error(message: str):
    # No fallback browser: it might connect to an unrelated service on port 8000.
    print('识矿：' + message, file=sys.stderr)
    if sys.platform == 'darwin':
        subprocess.run(['osascript', '-e', 'on run argv\ndisplay alert "识矿无法启动" message (item 1 of argv) as critical\nend run', message], check=False)
    elif sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, '识矿无法启动', 0x10)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='识矿 · 矿物识别')
    parser.add_argument('--verify-only', action='store_true', help='Verify bundled assets without opening a window')
    parser.add_argument('--serve-only', action='store_true', help='Local development health/server diagnostic')
    parser.add_argument('--smoke-report', type=Path, help='Run fixed real-photo runtime smoke and write a local report')
    parser.add_argument('--smoke-fixtures', type=Path, help='Directory containing approved Quartz/dog smoke JPEGs')
    parser.add_argument('--smoke-phase', choices=['write', 'restart'], default='write')
    args = parser.parse_args(argv)
    if args.smoke_report:
        args.smoke_report = args.smoke_report.resolve()
    if args.smoke_fixtures:
        args.smoke_fixtures = args.smoke_fixtures.resolve()
    server = None
    lock = None
    smoke = None
    try:
        root = resource_root()
        manifest = verify_bundle(root)
        if args.verify_only:
            print(json.dumps({'version': manifest['app_version'], 'model_version': manifest['model_version'], 'assets': len(manifest['files']), 'verified': True}))
            return 0
        lock = InstanceLock(state_directory() / 'desktop.lock').acquire()
        require_writable(persistent_profile())
        server = AssetServer(root, manifest).start()
        if args.serve_only:
            print(json.dumps({'origin': server.origin, 'version': manifest['app_version']}), flush=True)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
            return 0
        import webview
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['ALLOW_DOWNLOADS'] = True  # Preserve Web UI JSON backup export.
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        if sys.platform == 'win32':
            runtime = prepare_fixed_runtime(root)
            webview.settings['WEBVIEW2_RUNTIME_PATH'] = str(runtime)
        window = webview.create_window('识矿 · 矿物识别', server.origin + '/', width=1000, height=820,
            min_size=(390, 620), background_color='#edf2f4', text_select=True, zoomable=True)
        if window is None:
            raise DesktopError('桌面窗口未能创建。请检查系统 WebView。')
        # Trusted reinjection repairs a cached old index even when it lacks the new
        # script. Only our exclusively bound, validated origin is eligible.
        def loaded():
            if (window.get_current_url() or '').split('#', 1)[0].split('?', 1)[0] in (server.origin + '/', server.origin + '/index.html'):
                window.run_js(server.bridge)
        window.events.loaded += loaded
        if args.smoke_report:
            if not args.smoke_fixtures:
                raise DesktopError('烟测需要真实照片 fixture 目录。')
            from desktop.smoke import Smoke
            smoke = Smoke(window, args.smoke_report, args.smoke_fixtures, args.smoke_phase, manifest)
        webview.start(func=smoke.run if smoke else None, private_mode=False,
            storage_path=str(persistent_profile()), gui='edgechromium' if sys.platform == 'win32' else None,
            localization={'global.quitConfirmation': '确认关闭识矿？', 'global.ok': '确定', 'global.cancel': '取消', 'global.saveFile': '保存备份'})
        if smoke and not smoke.success:
            return 1
        return 0
    except Exception as error:
        if args.smoke_report:
            args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_report.write_text(json.dumps({'passed': False, 'failure': str(error)}, ensure_ascii=False, indent=2), encoding='utf-8')
        if args.verify_only or args.serve_only or args.smoke_report:
            print('识矿：' + str(error), file=sys.stderr)
        else:
            show_error(str(error))
        return 1
    finally:
        if server:
            server.stop()
        if lock:
            lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
