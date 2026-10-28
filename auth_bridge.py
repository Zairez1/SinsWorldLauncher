"""Single-use AuthMe tickets; passwords/access tokens never go into Minecraft arguments."""
from pathlib import Path
import os
import re
import shutil
import time
import urllib.parse
import uuid
from github_updates import GitHub, UpdateError, checksum

CLIENT_NAME = 'sinsworld-auth-client-1.0.0.jar'

def server_key(value):
    if not isinstance(value, str) or not value or len(value) > 255 or any(c.isspace() for c in value) or any(c in value for c in '/\\?#@'):
        raise UpdateError('Некорректный игровой адрес сервиса авторизации')
    try:
        p = urllib.parse.urlsplit('//' + value)
        if not p.hostname or p.username or p.password or not 0 < (p.port or 25565) < 65536:
            raise ValueError()
        return p.hostname.rstrip('.').casefold(), p.port or 25565
    except ValueError as exc:
        raise UpdateError('Некорректный игровой адрес сервиса авторизации') from exc

def validate_session(data, account, server, now_ms=None):
    if not isinstance(data, dict) or data.get('mode') != 'authme_bridge':
        raise UpdateError('Сервис не поддерживает вход через AuthMe-мост')
    name = account.get('name', '')
    if not re.fullmatch(r'[A-Za-z0-9_]{3,16}', name) or data.get('name') != name:
        raise UpdateError('Игровая сессия не соответствует аккаунту')
    import hashlib
    expected = uuid.UUID(bytes=hashlib.md5(('OfflinePlayer:' + name).encode()).digest(), version=3)
    try:
        if uuid.UUID(data.get('uuid', '')) != expected or uuid.UUID(account.get('id', '')) != expected:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise UpdateError('UUID игровой сессии не соответствует аккаунту AuthMe') from exc
    if server_key(data.get('server')) != server_key(server):
        raise UpdateError('Сервис выдал билет для другого игрового сервера')
    ticket = data.get('ticket', '')
    if not isinstance(ticket, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
        raise UpdateError('Сервис выдал некорректный билет входа')
    now = time.time() * 1000 if now_ms is None else now_ms
    expires = data.get('expires_at')
    if type(expires) is not int or not now + 5000 < expires <= now + 660000:
        raise UpdateError('Билет входа истёк. Проверьте время Windows и повторите запуск.')
    return {'ticket': ticket, 'server': data['server'], 'uuid': str(expected), 'expires_at': expires}

def install_client(config, data_dir, game, cancel, notify):
    binding = config.get('auth_bridge_client', {})
    expected = binding.get('sha256', '')
    size = binding.get('size')
    asset_name = binding.get('asset', '')
    if asset_name not in (CLIENT_NAME, 'sinsworld-auth-client-' + str(expected) + '.jar') or not re.fullmatch('[0-9a-f]{64}', str(expected)) or type(size) is not int or size <= 0:
        raise UpdateError('Нужна сборка лаунчера с проверенным клиентским модом AuthMe')
    cache = Path(data_dir) / 'auth-client' / (expected + '.jar')
    if not cache.is_file() or cache.stat().st_size != size or checksum(cache) != expected:
        client = GitHub(binding.get('repository', ''), binding.get('release', ''), cancel=cancel)
        assets = client.get_release()
        if asset_name not in assets:
            raise UpdateError('Клиентский мод авторизации не опубликован')
        client.download(assets[asset_name], cache, expected, size, notify)
    target = Path(game) / 'mods' / CLIENT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.jar.auth-part')
    try:
        shutil.copy2(cache, temp)
        if checksum(temp) != expected:
            raise UpdateError('Клиентский мод авторизации повреждён')
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target

def cleanup_ticket(path):
    if path is not None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

def auth_ssl_context(certificate_pem='', trusted_origin='', origin=''):
    import ssl
    if not certificate_pem:
        return ssl.create_default_context()
    if origin != trusted_origin:
        raise UpdateError('Сертификат привязан к другому адресу. Повторите Configure-Auth.bat.')
    if not isinstance(certificate_pem, str) or len(certificate_pem) > 16000 or certificate_pem.count('-----BEGIN CERTIFICATE-----') != 1 or 'PRIVATE KEY' in certificate_pem:
        raise UpdateError('Вставьте только публичный tls-certificate.pem из панели сервера')
    try:
        # Trust only the manually installed server certificate, with normal IP/name,
        # validity and signature checks. Never disable TLS verification.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=certificate_pem)
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        return context
    except (ValueError, ssl.SSLError) as exc:
        raise UpdateError('Некорректный публичный сертификат сервера') from exc

def check_api_url(value, certificate_pem=''):
    import json
    import urllib.request
    if not isinstance(value, str):
        raise UpdateError('Введите HTTPS-адрес сервиса')
    value = value.strip().rstrip('/')
    p = urllib.parse.urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.path:
        raise UpdateError('Нужен HTTPS-адрес без пути, логина и параметров')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    request = urllib.request.Request(value + '/health', headers={'User-Agent':'SinsWorldLauncher/AuthSetup'})
    try:
        with urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=auth_ssl_context(certificate_pem, value, value))).open(request, timeout=15) as response:
            raw = response.read(10001)
        if len(raw) > 10000:
            raise ValueError()
        health = json.loads(raw)
        if not isinstance(health, dict) or health.get('service') != 'SinsWorldAuth' or health.get('protocol') != 1 or health.get('configured') is not True:
            raise UpdateError('Мост не готов: задайте public-api-url в конфигурации плагина и перезапустите сервер')
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError('HTTPS-сервис не ответил. Проверьте домен, сертификат и запуск плагина.') from exc
    return value
