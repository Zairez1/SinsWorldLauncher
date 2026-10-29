"""Read only a bounded Minecraft texture; authentication stays on the auth origin."""
import re
import ssl
import struct
import urllib.request

MAX_SKIN_BYTES = 256 * 1024


def texture_url(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'https?://textures\.minecraft\.net/texture/[0-9a-fA-F]{64}', value):
        raise ValueError('Unsupported skin texture origin')
    return 'https://' + value.split('://', 1)[1]


def validate_skin_png(raw):
    if (len(raw) < 33 or len(raw) > MAX_SKIN_BYTES or
            raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR'):
        raise ValueError('Invalid skin PNG')
    width, height = struct.unpack('>II', raw[16:24])
    if width != 64 or height not in (32, 64):
        raise ValueError('Unsupported skin dimensions')
    return raw


def fetch_skin(value):
    url = texture_url(value)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    # Never copy account headers, cookies, passwords or bearer tokens to the CDN.
    request = urllib.request.Request(url, headers={'Accept': 'image/png'})
    with opener.open(request, timeout=10) as response:
        return validate_skin_png(response.read(MAX_SKIN_BYTES + 1))
