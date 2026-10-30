"""Upgrade of ca/desktop/run_app.py, retaining the Python pywebview shell."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop.runtime import AssetServer, DesktopError, InstanceLock, persistent_profile, prepare_fixed_runtime, require_writable, resource_root, state_directory, verify_bundle


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_receipt(path: Path, document: dict):
    """Publish a small complete receipt; a watcher must never read partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def show_error(message: str):
    # No fallback browser: it might connect to an unrelated service on port 8000.
    print('识矿：' + message, file=sys.stderr)
    if sys.platform == 'darwin':
        subprocess.run(['osascript', '-e', 'on run argv\ndisplay alert "识矿无法启动" message (item 1 of argv) as critical\nend run', message], check=False)
    elif sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, '识矿无法启动', 0x10)


def main(argv=None) -> int:
    python_entry_utc = utc_now()
    parser = argparse.ArgumentParser(description='识矿 · 矿物识别')
    parser.add_argument('--verify-only', action='store_true', help='Verify bundled assets without opening a window')
    parser.add_argument('--serve-only', action='store_true', help='Local development health/server diagnostic')
    parser.add_argument('--smoke-report', type=Path, help='Run fixed real-photo runtime smoke and write a local report')
    parser.add_argument('--smoke-fixtures', type=Path, help='Directory containing approved Quartz/dog smoke JPEGs')
    parser.add_argument('--smoke-phase', choices=['write', 'restart'], default='write')
    parser.add_argument('--smoke-run-id', help='Shared fresh identity for one write/restart smoke pair')
    parser.add_argument('--smoke-write-report', type=Path, help='Prior successful write report; verifies full restart content before any put')
    args = parser.parse_args(argv)
    if args.smoke_run_id and (not args.smoke_report or not re.fullmatch(r'[A-Fa-f0-9-]{16,64}', args.smoke_run_id)):
        parser.error('--smoke-run-id requires --smoke-report and a bounded hex/GUID identity')
    if args.smoke_write_report and (not args.smoke_report or args.smoke_phase != 'restart'):
        parser.error('--smoke-write-report requires --smoke-report --smoke-phase restart')
    if args.smoke_report and args.smoke_phase == 'restart' and (not args.smoke_write_report or not args.smoke_run_id):
        parser.error('restart requires --smoke-run-id and the matching --smoke-write-report; no legacy cleanup')
    if args.smoke_report:
        args.smoke_report = args.smoke_report.resolve()
    if args.smoke_fixtures:
        args.smoke_fixtures = args.smoke_fixtures.resolve()
    if args.smoke_write_report:
        args.smoke_write_report = args.smoke_write_report.resolve()
    host = {'schema': 'shikuang-smoke-host-start-v1', 'run_id': args.smoke_run_id or secrets.token_hex(16),
            'phase': args.smoke_phase, 'launch_id': secrets.token_hex(16), 'pid': os.getpid(),
            'parent_pid': os.getppid(), 'executable': str(Path(sys.executable).resolve()),
            'python_entry_utc': python_entry_utc, 'server_started_utc': None, 'origin': None}
    server = None
    lock = None
    smoke = None
    exit_code = 1
    try:
        root = resource_root()
        manifest = verify_bundle(root)
        if args.verify_only:
            print(json.dumps({'version': manifest['app_version'], 'model_version': manifest['model_version'], 'assets': len(manifest['files']), 'verified': True}))
            exit_code = 0
            return 0
        lock = InstanceLock(state_directory() / 'desktop.lock').acquire()
        require_writable(persistent_profile())
        server = AssetServer(root, manifest).start()
        host.update(server_started_utc=utc_now(), origin=server.origin)
        if args.smoke_report:
            write_receipt(Path(str(args.smoke_report) + '.host-start.json'), host)
        if args.serve_only:
            print(json.dumps({'origin': server.origin, 'version': manifest['app_version']}), flush=True)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
            exit_code = 0
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
            smoke = Smoke(window, args.smoke_report, args.smoke_fixtures, args.smoke_phase, manifest,
                          write_report=args.smoke_write_report, run_id=host['run_id'], host=host)
        webview.start(func=smoke.run if smoke else None, private_mode=False,
            storage_path=str(persistent_profile()), gui='edgechromium' if sys.platform == 'win32' else None,
            localization={'global.quitConfirmation': '确认关闭识矿？', 'global.ok': '确定', 'global.cancel': '取消', 'global.saveFile': '保存备份'})
        if smoke and not smoke.success:
            return 1
        exit_code = 0
        return 0
    except Exception as error:
        if args.smoke_report:
            write_receipt(args.smoke_report, {'passed': False, 'failure': str(error), 'phase': args.smoke_phase,
                                             'run_id': host['run_id'], 'host': host})
        if args.verify_only or args.serve_only or args.smoke_report:
            print('识矿：' + str(error), file=sys.stderr)
        else:
            show_error(str(error))
        return 1
    finally:
        cleanup_errors = []
        server_stopped = False
        lock_closed = False
        if server:
            try:
                server.stop()
                server_stopped = True
            except Exception as error:
                cleanup_errors.append('Asset server cleanup failed: ' + str(error))
        if lock:
            try:
                lock.close()
                lock_closed = True
            except Exception as error:
                cleanup_errors.append('Instance lock cleanup failed: ' + str(error))
        if cleanup_errors:
            exit_code = 1
        if args.smoke_report:
            write_receipt(Path(str(args.smoke_report) + '.host-stop.json'),
                          {**host, 'schema': 'shikuang-smoke-host-stop-v1', 'shutdown_at_utc': utc_now(),
                           'server_stop_completed': server_stopped, 'instance_lock_closed': lock_closed,
                           'exit_code': exit_code, 'cleanup_errors': cleanup_errors})
        if cleanup_errors:
            raise DesktopError('; '.join(cleanup_errors))


if __name__ == '__main__':
    raise SystemExit(main())
