"""Read-only loopback asset server. No Python ML / upload / credential API."""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ORIGIN_PORT = 8000  # Preserve the published 1.0 desktop origin/history partition.
HEALTH_PATH = '/__shikuang_health__'


class DesktopError(RuntimeError):
    pass


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])).resolve()


def state_directory() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'Shikuang'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/识矿'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'shikuang'


def persistent_profile() -> Path:
    # The Cocoa backend uses WKWebsiteDataStore.defaultDataStore(), scoped to the
    # app identity, ignoring storage_path. Keep CFBundleIdentifier=Shikuang in spec.
    # Windows keeps the old pywebview default folder, rather than moving its data.
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'pywebview'
    return Path.home() / '.pywebview'


def require_writable(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ('.shikuang-write-' + secrets.token_hex(8))
        with probe.open('xb') as stream:
            stream.write(b'ok')
        probe.unlink()
    except OSError as error:
        raise DesktopError('无法写入本机资料目录。请检查目录权限和剩余空间；原记录未删除。') from error


class InstanceLock:
    """OS-released lock, no stale PID guessing or lock-file deletion races."""
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def acquire(self):
        require_writable(self.path.parent)
        stream = self.path.open('a+b')
        try:
            if sys.platform == 'win32':
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise DesktopError('识矿已在运行。请先返回已打开的窗口，或关闭它后再启动。') from error
        self.stream = stream
        return self

    def close(self):
        if self.stream:
            if sys.platform == 'win32':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()
            self.stream = None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_path(root: Path, name: str) -> Path:
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or '\\' in name or any(x in ('', '.', '..') for x in name.split('/')):
        raise DesktopError('资源清单包含不安全路径。请重新安装完整安装包。')
    path = root.joinpath(*pure.parts)
    # No symlinked files/directories may escape or substitute the sealed assets.
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(root)) or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise DesktopError('安装资源缺失或路径异常。请重新安装完整安装包。')
    return path


def verify_bundle(root: Path) -> dict:
    # PyInstaller macOS BUNDLE intentionally links Frameworks/web to Resources/web.
    # Resolve that package-level link; asset descendants remain strict and sealed.
    web = (root / 'web').resolve()
    try:
        manifest = json.loads((web / '.web-assets.json').read_text(encoding='utf-8'))
        if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict):
            raise ValueError('asset manifest schema')
        required = {'index.html', 'app.js', 'core.js', 'inference.js', 'engine-worker.js',
                    'vendor/ort.min.js', 'vendor/ort-wasm-simd-threaded.mjs', 'vendor/ort-wasm-simd-threaded.wasm',
                    'model-manifest.json', 'version.json', 'data/classes.js', 'data/minerals.js',
                    'data/catalog.json', 'data/crystals.json', 'data/image-sources.json',
                    'data/reference-manifest.json', 'data/licenses/ima-cnmnc-2026-09.txt'}
        if not required.issubset(manifest['files']):
            raise ValueError('asset manifest incomplete')
        for name, identity in manifest['files'].items():
            file = safe_path(web, name)
            if file.stat().st_size != identity['bytes'] or not hmac.compare_digest(sha256(file), identity['sha256']):
                raise ValueError('asset identity ' + name)
        model_bytes = (web / 'model-manifest.json').read_bytes()
        model = json.loads(model_bytes)
        if hashlib.sha256(model_bytes).hexdigest() != manifest['model_manifest_sha256']:
            raise ValueError('model manifest mismatch')
        for row in model['models']:
            if manifest['files'].get(row['path']) != {'bytes': row['bytes'], 'sha256': row['sha256']}:
                raise ValueError('model outside sealed bundle')
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DesktopError('安装包资源校验失败。请重新安装同一版完整安装包；本机记录不会被清除。') from error


def prepare_fixed_runtime(root: Path) -> Path:
    runtime = (root / 'vendor/webview2').resolve()
    if str(runtime).startswith('\\\\'):
        raise DesktopError('WebView2 不能从网络共享目录运行。请把完整识矿文件夹复制到本机后打开。')
    try:
        identity = json.loads((root / 'vendor/runtime-assets.json').read_text(encoding='utf-8-sig'))
        if identity.get('schema_version') != 1 or 'msedgewebview2.exe' not in identity['files']:
            raise ValueError('Fixed runtime manifest')
        for name, item in identity['files'].items():
            file = safe_path(runtime, name)
            if file.stat().st_size != item['bytes'] or sha256(file) != item['sha256']:
                raise ValueError('Fixed runtime identity mismatch')
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise DesktopError('Windows 安装包的内置 WebView2 缺失或校验失败，请重新下载完整离线包。') from error
    # Microsoft documents this per-deployment ACL for unpackaged Fixed Version
    # 120+ on Windows 10. ZIP extraction does not preserve the build machine ACL.
    try:
        for sid in ['*S-1-15-2-2:(OI)(CI)(RX)', '*S-1-15-2-1:(OI)(CI)(RX)']:
            subprocess.run(['icacls.exe', str(runtime), '/grant', sid, '/T', '/Q'], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError) as error:
        raise DesktopError('无法设置内置浏览器的读取权限。请把完整识矿文件夹放在本机有写入权限的目录后重试。历史记录保留。') from error
    return runtime


