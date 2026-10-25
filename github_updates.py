"""Public GitHub Releases transport and transactional, hash-verified updates.

No publisher credentials are shipped to clients. Trust is the configured GitHub
repository over HTTPS; SHA-256 detects corruption, not a compromised publisher.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

MAX_JSON = 2_000_000
MAX_FILE = 2 * 1024**3
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')

class UpdateError(RuntimeError):
    pass

class Cancelled(UpdateError):
    pass

def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise UpdateError('Версия должна иметь вид 1.2.3')
    return tuple(map(int, value.lstrip('v').split('.')))

def validate_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
        raise UpdateError('Разрешены только HTTPS-ссылки GitHub')
    if not (host in ('github.com', 'api.github.com', 'raw.githubusercontent.com')
            or host.endswith('.githubusercontent.com')):
        raise UpdateError('Ссылка ведёт не на GitHub')
    return url

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class GitHub:
    def __init__(self, repository: str, release: str, *, opener=None, cancel=None):
        if not REPO.fullmatch(repository or '') or any(x in ('.', '..') for x in repository.split('/')):
            raise UpdateError('Укажите GitHub-репозиторий в формате owner/repository')
        if not isinstance(release, str) or not release or len(release) > 160:
            raise UpdateError('Укажите тег GitHub Release')
        self.repository, self.release = repository, release
        self.opener = opener or urllib.request.build_opener(SafeRedirect())
        self.cancel = cancel or threading.Event()

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled('Операция отменена')

    def request(self, url):
        self.check_cancel()
        validate_url(url)
        req = urllib.request.Request(url, headers={
            'User-Agent': 'SinsWorldLauncher/1.0',
            'Accept': 'application/vnd.github+json' if '/api.github.com/' in url else 'application/octet-stream',
        })
        try:
            return self.opener.open(req, timeout=25)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError('Репозиторий или Release не найден. Нужен публичный релиз с приложенными файлами.') from exc
            if exc.code in (403, 429):
                raise UpdateError('GitHub временно ограничил запросы. Повторите позже.') from exc
            raise UpdateError(f'GitHub вернул HTTP {exc.code}') from exc

    def json(self, url):
        with self.request(url) as response:
            raw = response.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            raise UpdateError('Слишком большой JSON-файл')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise UpdateError('GitHub вернул некорректный JSON') from exc
        if not isinstance(data, dict):
            raise UpdateError('JSON должен быть объектом')
        return data

    def get_release(self):
        tag = urllib.parse.quote(self.release, safe='')
        data = self.json(f'https://api.github.com/repos/{self.repository}/releases/tags/{tag}')
        if data.get('draft'):
            raise UpdateError('Черновой релиз недоступен игрокам')
        assets = data.get('assets')
        if not isinstance(assets, list) or len(assets) > 1500:
            raise UpdateError('Некорректный список assets')
        result = {}
        for asset in assets:
            name = asset.get('name')
            if not isinstance(name, str) or name in result:
                raise UpdateError('Повторяющееся имя Release asset')
            url = asset.get('browser_download_url', '')
            p = urllib.parse.urlsplit(url)
            expected = f'/{self.repository}/releases/download/'
            if p.hostname != 'github.com' or not p.path.casefold().startswith(expected.casefold()):
                raise UpdateError('Asset принадлежит другому репозиторию')
            validate_url(url)
            result[name] = asset
        return result

    def manifest(self, assets, name):
        if name not in assets:
            raise UpdateError(f'В Release отсутствует {name}')
        data = self.json(assets[name]['browser_download_url'])
        if data.get('schema') != 1:
            raise UpdateError('Неподдерживаемая версия манифеста')
        return data

    def download(self, asset, target: Path, sha256: str, size: int, notify=lambda *_: None):
        if not isinstance(sha256, str) or not SHA.fullmatch(sha256):
            raise UpdateError('Не указан корректный SHA-256')
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE:
            raise UpdateError('Некорректный размер файла')
        if asset.get('size') != size:
            raise UpdateError('Размер asset не совпадает с манифестом')
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.part')
        try:
            total, h = 0, hashlib.sha256()
            with self.request(asset['browser_download_url']) as response, temp.open('wb') as stream:
                while True:
                    self.check_cancel()
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > size:
                        raise UpdateError('Размер скачивания превышает манифест')
                    h.update(chunk)
                    stream.write(chunk)
                    notify(total, size)
            if total != size or h.hexdigest() != sha256:
                raise UpdateError('Контрольная сумма или размер не совпадают. Файл не установлен.')
            self.check_cancel()
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def launcher_info(self, current_version):
        assets = self.get_release()
        manifest = self.manifest(assets, 'launcher-manifest.json')
        newer = version_tuple(manifest.get('version')) > version_tuple(current_version)
        if not newer:
            return None
        if manifest.get('asset') != 'SinsWorldLauncher.exe':
            raise UpdateError('В манифесте нужен SinsWorldLauncher.exe')
        if manifest['asset'] not in assets:
            raise UpdateError('EXE отсутствует в релизе')
        return manifest, assets[manifest['asset']]

def mod_path(root, name):
    if not isinstance(name, str) or '\\' in name or ':' in name:
        raise UpdateError('Недопустимый путь мода')
    p = PurePosixPath(name)
    if p.as_posix() != name or len(p.parts) != 2 or p.parts[0] != 'mods' or p.parts[1] in ('.', '..') or not p.name.lower().endswith('.jar'):
        raise UpdateError('Разрешены только файлы mods/*.jar')
    if any(c in p.name for c in '<>"|?*') or p.name.endswith((' ', '.')):
        raise UpdateError('Имя мода несовместимо с Windows')
    stem = p.name.split('.')[0].upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise UpdateError('Зарезервированное имя файла Windows')
    target = Path(root).joinpath(*p.parts)
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise UpdateError('Путь выходит за папку игры')
    return target

def sync_mods(client: GitHub, game: Path, minecraft_version, forge_version, notify=lambda *_: None,
              critical=lambda _: None):
    assets = client.get_release()
    manifest = client.manifest(assets, 'mods-manifest.json')
    if manifest.get('minecraft_version') != minecraft_version or manifest.get('forge_version') != forge_version:
        raise UpdateError('Minecraft/Forge манифеста модов не совпадает с настройками')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries or len(entries) > 1000:
        raise UpdateError('Манифест должен содержать от 1 до 1000 модов')
    desired, seen = {}, set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise UpdateError('Некорректная запись мода')
        target = mod_path(game, entry.get('path'))
        key = entry['path'].casefold()
        if key in seen or entry.get('asset') != target.name:
            raise UpdateError('Повторяющийся мод или неправильное имя asset')
        seen.add(key)
        if not SHA.fullmatch(str(entry.get('sha256', ''))):
            raise UpdateError('У мода отсутствует SHA-256')
        if type(entry.get('size')) is not int or not 0 < entry['size'] <= MAX_FILE:
            raise UpdateError('Некорректный размер мода')
        if entry['asset'] not in assets or assets[entry['asset']].get('size') != entry['size']:
            raise UpdateError('Мод отсутствует в Release или имеет неверный размер')
        desired[entry['path']] = entry
    game = Path(game)
    game.mkdir(parents=True, exist_ok=True)
    state_file = game / '.sinsworld-managed-mods.json'
    previous = {}
    if state_file.exists():
        try:
            previous = json.loads(state_file.read_text('utf-8'))
            if not isinstance(previous, dict):
                raise ValueError()
            for name, sha in previous.items():
                mod_path(game, name)
                if not SHA.fullmatch(str(sha)):
                    raise ValueError()
        except (ValueError, TypeError) as exc:
            raise UpdateError('Повреждён список управляемых модов; изменения отменены') from exc
    # Stage every changed file before touching the working pack.
    with tempfile.TemporaryDirectory(prefix='.sw-mods-', dir=game) as folder:
        staging = Path(folder)
        changed = []
        for index, (name, entry) in enumerate(desired.items(), 1):
            client.check_cancel()
            target = mod_path(game, name)
            if target.is_file() and target.stat().st_size == entry['size'] and checksum(target) == entry['sha256']:
                notify(index, len(entries), f'Актуален: {target.name}')
                continue
            temp = staging / target.name
            client.download(assets[entry['asset']], temp, entry['sha256'], entry['size'],
                            lambda done, total: notify(index - 1 + done / total, len(entries), f'Загрузка: {target.name}'))
            changed.append((temp, target))
            notify(index, len(entries), f'Проверен: {target.name}')
        client.check_cancel()
        # Preserve untracked or locally modified old files. Never delete all *.jar.
        obsolete = [mod_path(game, name) for name, sha in previous.items()
                    if name not in desired and name.casefold() not in seen
                    and mod_path(game, name).is_file() and checksum(mod_path(game, name)) == sha]
        backups, installed = [], []
        backup_dir = staging / 'backup'
        backup_dir.mkdir()
        critical(True)
        try:
            for i, target in enumerate(dict.fromkeys([t for _, t in changed] + obsolete)):
                if target.exists():
                    backup = backup_dir / str(i)
                    os.replace(target, backup)
                    backups.append((backup, target))
            for source, target in changed:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                installed.append(target)
            state_temp = staging / 'managed.json'
            state_temp.write_text(json.dumps({name: entry['sha256'] for name, entry in desired.items()}, indent=2), 'utf-8')
            os.replace(state_temp, state_file)
        except BaseException:
            for target in installed:
                target.unlink(missing_ok=True)
            for backup, target in reversed(backups):
                os.replace(backup, target)
            raise
        finally:
            critical(False)
    notify(len(entries), len(entries), 'Моды обновлены')
    return len(entries)
