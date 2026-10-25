"""One-time/maintainer import from the original public folder into Release assets.
Players never contact Yandex: this tool runs only in publisher CI.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from github_updates import mod_path, UpdateError

def safe_url(url):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or p.username or p.password or not any(
            host == h or host.endswith('.' + h) for h in ('yandex.net', 'yandex.ru', 'yandex.com', 'yandexcloud.net', 'yadi.sk')):
        raise UpdateError('Импорт разрешён только по HTTPS с доменов Яндекса')
    return url

class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def with_retry(action):
    for attempt in range(5):
        try:
            return action()
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 4:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 4:
                raise
        time.sleep(min(2 ** (attempt + 1), 16))

def run(source_url, output):
    safe_url(source_url)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(Redirect())
    endpoint = 'https://cloud-api.yandex.net/v1/disk/public/resources'
    def metadata(path='', download=False, offset=0):
        params = {'public_key': source_url, 'path': path}
        if not download:
            params.update({'limit': 100, 'offset': offset})
        url = endpoint + ('/download' if download else '') + '?' + urllib.parse.urlencode(params)
        with with_retry(lambda: opener.open(url, timeout=45)) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise UpdateError('Слишком большой ответ Яндекс Диска')
        return json.loads(data)
    def walk(path='', depth=0):
        if depth > 8:
            raise UpdateError('Слишком глубокая папка')
        offset = 0
        while True:
            data = metadata(path, offset=offset)
            if data.get('type') != 'dir':
                raise UpdateError('Нужна публичная папка с модами')
            embedded = data.get('_embedded', {})
            items = embedded.get('items', [])
            for item in items:
                if item.get('type') == 'dir':
                    yield from walk(item['path'], depth + 1)
                elif item.get('type') == 'file' and item.get('name', '').lower().endswith('.jar'):
                    yield item
            offset += len(items)
            if not items or offset >= embedded.get('total', offset):
                break
    entries = list(walk())
    if not 1 <= len(entries) <= 1000:
        raise UpdateError('Не найден набор из 1–1000 JAR-модов')
    seen = set()
    for entry in entries:
        name = entry['name']
        mod_path(output.parent / 'validation', 'mods/' + name)
        if name.casefold() in seen:
            raise UpdateError('Повторяющееся имя мода')
        seen.add(name.casefold())
        if not 0 < int(entry.get('size', 0)) <= 2 * 1024**3:
            raise UpdateError('Недопустимый размер мода: ' + name)
    def fetch_one(index_entry):
        i, entry = index_entry
        target = output / entry['name']
        temp = target.with_suffix(target.suffix + '.part')
        def attempt_download():
            url = safe_url(metadata(entry['path'], download=True)['href'])
            h, count = hashlib.sha256(), 0
            try:
                with opener.open(url, timeout=90) as response, temp.open('wb') as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > entry['size']:
                            raise UpdateError('Размер мода изменился при скачивании')
                        stream.write(chunk)
                        h.update(chunk)
                if count != entry['size']:
                    raise UpdateError('Скачивание мода не завершено')
                remote_hash = str(entry.get('sha256', '')).lower()
                if remote_hash and (not re.fullmatch('[0-9a-f]{64}', remote_hash) or h.hexdigest() != remote_hash):
                    raise UpdateError('SHA-256 Яндекс Диска не совпал')
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
        with_retry(attempt_download)
        print(f'[{i}/{len(entries)}] {target.name}', flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch_one, enumerate(entries, 1)))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, default=Path('release-mods'))
    args = parser.parse_args()
    run(args.source, args.output)