class AssetServer(ThreadingHTTPServer):
    # Do not reuse an occupied port; keep shutdown bounded if a client stalls.
    # POSIX reuse permits immediate restart after our own TIME_WAIT sockets; it
    # does not permit another listening server (SO_REUSEPORT is never enabled).
    # Windows instead uses EXCLUSIVEADDRUSE: its REUSEADDR can hijack a listener.
    allow_reuse_address = sys.platform != 'win32'
    daemon_threads = True
    block_on_close = False

    def __init__(self, root: Path, manifest: dict, port: int = ORIGIN_PORT):
        self.root = (root / 'web').resolve()
        self.manifest = manifest
        self.nonce = secrets.token_hex(32)
        self.thread = None
        try:
            super().__init__(('127.0.0.1', port), AssetHandler)
        except OSError as error:
            raise DesktopError('识矿的本机端口 8000 被其他程序占用。请关闭占用程序后重试；未连接任何未知服务，原记录保留。') from error
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])
        try:
            self.bridge = (root / 'desktop/bridge.js').read_text(encoding='utf-8').replace('__DESKTOP_METADATA__', json.dumps({
                'version': manifest['app_version'], 'modelVersion': manifest['model_version'],
                'modelManifestSha256': manifest['model_manifest_sha256'], 'webCommit': manifest['web_commit'],
                'origin': self.origin,
            }, ensure_ascii=False))
        except (OSError, KeyError):
            self.server_close()
            raise DesktopError('桌面启动组件缺失，请重新安装完整包。')

    def start(self, timeout: float = 5):
        self.thread = threading.Thread(target=self.serve_forever, kwargs={'poll_interval': 0.1}, name='shikuang-assets', daemon=True)
        self.thread.start()
        opener = build_opener(ProxyHandler({}))  # Never route local health via a system proxy.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with opener.open(Request(self.origin + HEALTH_PATH, headers={'Cache-Control': 'no-store'}), timeout=0.5) as response:
                    data = json.loads(response.read(1024))
                if data.get('nonce') == self.nonce and data.get('version') == self.manifest['app_version']:
                    return self
                raise DesktopError('本机服务身份校验失败。请关闭识矿后重试。')
            except (URLError, TimeoutError, socket.timeout):
                time.sleep(0.03)
        self.stop()
        raise DesktopError('识矿本机服务未能在 5 秒内就绪。请检查系统权限后重试。')

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def stop(self):
        if self.thread:
            self.shutdown()
            self.thread.join(timeout=2)
            self.thread = None
        self.server_close()


class AssetHandler(BaseHTTPRequestHandler):
    server_version = 'ShikuangDesktop'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        # URLs and user content must not leak through request logs.
        pass

    def do_HEAD(self):
        self.serve(head=True)

    def do_GET(self):
        self.serve(head=False)

    def serve(self, head: bool):
        self.close_connection = True
        if self.headers.get('Host') != '127.0.0.1:' + str(self.server.server_address[1]):
            self.send_error(421, 'Misdirected Request')
            return
        path = unquote(urlsplit(self.path).path)
        if path == HEALTH_PATH:
            data = json.dumps({'nonce': self.server.nonce, 'version': self.server.manifest['app_version']}).encode()
            self.payload(data, 'application/json', head)
            return
        if path == '/__shikuang_desktop__.js':
            self.payload(self.server.bridge.encode('utf-8'), 'text/javascript; charset=utf-8', head)
            return
        name = 'index.html' if path == '/' else path.removeprefix('/')
        if name not in self.server.manifest['files']:
            self.send_error(404, 'Not Found')
            return
        try:
            file = safe_path(self.server.root, name)
            if name == 'index.html':
                data = file.read_bytes().replace(b'<head>', b'<head><script src="/__shikuang_desktop__.js"></script>', 1)
                self.payload(data, 'text/html; charset=utf-8', head)
            else:
                types = {'.wasm': 'application/wasm', '.mjs': 'text/javascript', '.js': 'text/javascript', '.json': 'application/json', '.onnx': 'application/octet-stream'}
                media = types.get(file.suffix) or mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                self.headers_for(200, file.stat().st_size, media)
                if not head:
                    with file.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            self.wfile.write(block)
        except (OSError, DesktopError):
            if not self.wfile.closed:
                # Browser/worker gets an explicit resource failure, never substitute HTML.
                self.close_connection = True

    def headers_for(self, status: int, length: int, media: str):
        self.send_response(status)
        self.send_header('Content-Type', media)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()

    def payload(self, data: bytes, media: str, head: bool):
        self.headers_for(200, len(data), media)
        if not head:
            self.wfile.write(data)

