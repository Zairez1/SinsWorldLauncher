import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
import launcher
from auth_bridge import auth_ssl_context

class BundledAuthTests(unittest.TestCase):
    def test_old_preview_settings_cannot_replace_published_server_identity(self):
        defaults = json.loads((launcher.ROOT / 'config.json').read_text())
        self.assertTrue(defaults['auth_service_locked'])
        for old_url in ('', 'https://obsolete.example.org'):
            with self.subTest(url=old_url), tempfile.TemporaryDirectory() as d:
                saved = Path(d) / 'config.json'
                saved.write_text(json.dumps({'auth_api_url': old_url, 'auth_tls_certificate': 'old',
                    'auth_tls_origin': old_url, 'auth_service_locked': False, 'server_address': 'wrong.example.org',
                    'ram_mb': 8192, 'nickname': 'Zaire'}))
                with patch.object(launcher, 'CONFIG_FILE', saved):
                    result = launcher.load_config()
                for key in ('auth_api_url','auth_tls_origin','auth_tls_certificate','server_address'):
                    self.assertEqual(defaults[key], result[key])
                self.assertTrue(result['auth_service_locked'])
                self.assertEqual(result['ram_mb'], 8192)
                self.assertEqual(result['nickname'], 'Zaire')
                context=auth_ssl_context(result['auth_tls_certificate'],result['auth_tls_origin'],result['auth_api_url'])
                self.assertTrue(context.check_hostname)
                self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
