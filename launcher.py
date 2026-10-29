"""SinsWorld desktop launcher. Native WebView UI, GitHub Releases updates."""
from __future__ import annotations
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from types import SimpleNamespace

from github_updates import GitHub, UpdateError, Cancelled, sync_mods, checksum
from version import VERSION
from skin_avatar import fetch_skin, texture_url, validate_skin_png, MAX_SKIN_BYTES
import base64

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
BASE_DATA = Path(os.getenv('APPDATA', str(Path.home()))) / 'SinsWorldLauncher'
AUTH_PREVIEW = bool(json.loads((ROOT / 'config.json').read_text('utf-8')).get('preview_build', False))
DATA = BASE_DATA.with_name('SinsWorldLauncherAuthPreview') if AUTH_PREVIEW else BASE_DATA
GAME_DIRECTORY = BASE_DATA / 'game'
CONFIG_FILE = DATA / 'config.json'

def restart_environment():
    # A replacement EXE must unpack its own runtime after the old one exits.
    return {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def load_config():
    defaults = json.loads((ROOT / 'config.json').read_text('utf-8'))
    saved_path = CONFIG_FILE
    if not saved_path.exists() and AUTH_PREVIEW and (BASE_DATA / 'config.json').is_file():
        saved_path = BASE_DATA / 'config.json'
    saved = json.loads(saved_path.read_text('utf-8')) if saved_path.exists() else {}
    if not isinstance(saved, dict):
        raise ValueError('Некорректный config.json')
    # Empty settings from the pre-GitHub version inherit the published channel.
    for key in ('launcher_repository', 'mods_repository'):
        if not saved.get(key):
            saved.pop(key, None)
    # A client JAR pin belongs to this EXE, never to a stale saved configuration.
    saved.pop('auth_bridge_client', None)
    saved.pop('preview_build', None)
    saved.pop('auth_service_locked', None)
    # The published server identity supersedes stale preview settings, including
    # an empty old API URL or an obsolete manually installed certificate.
    if defaults.get('auth_service_locked'):
        for key in ('auth_api_url', 'auth_mode', 'auth_tls_origin', 'auth_tls_certificate', 'server_address', 'launcher_repository', 'launcher_release'):
            saved.pop(key, None)
    # Deliberately do not use previous Google/Yandex settings for any update.
    return {**defaults, **saved}

class LauncherLogHandler(RotatingFileHandler):
    def emit(self, record):
        try:
            super().emit(record)
        finally:
            # Release the file on Windows, including after diagnostic/test runs.
            self.close()


class LauncherAPI:
    def __init__(self, config=None, data=None):
        self._config = config if config is not None else load_config()
        self._config['check_updates'] = True
        self._data = Path(data) if data is not None else DATA
        self._data.mkdir(parents=True, exist_ok=True)
        self._game = self._data / 'game' if data is not None else GAME_DIRECTORY
        self._preview = AUTH_PREVIEW and data is None
        self._window = None
        self._cancel = threading.Event()
        self._guard = threading.Lock()
        self._busy = False
        self._installing = False
        self._restarting = False
        self._started = False
        self._pending_update = None
        self._update_required = False
        self._session = None
        self._auth_guard = threading.RLock()
        self._avatar_guard = threading.Lock()
        self._avatar_pending = False
        self._avatar_attempt = 0.0
        self._avatar_identity = None
        self._avatar_url = None
        self._avatar_data = None
        self._skin_png = None
        self._skin_png_owner = None
        self._game_process = None
        self._launch_state = {'kind': 'idle', 'sequence': 0, 'busy': False}
        self._state_lock = threading.Lock()
        self._log = logging.Logger('SinsWorld')
        self._log.setLevel(logging.INFO)
        handler = LauncherLogHandler(self._data / 'launcher.log', maxBytes=2_000_000, backupCount=1, encoding='utf-8', delay=True)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self._log.addHandler(handler)

    def get_launch_state(self):
        with self._state_lock:
            return {**self._launch_state, 'busy': self._busy}

    def emit(self, kind, **data):
        if kind in ('game_progress', 'game_started', 'game_exited', 'error', 'idle'):
            with self._state_lock:
                self._launch_state = {'kind': kind, **data, 'sequence': self._launch_state['sequence'] + 1}
        if self._window is not None and not self._cancel.is_set():
            try:
                if kind == 'main' or (kind == 'splash' and data.get('state') == 'checking'):
                    width, height = (1180, 664) if kind == 'main' else (460, 560)
                    if sys.platform == 'win32':
                        screen_w = ctypes.windll.user32.GetSystemMetrics(0)
                        screen_h = ctypes.windll.user32.GetSystemMetrics(1)
                        width, height = min(width, screen_w - 32), min(height, screen_h - 48)
                        self._window.resize(width, height)
                        self._window.move(max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 2))
                    else:
                        self._window.resize(width, height)
                self._window.evaluate_js('window.receiveLauncherEvent(' + json.dumps({'kind': kind, **data}, ensure_ascii=False) + ')')
            except Exception:
                self._log.exception('Cannot deliver UI event %s', kind)

    def profile(self):
        if self._session:
            return {'name': self._session['account']['name'], 'id': self._session['account']['id'], 'connected': True,
                    'skin_data': self._avatar_data if self._avatar_identity == self._session['account']['id'] else None}
        return {'name': self._config.get('nickname', ''), 'connected': False}

    def choose_skin_png(self):
        if self._busy or not self._session or self._window is None:
            return {'ok': False, 'error': 'Войдите в аккаунт и дождитесь завершения операции'}
        try:
            import webview
            chosen = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=('PNG skin (*.png)',))
            if not chosen:
                return {'ok': True, 'cancelled': True}
            path = Path(chosen[0])
            if path.suffix.lower() != '.png':
                raise ValueError('Выберите PNG-файл скина')
            with path.open('rb') as source:
                raw = validate_skin_png(source.read(MAX_SKIN_BYTES + 1))
            self._skin_png = base64.b64encode(raw).decode('ascii')
            self._skin_png_owner = self._session['account']['id'] if self._session else None
            return {'ok': True, 'name': path.name, 'skin_data': 'data:image/png;base64,' + self._skin_png}
        except Exception:
            return {'ok': False, 'error': 'Нужен PNG 64×64 или 64×32 размером до 256 КБ'}

    def change_skin(self, mode, value='', variant='classic'):
        if not self._session or self._config.get('auth_mode') != 'authme_bridge':
            return {'ok': False, 'error': 'Сначала войдите в аккаунт сервера'}
        if self._game_process is not None and self._game_process.poll() is None:
            return {'ok': False, 'error': 'Закройте Minecraft перед сменой скина'}
        if mode not in ('name','url','png') or variant not in ('classic','slim'):
            return {'ok': False, 'error': 'Неверный способ установки скина'}
        session = self._session
        account_id = session['account']['id']
        if mode == 'png':
            if self._skin_png_owner != account_id or not self._skin_png:
                return {'ok': False, 'error': 'Сначала выберите PNG-файл'}
            value = self._skin_png
        elif not isinstance(value,str) or len(value)>2048 or not value.strip():
            return {'ok': False, 'error': 'Укажите ник или HTTPS-ссылку на скин'}
        values = {'mode':mode,'value':value.strip(),'variant':variant}
        def work():
            self.emit('skin_change',state='pending',message='Подготовка скина…')
            try:
                result = self._auth_request('/v1/profile/skin/change',values,session['access_token'])
                job = result.get('job')
                if not isinstance(job,str) or len(job)>64:
                    raise ValueError('Сервер не подтвердил запрос скина')
                deadline = time.monotonic()+180
                while True:
                    if self._cancel.wait(2):
                        return
                    current = self._session
                    if not current or current['account']['id'] != account_id:
                        return
                    status = self._auth_request('/v1/profile/skin/status',{'job':job},current['access_token'])
                    if status.get('state') == 'done':
                        try:
                            url=texture_url(status['texture_url'])
                            skin='data:image/png;base64,'+base64.b64encode(fetch_skin(url)).decode('ascii')
                            if self._session and self._session['account']['id']==account_id:
                                with self._avatar_guard:
                                    self._avatar_identity,self._avatar_url,self._avatar_data=account_id,url,skin
                                self.emit('avatar',account_id=account_id,skin_data=skin)
                        except Exception:
                            with self._avatar_guard:
                                self._avatar_url=None;self._avatar_attempt=0
                            self.refresh_avatar()
                        self.emit('skin_change',state='done',message='Скин сохранён для всей сети. В игре он появится при следующем входе.')
                        return
                    if status.get('state') == 'error':
                        raise ValueError(status.get('message','Не удалось установить скин'))
                    if time.monotonic()>deadline:
                        raise ValueError('Сервис ещё обрабатывает скин. Проверьте результат позже; повторная отправка не требуется.')
            except Exception as exc:
                self.emit('skin_change',state='error',message=str(exc)[:300])
        return self._task(work)

    def refresh_avatar(self):
        session = self._session
        if not session or self._config.get('auth_mode') != 'authme_bridge':
            return {'ok': True}
        account_id = session['account']['id']
        with self._avatar_guard:
            if self._avatar_pending or time.monotonic() - self._avatar_attempt < 15:
                return {'ok': True}
            self._avatar_pending = True
            self._avatar_attempt = time.monotonic()

        def work():
            try:
                # Optional endpoint: older server plugins and skin outages must never block login.
                result = self._auth_request('/v1/profile/skin', {}, session['access_token'])
                url = texture_url(result['texture_url']) if result.get('available') else None
                if url and url == self._avatar_url and self._avatar_identity == account_id:
                    skin = self._avatar_data
                else:
                    skin = 'data:image/png;base64,' + base64.b64encode(fetch_skin(url)).decode('ascii') if url else None
                with self._avatar_guard:
                    current = self._session
                    if self._cancel.is_set() or not current or current['account']['id'] != account_id:
                        return
                    self._avatar_identity, self._avatar_url, self._avatar_data = account_id, url, skin
                self.emit('avatar', account_id=account_id, skin_data=skin)
            except Exception:
                # The default/previous head is retained; no tokens or remote payloads in the log.
                pass
            finally:
                with self._avatar_guard:
                    self._avatar_pending = False
        threading.Thread(target=work, name='SinsWorld-avatar', daemon=True).start()
        return {'ok': True}

    def get_settings(self):
        keys = ('ram_mb', 'java_path', 'resolution_width', 'resolution_height', 'fullscreen',
                'check_updates', 'launcher_repository', 'launcher_release', 'mods_repository',
                'mods_release', 'server_address', 'auth_api_url', 'trailer_url', 'auth_mode')
        return {**{k: self._config.get(k) for k in keys}, 'version': VERSION,
                'game_directory': str(self._game), 'selected_server': self._config.get('selected_server', 'sinsworld'), 'profile': self.profile()}

    def save_settings(self, values):
        if self._busy:
            return {'ok': False, 'error': 'Дождитесь завершения текущей операции'}
        try:
            result = dict(self._config)
            for name, low, high in [('ram_mb', 1024, 65536), ('resolution_width', 640, 7680), ('resolution_height', 480, 4320)]:
                val = int(values.get(name, result[name]))
                if not low <= val <= high:
                    raise ValueError('Недопустимое значение: ' + name)
                result[name] = val
            for name in ('launcher_repository', 'mods_repository'):
                value = str(values.get(name, result.get(name, ''))).strip()
                if value:
                    GitHub(value, 'validation')
                result[name] = value
            for name in ('launcher_release', 'mods_release', 'java_path', 'server_address', 'auth_api_url', 'trailer_url'):
                result[name] = str(values.get(name, result.get(name, ''))).strip()
            for name in ('auth_api_url', 'trailer_url'):
                if result[name] and urllib.parse.urlsplit(result[name]).scheme != 'https':
                    raise ValueError(name + ': нужна HTTPS-ссылка')
            if self._config.get('auth_service_locked'):
                for key in ('auth_api_url', 'server_address', 'launcher_repository', 'launcher_release'):
                    if result.get(key) != self._config.get(key):
                        raise ValueError('Адрес сервиса SinsWorld закреплён в этой сборке')
            result['check_updates'] = True
            for name in ('fullscreen',):
                result[name] = bool(values.get(name, result.get(name, True)))
            # RAM upper bound also respects available physical memory where detectable.
            if sys.platform == 'win32':
                class MEMORY(ctypes.Structure):
                    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                                ('total', ctypes.c_ulonglong), ('avail', ctypes.c_ulonglong),
                                ('page', ctypes.c_ulonglong), ('availpage', ctypes.c_ulonglong),
                                ('virtual', ctypes.c_ulonglong), ('availvirtual', ctypes.c_ulonglong),
                                ('extended', ctypes.c_ulonglong)]
                m = MEMORY(); m.length = ctypes.sizeof(m)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) and result['ram_mb'] > m.total // 1024**2 - 1536:
                    raise ValueError('Оставьте минимум 1,5 ГБ памяти для Windows')
            atomic_json(self._data / 'config.json', result)
            if result.get('auth_api_url') != self._config.get('auth_api_url'):
                self._session = None
            self._config = result
            return {'ok': True, 'settings': self.get_settings()}
        except (ValueError, TypeError, OSError, UpdateError) as exc:
            return {'ok': False, 'error': str(exc)}

    def open_game_folder(self):
        folder = self._game
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(str(folder))
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)])
        return {'ok': True}

    def _task(self, action, *, startup=False):
        with self._guard:
            if self._busy:
                return {'ok': False, 'error': 'Операция уже выполняется'}
            self._busy = True
        def worker():
            try:
                action()
            except Cancelled:
                pass
            except Exception as exc:
                self._log.exception('Operation failed')
                if startup:
                    self._installing = False
                self.emit('error', message=(str(exc) or type(exc).__name__) + '\nЖурнал: ' + str(self._data / 'launcher.log'), startup=startup)
            finally:
                self._busy = False
                if self._launch_state['kind'] != 'error':
                    self.emit('idle')
        threading.Thread(target=worker, daemon=True).start()
        return {'ok': True}

    def startup(self):
        if self._started:
            return {'ok': True}
        self._started = True
        managed_update = bool(os.environ.get('SINSWORLD_UPDATE_READY_FILE'))
        self._signal_update_ready()
        if not managed_update:
            self._cleanup_legacy_backup()
        return self.check_launcher()

    def check_launcher(self):
        def work():
            self.emit('splash', state='checking')
            repository = self._config.get('launcher_repository', '')
            if not repository:
                self.emit('configuration', message='Укажите GitHub-репозиторий лаунчера в настройках')
                return
            client = GitHub(repository, self._config.get('launcher_release', 'launcher-current'), cancel=self._cancel)
            info = client.launcher_info(VERSION)
            if info is None:
                self._pending_update = None
                self._update_required = False
                self.emit('splash', state='no-update', progress=100)
                time.sleep(.65)
                self.emit('main')
                self._restore_session()
                return
            manifest, asset = info
            self._pending_update = (manifest, asset)
            self._update_required = True
            self.emit('update_required', version=manifest['version'], message='Эта версия лаунчера устарела. Скачайте новую для продолжения.')
        return self._task(work, startup=True)

    def install_launcher_update(self):
        if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
            return {'ok': False, 'error': 'Автоустановка доступна в собранном Windows EXE'}
        if self._game_process is not None and self._game_process.poll() is None:
            return {'ok': False, 'error': 'Закройте Minecraft перед обновлением лаунчера'}
        def work():
            client = GitHub(self._config['launcher_repository'], self._config['launcher_release'], cancel=self._cancel)
            info = client.launcher_info(VERSION)
            if info is None:
                raise UpdateError('Подходящее обновление ещё не опубликовано')
            manifest, asset = info
            self.emit('splash', state='updating')
            staging = self._data / 'updates' / str(uuid.uuid4())
            staging.mkdir(parents=True)
            target = staging / 'SinsWorldLauncher.exe'
            client.download(asset, target, manifest.get('sha256'), manifest.get('size'),
                            lambda done, total: self.emit('splash_progress', checked=min(done, total - 1), total=total))
            # The helper must survive PyInstaller's temporary directory cleanup.
            helper = staging / 'apply_update.ps1'
            shutil.copy2(ROOT / 'apply_update.ps1', helper)
            self._cancel_check()
            self._installing = True
            self.emit('splash', state='installing', progress=100)
            subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                              '-File', str(helper), '-ParentPid', str(os.getpid()), '-BootloaderPid', str(os.getppid()), '-Source', str(target),
                              '-Target', str(Path(sys.executable).resolve()), '-ExpectedSha256', manifest['sha256'], '-ReadySignal', str(staging / 'ready.json'), '-DeleteBackup'],
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                             cwd=str(staging), env=restart_environment())
            time.sleep(.5)
            self._restarting = True
            self._window.destroy()
        return self._task(work, startup=True)

    def select_server(self, server_id):
        if self._busy or (self._game_process is not None and self._game_process.poll() is None):
            return {'ok': False, 'error': 'Дождитесь завершения игры или текущей операции'}
        if server_id not in ('sinsworld', 'naruto', 'solo'):
            return {'ok': False, 'error': 'Неизвестный сервер'}
        self._config['selected_server'] = server_id
        atomic_json(self._data / 'config.json', self._config)
        return {'ok': True, 'selected_server': server_id}

    def _cleanup_legacy_backup(self):
        # Older updater helpers leave this one reserved sibling backup. Never scan
        # Downloads or remove other EXEs. Runs only after WebView startup is ready.
        if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
            return
        exe = Path(sys.executable).resolve()
        backup = exe.with_name(exe.name + '.previous')
        try:
            if backup.is_symlink() or not backup.is_file():
                return
            with backup.open('rb') as stream:
                if stream.read(2) != b'MZ':
                    return
            backup.unlink()
        except OSError:
            self._log.warning('Old launcher backup is still locked; it was retained')

    def _signal_update_ready(self):
        value = os.environ.pop('SINSWORLD_UPDATE_READY_FILE', '')
        expected = os.environ.pop('SINSWORLD_UPDATE_EXPECTED_HASH', '')
        if not value or not re.fullmatch('[0-9a-f]{64}', expected):
            return
        try:
            target = Path(value).resolve()
            target.relative_to((self._data / 'updates').resolve())
            if target.name == 'ready.json' and checksum(Path(sys.executable)) == expected:
                atomic_json(target, {'sha256': expected})
        except (ValueError, OSError):
            self._log.warning('Cannot acknowledge launcher replacement')

    def _cancel_check(self):
        if self._cancel.is_set():
            raise Cancelled('Операция отменена')

    def continue_to_main(self):
        if self._update_required:
            return {'ok': False, 'error': 'Обновите лаунчер для продолжения'}
        if self._busy or self._installing:
            return {'ok': False, 'error': 'Дождитесь завершения проверки'}
        self.emit('main')
        return {'ok': True}

    def get_news(self):
        repo = self._config.get('launcher_repository', '')
        if not repo:
            return {'ok': True, 'items': []}
        try:
            client = GitHub(repo, self._config.get('launcher_release', 'launcher-current'), cancel=self._cancel)
            assets = client.get_release()
            if 'news.json' not in assets:
                return {'ok': True, 'items': []}
            data = client.json(assets['news.json']['browser_download_url'])
            items = data.get('items', [])
            if not isinstance(items, list):
                raise UpdateError('Некорректный news.json')
            return {'ok': True, 'items': [{'title': str(x.get('title', ''))[:150], 'date': str(x.get('date', ''))[:30],
                                         'body': str(x.get('body', ''))[:10000]} for x in items[:100] if isinstance(x, dict)]}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _auth_request(self, route, values, token=None, retry_session=True):
        with self._auth_guard:
            return self._auth_request_locked(route, values, token, retry_session)

    def _auth_request_locked(self, route, values, token=None, retry_session=True):
        base = self._config.get('auth_api_url', '').rstrip('/')
        p = urllib.parse.urlsplit(base)
        if p.scheme != 'https' or not p.hostname or p.username or p.password:
            raise UpdateError('Сервис аккаунтов не подключён. Нужен серверный HTTPS API; AuthMe сам по себе не является таким API.')
        headers = {'Content-Type': 'application/json', 'User-Agent': 'SinsWorldLauncher/' + VERSION}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(base + route, data=json.dumps(values).encode(), headers=headers, method='POST')
        # No redirects with credentials to another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        try:
            from auth_bridge import auth_ssl_context
            context = auth_ssl_context(self._config.get('auth_tls_certificate', ''), self._config.get('auth_tls_origin', ''), base)
            with urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context)).open(req, timeout=25) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise UpdateError('Слишком большой ответ сервиса')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and token and retry_session and self._session and self._session.get('refresh_token'):
                refreshed = self._auth_request('/v1/sessions/refresh', {'refresh_token': self._session['refresh_token']}, retry_session=False)
                self._accept_session(refreshed)
                try:
                    import keyring
                    service = 'SinsWorld:' + self._config['auth_api_url']
                    if keyring.get_password(service, 'refresh'):
                        keyring.set_password(service, 'refresh', refreshed['refresh_token'])
                except Exception:
                    pass
                return self._auth_request(route, values, refreshed['access_token'], retry_session=False)
            try:
                reply = json.loads(exc.read(4096))
                detail = reply.get('error') if isinstance(reply, dict) else None
            except Exception:
                detail = None
            message = detail[:300] if isinstance(detail, str) and detail else 'Сервис отклонил запрос. Проверьте данные и доступность аккаунта.'
            if exc.code == 426:
                self._update_required = True
                self.emit('update_required', version='актуальная', message=message)
            raise UpdateError(message) from exc
        except (ValueError, UnicodeError) as exc:
            raise UpdateError('Некорректный ответ сервиса аккаунтов') from exc

    def account(self, mode, nickname, password='', repeat='', remember=False, recovery_code=''):
        if self._busy:
            return {'ok': False, 'error': 'Дождитесь завершения операции'}
        try:
            if not re.fullmatch(r'[A-Za-z0-9_]{3,16}', nickname or ''):
                raise ValueError('Ник: 3–16 символов A–Z, 0–9 или _')
            if mode == 'local':
                if self._config.get('auth_api_url'):
                    raise ValueError('Локальный вход отключён, когда подключён сервис аккаунтов')
                self._config['nickname'] = nickname
                self._session = None
                atomic_json(self._data / 'config.json', self._config)
                return {'ok': True, 'profile': self.profile(), 'local': True}
            if mode not in ('login', 'register', 'recover'):
                raise ValueError('Неизвестный режим аккаунта')
            authme = self._config.get('auth_mode') == 'authme_bridge'
            minimum = 5 if authme and mode == 'login' else 12
            maximum = 30 if authme else 128
            if not minimum <= len(password) <= maximum:
                raise ValueError(f'Пароль должен содержать {minimum}–{maximum} символов')
            if mode in ('register', 'recover') and password != repeat:
                raise ValueError('Пароли не совпадают')
            if mode == 'recover':
                self._auth_request('/v1/account/recover', {'nickname': nickname, 'recovery_code': recovery_code, 'password': password})
                return {'ok': True, 'message': 'Пароль изменён. Войдите с новым паролем.'}
            route = '/v1/accounts/register' if mode == 'register' else '/v1/sessions/login'
            data = self._auth_request(route, {'nickname': nickname, 'password': password})
            self._accept_session(data)
            warning = ''
            if not remember:
                try:
                    import keyring
                    keyring.delete_password('SinsWorld:' + self._config['auth_api_url'], 'refresh')
                except Exception:
                    pass
            if remember and data.get('refresh_token'):
                try:
                    import keyring
                    keyring.set_password('SinsWorld:' + self._config['auth_api_url'], 'refresh', data['refresh_token'])
                except Exception:
                    warning = 'Не удалось сохранить сессию в защищённом хранилище. Вход действует до закрытия.'
            return {'ok': True, 'profile': self.profile(), 'recovery_code': str(data.get('recovery_code', '')), 'warning': warning}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def _accept_session(self, data):
        with self._auth_guard:
            return self._accept_session_locked(data)

    def _accept_session_locked(self, data):
        account = data.get('account', {})
        uuid.UUID(account.get('id', ''))
        if not re.fullmatch(r'[A-Za-z0-9_]{3,16}', account.get('name', '')) or not isinstance(data.get('access_token'), str):
            raise ValueError('Некорректная сессия аккаунта')
        if not self._session or self._session['account']['id'] != account['id']:
            with self._avatar_guard:
                self._avatar_identity = self._avatar_url = self._avatar_data = None
                self._avatar_attempt = 0.0
                self._skin_png = self._skin_png_owner = None
        self._session = data

    def _restore_session(self):
        if not self._config.get('auth_api_url'):
            return
        try:
            import keyring
            service = 'SinsWorld:' + self._config['auth_api_url']
            token = keyring.get_password(service, 'refresh')
            if token:
                data = self._auth_request('/v1/sessions/refresh', {'refresh_token': token})
                self._accept_session(data)
                if data.get('refresh_token'):
                    keyring.set_password(service, 'refresh', data['refresh_token'])
                self.emit('profile', profile=self.profile())
        except Exception:
            self.emit('notice', message='Сохранённая сессия недоступна. Войдите снова.')

    def logout(self):
        with self._auth_guard:
            return self.logout_locked()

    def logout_locked(self):
        try:
            if self._session:
                self._auth_request('/v1/sessions/logout', {}, self._session['access_token'])
        except Exception:
            pass
        self._session = None
        with self._avatar_guard:
            self._avatar_identity = self._avatar_url = self._avatar_data = None
            self._avatar_attempt = 0.0
            self._skin_png = self._skin_png_owner = None
        self._config['nickname'] = ''
        atomic_json(self._data / 'config.json', self._config)
        try:
            import keyring
            keyring.delete_password('SinsWorld:' + self._config.get('auth_api_url', ''), 'refresh')
        except Exception:
            pass
        return {'ok': True, 'profile': self.profile()}

    def _java17(self, mc, game, callback):
        def valid(path):
            if not path:
                return False
            try:
                result = subprocess.run([str(path), '-version'], capture_output=True, text=True, timeout=15,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                return result.returncode == 0 and bool(re.search(r'version "17(?:[.\+" ]|$)', result.stdout + result.stderr))
            except (OSError, subprocess.TimeoutExpired):
                return False
        # Respect a valid explicit Java 17, then prefer our private runtime.
        configured = self._config.get('java_path')
        if valid(configured):
            return str(configured)
        runtime = mc.runtime.get_executable_path('java-runtime-gamma', str(game))
        if valid(runtime):
            return str(runtime)
        system = shutil.which('java')
        if valid(system):
            return str(system)
        self._cancel_check()
        self.emit('game_progress', message='Установка Java 17', checked=None, total=None)
        mc.runtime.install_jvm_runtime('java-runtime-gamma', str(game), callback=callback)
        self._cancel_check()
        runtime = mc.runtime.get_executable_path('java-runtime-gamma', str(game))
        if not valid(runtime):
            raise UpdateError('Не удалось подготовить Java 17. Проверьте соединение и повторите запуск.')
        return str(runtime)

    def _install_callback(self, message):
        progress = {'max': 0, 'last': 0.0, 'status': ''}
        lock = threading.Lock()
        def notify(value=None, force=False):
            self._cancel_check()
            with lock:
                now = time.monotonic()
                if not force and now - progress['last'] < .2:
                    return
                progress['last'] = now
                self.emit('game_progress', message=message, checked=value, total=progress['max'] or None)
        def status(text):
            # File callbacks run concurrently; keep the native bridge responsive.
            if not str(text).startswith('Download '):
                self._log.info('%s: %s', message, text)
            notify()
        def maximum(value):
            progress['max'] = value
            notify(0, True)
        return {'setStatus': status, 'setProgress': notify, 'setMax': maximum}

    def _install_forge(self, mc, forge, game, java):
        # lib 7.1 ignores processor exit codes. Check them and retain output.
        original = getattr(mc.forge, 'subprocess', None)
        def run_processor(command, **kwargs):
            self._cancel_check()
            with open(self._data / 'forge-install.log', 'ab') as output:
                result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                        timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise UpdateError('Не удалось установить Forge. Журнал: ' + str(self._data / 'forge-install.log'))
            return result
        mc.forge.subprocess = SimpleNamespace(run=run_processor)
        try:
            mc.forge.install_forge_version(forge, str(game),
                        callback=self._install_callback('Установка Forge'), java=java)
        finally:
            if original is not None:
                mc.forge.subprocess = original
            else:
                del mc.forge.subprocess

    def _prepare_game(self, mc, game):
        version, forge = self._config['minecraft_version'], self._config['forge_version']
        candidate = f"{version}-forge-{forge.split('-', 1)[1]}"
        self._log.info('Preparing Minecraft %s and Forge %s in %s', version, forge, game)
        self.emit('game_progress', message='Скачивание Minecraft', checked=None, total=None)
        callback = self._install_callback('Скачивание Minecraft')
        mc.install.install_minecraft_version(version, str(game), callback=callback)
        client = game / 'versions' / version / (version + '.jar')
        if not client.is_file() or not client.stat().st_size:
            raise UpdateError('Minecraft не скачан: файл игры отсутствует. Проверьте доступ к серверам Mojang.')
        self._cancel_check()
        java = self._java17(mc, game, self._install_callback('Установка Java 17'))
        marker = game / '.sinsworld-forge-ready.json'
        profile = game / 'versions' / candidate / (candidate + '.json')
        try:
            ready = json.loads(marker.read_text('utf-8')).get('forge') == forge
        except (OSError, ValueError):
            ready = False
        # Forge writes its profile BEFORE processors run. A profile alone does
        # not prove that an interrupted installation completed successfully.
        if not ready or not profile.is_file():
            marker.unlink(missing_ok=True)
            self.emit('game_progress', message='Установка Forge', checked=None, total=None)
            self._install_forge(mc, forge, game, java)
            if not profile.is_file():
                raise UpdateError('Forge не создал файл установленной версии')
        self.emit('game_progress', message='Проверка библиотек игры', checked=None, total=None)
        mc.install.install_minecraft_version(candidate, str(game),
                            callback=self._install_callback('Проверка библиотек игры'))
        # Forge's processors can return a failure without raising in lib 7.1.
        # Check the actual launch classpath before accepting the installation.
        options = {'username': 'InstallCheck', 'uuid': '0' * 32, 'token': '0', 'executablePath': java}
        command = mc.command.get_minecraft_command(candidate, str(game), options)
        for flag in ('-cp', '-classpath', '--class-path', '-p', '--module-path'):
            if flag in command:
                entries = command[command.index(flag) + 1].split(os.pathsep)
                missing = [p for p in entries if p and not Path(p).is_file()]
                if missing:
                    marker.unlink(missing_ok=True)
                    raise UpdateError('Не скачаны библиотеки игры: ' + ', '.join(Path(p).name for p in missing[:3]) + '. Повторите запуск.')
        atomic_json(marker, {'forge': forge})
        self._cancel_check()
        self._log.info('Game installation verified')
        return candidate, java

    def _hide_for_game(self):
        if self._window is not None:
            try:
                self._window.hide()
            except Exception:
                self._log.exception('Could not hide launcher window')

    def _watch_game(self, process, auth_file=None):
        try:
            code = process.wait()
            self._log.info('Minecraft exited: %s', code)
            message = ('Готов к запуску' if code == 0 else
                       'Игра завершилась с ошибкой. Журнал: ' + str(self._data / 'minecraft-console.log'))
        except Exception:
            self._log.exception('Could not wait for Minecraft')
            message = 'Не удалось проверить завершение игры. Журнал: ' + str(self._data / 'launcher.log')
        finally:
            from auth_bridge import cleanup_ticket
            cleanup_ticket(auth_file)
        if self._cancel.is_set():
            return
        # Keep the existing process/WebView alive while playing, then reveal
        # the same window for both a normal exit and a game crash.
        if self._window is not None:
            try:
                self._window.show()
                self._window.restore()
            except Exception:
                self._log.exception('Could not restore launcher window')
        self.emit('game_exited', message=message)

    def play(self, only_mods=False):
        if self._update_required:
            return {'ok': False, 'error': 'Обновите лаунчер для продолжения'}
        if self._config.get('selected_server', 'sinsworld') in ('naruto', 'solo'):
            name = 'Solo Leveling' if self._config['selected_server'] == 'solo' else 'Наруто'
            return {'ok': False, 'error': f'Сервер {name} готовится к открытию'}
        if self._game_process is not None and self._game_process.poll() is None:
            return {'ok': False, 'error': 'Minecraft уже запущен. Обновлять моды во время игры нельзя.'}
        if not only_mods and not self.profile()['name']:
            return {'ok': False, 'error': 'Сначала войдите в аккаунт или выберите локальный профиль'}
        def work():
            self.emit('game_progress', message='Подготовка к запуску', checked=None, total=None)
            self._log.info('Play requested; mods_only=%s', only_mods)
            game = self._game
            game.mkdir(parents=True, exist_ok=True)
            game_session = None
            auth_file = None
            authme = self._config.get('auth_mode') == 'authme_bridge'
            if self._config.get('auth_api_url') and not only_mods and not self._session:
                raise UpdateError('Сессия истекла. Войдите в аккаунт.')
            if not only_mods:
                import minecraft_launcher_lib as mc
                candidate, java = self._prepare_game(mc, game)
            self.emit('game_progress', message='Проверка модов', checked=None, total=None)
            # Mandatory integrity check before every launch.
            client = GitHub(self._config.get('mods_repository', ''), self._config.get('mods_release', 'mods-current'), cancel=self._cancel)
            sync_mods(client, game, self._config['minecraft_version'], self._config['forge_version'],
                      lambda checked, total, text: self.emit('game_progress', checked=checked, total=total, message=text),
                      critical=lambda active: setattr(self, '_installing', active))
            if only_mods:
                self.emit('notice', message='Моды обновлены через GitHub')
                return
            self._cancel_check()
            if self._config.get('auth_api_url'):
                if authme:
                    from auth_bridge import install_client, validate_session
                    self.emit('game_progress', message='Подготовка входа на сервер', checked=None, total=None)
                    install_client(self._config, self._data, game, self._cancel,
                        lambda done, total: self.emit('game_progress', message='Подготовка входа на сервер', checked=done, total=total))
                    game_session = self._auth_request('/v1/game/session', {'server_id': self._config.get('selected_server','sinsworld')}, self._session['access_token'])
                    proof = validate_session(game_session, self._session['account'], self._config.get('server_address', ''))
                    auth_file = game / '.sinsworld-auth' / (str(uuid.uuid4()) + '.json')
                else:
                    game_session = self._auth_request('/v1/game/session', {'server_id': self._config.get('selected_server','sinsworld')}, self._session['access_token'])
                    if not all(game_session.get(k) for k in ('name', 'uuid', 'access_token')):
                        raise UpdateError('Сервис не выдал игровую сессию')
                    uuid.UUID(game_session['uuid'])
                    if game_session['name'] != self.profile()['name'] or uuid.UUID(game_session['uuid']) != uuid.UUID(self._session['account']['id']):
                        raise UpdateError('Игровая сессия не соответствует аккаунту')
                    injector = ROOT / 'authlib-injector.jar'
                    from github_updates import checksum
                    expected = self._config.get('authlib_injector_sha256', '')
                    if not injector.is_file() or not re.fullmatch('[0-9a-f]{64}', expected) or checksum(injector) != expected:
                        raise UpdateError('Для собственной авторизации нужен проверенный authlib-injector.jar и его SHA-256. См. docs/AUTH.md.')
                    if not self._config.get('yggdrasil_url', '').startswith('https://'):
                        raise UpdateError('Не настроен yggdrasil_url')
            nickname = self.profile()['name']
            # Preserve legacy local profile compatibility; this does NOT bypass AuthMe.
            import hashlib
            player_uuid = str(uuid.UUID(bytes=hashlib.md5(('OfflinePlayer:' + nickname).encode()).digest(), version=3))
            jvm = [f'-Xmx{self._config["ram_mb"]}M']
            if game_session:
                nickname, player_uuid = game_session['name'], game_session['uuid']
                if authme:
                    jvm.append('-Dsinsworld.auth.file=' + str(auth_file))
                else:
                    jvm.append(f'-javaagent:{ROOT / "authlib-injector.jar"}={self._config["yggdrasil_url"]}')
            options = {'username': nickname, 'uuid': player_uuid, 'token': game_session['access_token'] if game_session and not authme else '0',
                       'executablePath': java, 'jvmArguments': jvm}
            command = mc.command.get_minecraft_command(candidate, str(game), options)
            command += ['--width', str(self._config['resolution_width']), '--height', str(self._config['resolution_height'])]
            if self._config.get('fullscreen'):
                command.append('--fullscreen')
            server = self._config.get('server_address', '').strip()
            if server:
                command += ['--quickPlayMultiplayer', server]
            self._cancel_check()
            try:
                if auth_file is not None:
                    atomic_json(auth_file, proof)
                with open(self._data / 'minecraft-console.log', 'ab') as console:
                    self._game_process = subprocess.Popen(command, cwd=game, stdout=console, stderr=subprocess.STDOUT,
                                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except BaseException:
                from auth_bridge import cleanup_ticket
                cleanup_ticket(auth_file)
                raise
            self.emit('game_started', message='Minecraft запущен')
            self._hide_for_game()
            threading.Thread(target=self._watch_game, args=(self._game_process, auth_file), daemon=True).start()
        return self._task(work)

    def toggle_maximize(self):
        if self._window:
            self._window.toggle_fullscreen()
        return {'ok': True}

    def minimize(self):
        if self._window:
            self._window.minimize()

    def close(self):
        if self._installing:
            return {'ok': False, 'error': 'Завершается установка обновления'}
        self._cancel.set()
        if self._window:
            self._window.destroy()
        return {'ok': True}

def setup_auth():
    import webview
    from auth_bridge import check_api_url
    class SetupAPI:
        def configure(self, value, certificate_pem=''):
            try:
                certificate_pem = certificate_pem.strip()
                url = check_api_url(value, certificate_pem)
                config = load_config()
                config.update(auth_api_url=url, auth_mode='authme_bridge', server_address='sinsworld.minecraft.best',
                              auth_tls_certificate=certificate_pem, auth_tls_origin=url if certificate_pem else '')
                atomic_json(CONFIG_FILE, config)
                return {'ok': True}
            except Exception as exc:
                return {'ok': False, 'error': str(exc)}
    mutex = None
    if sys.platform == 'win32':
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\SinsWorldLauncher')
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(0, 'Закройте лаунчер перед настройкой авторизации.', 'SinsWorld', 0)
            return
    webview.create_window('SinsWorld — подключение AuthMe', str(ROOT / 'ui/setup-auth.html'), js_api=SetupAPI(), width=650, height=650)
    webview.start(http_server=True, gui='edgechromium' if sys.platform == 'win32' else None)

def main():
    import webview
    api = LauncherAPI()
    mutex = None
    if sys.platform == 'win32':
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\SinsWorldLauncher')
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(0, 'SinsWorld уже запущен.', 'SinsWorld', 0)
            return
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('SinsWorld.Launcher')
    window = webview.create_window('SinsWorld', str(ROOT / 'ui' / 'index.html'), js_api=api,
                                   width=460, height=560, min_size=(420, 480),
                                   frameless=True, easy_drag=False, background_color='#080f0d')
    api._window = window
    def on_closing():
        if api._installing and not api._restarting:
            api.emit('notice', message='Дождитесь окончания установки')
            return False
        api._cancel.set()
        return True
    window.events.closing += on_closing
    window.events.closed += lambda: api._cancel.set()
    webview.start(http_server=True, debug=False, gui='edgechromium' if sys.platform == 'win32' else None,
                  icon=str(ROOT / 'SinsWorldLauncher.ico') if sys.platform == 'win32' else None)

if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == '--setup-auth':
        setup_auth()
    elif len(sys.argv) == 3 and sys.argv[1] == '--verify-auth-build':
        import auth_bridge
        config = json.loads((ROOT / 'config.json').read_text('utf-8'))
        binding = config['auth_bridge_client']
        assert re.fullmatch('[0-9a-f]{64}', binding['sha256']) and binding['size'] > 0
        assert (ROOT / 'ui/setup-auth.html').is_file()
        if config.get('auth_service_locked'):
            assert config['auth_api_url'] == config['auth_tls_origin'] == 'https://104.204.218.124:8765'
            context = auth_bridge.auth_ssl_context(config['auth_tls_certificate'], config['auth_tls_origin'], config['auth_api_url'])
            import ssl
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            assert load_config()['auth_tls_certificate'] == config['auth_tls_certificate']
        atomic_json(sys.argv[2], {'ok': True, 'version': VERSION, 'auth_mode': config['auth_mode']})
    elif len(sys.argv) == 3 and sys.argv[1] == '--verify-game-install':
        import minecraft_launcher_lib as mc
        diagnostic_data = Path(sys.argv[2]).resolve()
        api = LauncherAPI(data=diagnostic_data)
        try:
            game = diagnostic_data / 'game'
            game.mkdir(parents=True, exist_ok=True)
            candidate, java = api._prepare_game(mc, game)
            atomic_json(diagnostic_data / 'install-result.json', {'ok': True, 'version': candidate, 'java': java})
        except Exception:
            api._log.exception('Frozen installation test failed')
            raise SystemExit(1)
    else:
        main()
