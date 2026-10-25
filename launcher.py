"""SinsWorld desktop launcher. Native WebView UI, GitHub Releases updates."""
from __future__ import annotations
import ctypes
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

from github_updates import GitHub, UpdateError, Cancelled, sync_mods
from version import VERSION

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
DATA = Path(os.getenv('APPDATA', str(Path.home()))) / 'SinsWorldLauncher'
CONFIG_FILE = DATA / 'config.json'

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
    saved = json.loads(CONFIG_FILE.read_text('utf-8')) if CONFIG_FILE.exists() else {}
    if not isinstance(saved, dict):
        raise ValueError('Некорректный config.json')
    # Empty settings from the pre-GitHub version inherit the published channel.
    for key in ('launcher_repository', 'mods_repository'):
        if not saved.get(key):
            saved.pop(key, None)
    # Deliberately do not use previous Google/Yandex settings for any update.
    return {**defaults, **saved}

class LauncherAPI:
    def __init__(self, config=None, data=None):
        self._config = config if config is not None else load_config()
        self._data = Path(data) if data is not None else DATA
        self._data.mkdir(parents=True, exist_ok=True)
        self._window = None
        self._cancel = threading.Event()
        self._guard = threading.Lock()
        self._busy = False
        self._installing = False
        self._restarting = False
        self._started = False
        self._session = None
        self._game_process = None

    def emit(self, kind, **data):
        if self._window is not None and not self._cancel.is_set():
            try:
                if kind == 'main' or (kind == 'splash' and data.get('state') == 'checking'):
                    width, height = (1180 if kind == 'main' else 460), 720
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
                pass  # A closed WebView cannot consume events.

    def profile(self):
        if self._session:
            return {'name': self._session['account']['name'], 'connected': True}
        return {'name': self._config.get('nickname', ''), 'connected': False}

    def get_settings(self):
        keys = ('ram_mb', 'java_path', 'resolution_width', 'resolution_height', 'fullscreen',
                'check_updates', 'launcher_repository', 'launcher_release', 'mods_repository',
                'mods_release', 'server_address', 'auth_api_url', 'trailer_url')
        return {**{k: self._config.get(k) for k in keys}, 'version': VERSION,
                'game_directory': str(self._data / 'game'), 'profile': self.profile()}

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
            for name in ('fullscreen', 'check_updates'):
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
        folder = self._data / 'game'
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
                if startup:
                    self._installing = False
                self.emit('error', message=str(exc), startup=startup)
            finally:
                self._busy = False
                self.emit('idle')
        threading.Thread(target=worker, daemon=True).start()
        return {'ok': True}

    def startup(self):
        if self._started:
            return {'ok': True}
        self._started = True
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
                self.emit('splash', state='no-update', progress=100)
                time.sleep(.65)
                self.emit('main')
                self._restore_session()
                return
            manifest, asset = info
            if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
                self.emit('source_update', message=f"Доступна версия {manifest['version']}. Автоустановка работает в собранном Windows EXE.")
                return
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
                              '-File', str(helper), '-ParentPid', str(os.getpid()), '-Source', str(target),
                              '-Target', str(Path(sys.executable).resolve()), '-ExpectedSha256', manifest['sha256']],
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            time.sleep(.5)
            self._restarting = True
            self._window.destroy()
        return self._task(work, startup=True)

    def _cancel_check(self):
        if self._cancel.is_set():
            raise Cancelled('Операция отменена')

    def continue_to_main(self):
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

    def _auth_request(self, route, values, token=None):
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
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise UpdateError('Слишком большой ответ сервиса')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except urllib.error.HTTPError as exc:
            raise UpdateError('Сервис отклонил запрос. Проверьте данные и доступность аккаунта.') from exc
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
            if not 12 <= len(password) <= 128:
                raise ValueError('Пароль должен содержать 12–128 символов')
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
        account = data.get('account', {})
        uuid.UUID(account.get('id', ''))
        if not re.fullmatch(r'[A-Za-z0-9_]{3,16}', account.get('name', '')) or not isinstance(data.get('access_token'), str):
            raise ValueError('Некорректная сессия аккаунта')
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
        try:
            if self._session:
                self._auth_request('/v1/sessions/logout', {}, self._session['access_token'])
        except Exception:
            pass
        self._session = None
        self._config['nickname'] = ''
        atomic_json(self._data / 'config.json', self._config)
        try:
            import keyring
            keyring.delete_password('SinsWorld:' + self._config.get('auth_api_url', ''), 'refresh')
        except Exception:
            pass
        return {'ok': True, 'profile': self.profile()}

    def play(self, only_mods=False):
        if self._game_process is not None and self._game_process.poll() is None:
            return {'ok': False, 'error': 'Minecraft уже запущен. Обновлять моды во время игры нельзя.'}
        if not only_mods and not self.profile()['name']:
            return {'ok': False, 'error': 'Сначала войдите в аккаунт или выберите локальный профиль'}
        def work():
            game = self._data / 'game'
            game.mkdir(parents=True, exist_ok=True)
            # Optional server sessions must be verified by the Minecraft server too.
            game_session = None
            if self._config.get('auth_api_url') and not only_mods:
                if not self._session:
                    raise UpdateError('Сессия истекла. Войдите в аккаунт.')
                game_session = self._auth_request('/v1/game/session', {}, self._session['access_token'])
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
            if only_mods or self._config.get('check_updates', True):
                client = GitHub(self._config.get('mods_repository', ''), self._config.get('mods_release', 'mods-current'), cancel=self._cancel)
                sync_mods(client, game, self._config['minecraft_version'], self._config['forge_version'],
                          lambda checked, total, text: self.emit('game_progress', checked=checked, total=total, message=text),
                          critical=lambda active: setattr(self, '_installing', active))
            if only_mods:
                self.emit('notice', message='Моды обновлены через GitHub')
                return
            self._cancel_check()
            import minecraft_launcher_lib as mc
            version, forge = self._config['minecraft_version'], self._config['forge_version']
            candidate = f"{version}-forge-{forge.split('-', 1)[1]}"
            if not (game / 'versions' / candidate / (candidate + '.json')).is_file():
                callback = {'setStatus': lambda text: self.emit('game_progress', message=str(text), checked=None, total=None)}
                self.emit('game_progress', message='Установка Minecraft и Forge', checked=None, total=None)
                mc.install.install_minecraft_version(version, str(game), callback=callback)
                self._cancel_check()
                installed = mc.forge.install_forge_version(forge, str(game), callback=callback)
                if installed:
                    candidate = installed
                if not (game / 'versions' / candidate / (candidate + '.json')).is_file():
                    raise UpdateError('Forge не создал файл установленной версии')
            java = self._config.get('java_path') or shutil.which('java')
            if not java:
                raise UpdateError('Установите Java 17 или укажите её путь в настройках')
            result = subprocess.run([java, '-version'], capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not re.search(r'version "17(?:[.\+" ]|$)', result.stdout + result.stderr):
                raise UpdateError('Для Forge 1.20.1 требуется Java 17')
            nickname = self.profile()['name']
            # Preserve legacy local profile compatibility; this does NOT bypass AuthMe.
            import hashlib
            player_uuid = str(uuid.UUID(bytes=hashlib.md5(('OfflinePlayer:' + nickname).encode()).digest(), version=3))
            jvm = [f'-Xmx{self._config["ram_mb"]}M']
            if game_session:
                nickname, player_uuid = game_session['name'], game_session['uuid']
                jvm.append(f'-javaagent:{ROOT / "authlib-injector.jar"}={self._config["yggdrasil_url"]}')
            options = {'username': nickname, 'uuid': player_uuid, 'token': game_session['access_token'] if game_session else '0',
                       'executablePath': java, 'jvmArguments': jvm}
            command = mc.command.get_minecraft_command(candidate, str(game), options)
            command += ['--width', str(self._config['resolution_width']), '--height', str(self._config['resolution_height'])]
            if self._config.get('fullscreen'):
                command.append('--fullscreen')
            server = self._config.get('server_address', '').strip()
            if server:
                command += ['--quickPlayMultiplayer', server]
            self._cancel_check()
            self._game_process = subprocess.Popen(command, cwd=game)
            self.emit('game_started', message='Minecraft запущен')
        return self._task(work)

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
                                   width=460, height=720, min_size=(420, 640),
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
    main()
