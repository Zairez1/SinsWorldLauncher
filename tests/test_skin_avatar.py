import base64
from contextlib import contextmanager
import struct
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import launcher
from launcher import LauncherAPI
from skin_avatar import fetch_skin, texture_url, validate_skin_png, MAX_SKIN_BYTES

URL = 'https://textures.minecraft.net/texture/' + 'a' * 64
PNG = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 64, 64) + b'\x08\x06\x00\x00\x00' + b'\x00'*4


class ImmediateThread:
    def __init__(self, target, **kwargs): self.target = target
    def start(self): self.target()


class SkinAvatarTests(unittest.TestCase):
    def test_only_exact_texture_origin_and_no_credentials_paths_or_redirect_query(self):
        self.assertEqual(texture_url(URL.replace('https:', 'http:')), URL)
        for value in ['https://localhost/texture/'+'a'*64, URL+'?redirect=x', URL+'/x',
                      URL.replace('textures.minecraft.net', 'textures.minecraft.net.evil.test'),
                      URL.replace('textures.minecraft.net', 'user@textures.minecraft.net'),
                      'file:///skin.png', None]:
            with self.subTest(value=value), self.assertRaises(ValueError): texture_url(value)

    def test_skin_image_boundaries(self):
        self.assertEqual(validate_skin_png(PNG), PNG)
        for value in [b'<svg/>', PNG[:20], PNG+b'0'*MAX_SKIN_BYTES,
                      PNG[:16]+struct.pack('>II', 10000, 10000)+PNG[24:]]:
            with self.assertRaises(ValueError): validate_skin_png(value)

    def test_texture_fetch_sends_no_account_authorization_and_bounds_response(self):
        response=Mock();response.read.return_value=PNG
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=response)
        opener.open.return_value.__exit__=Mock(return_value=False)
        with patch('skin_avatar.urllib.request.build_opener',return_value=opener):
            self.assertEqual(fetch_skin(URL),PNG)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,URL)
        self.assertIsNone(request.get_header('Authorization'))
        response.read.assert_called_once_with(MAX_SKIN_BYTES+1)

    def test_avatar_lookup_uses_authenticated_session_and_caches_texture(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_mode':'authme_bridge'},Path(d));api.emit=Mock()
            api._accept_session({'account':{'id':'185fb4a0-40e2-32d2-acaf-60e26d2869ba','name':'Zaire'},'access_token':'private-token'})
            api._auth_request=Mock(return_value={'available':True,'texture_url':URL})
            with patch('launcher.threading.Thread',ImmediateThread),patch('launcher.fetch_skin',return_value=PNG) as fetch:
                self.assertTrue(api.refresh_avatar()['ok'])
                api._avatar_attempt=0
                api.refresh_avatar()
                fetch.assert_called_once_with(URL)
            api._auth_request.assert_called_with('/v1/profile/skin',{},'private-token')
            self.assertTrue(api.profile()['skin_data'].startswith('data:image/png;base64,'))
            self.assertEqual(api.emit.call_args.args[0],'avatar')

    def test_delayed_skin_cannot_cross_account_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_mode':'authme_bridge'},Path(d));api.emit=Mock()
            api._accept_session({'account':{'id':'185fb4a0-40e2-32d2-acaf-60e26d2869ba','name':'Zaire'},'access_token':'a'})
            api._auth_request=Mock(return_value={'available':True,'texture_url':URL})
            def changed(url):
                api._accept_session({'account':{'id':'00000000-0000-0000-0000-000000000001','name':'Other'},'access_token':'b'})
                return PNG
            with patch('launcher.threading.Thread',ImmediateThread),patch('launcher.fetch_skin',side_effect=changed):api.refresh_avatar()
            api.emit.assert_not_called()
            self.assertIsNone(api.profile()['skin_data'])

    def test_old_server_or_offline_skin_does_not_break_profile(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_mode':'authme_bridge'},Path(d));api.emit=Mock()
            api._accept_session({'account':{'id':'185fb4a0-40e2-32d2-acaf-60e26d2869ba','name':'Zaire'},'access_token':'a'})
            api._auth_request=Mock(side_effect=Exception('optional endpoint unavailable'))
            with patch('launcher.threading.Thread',ImmediateThread):self.assertTrue(api.refresh_avatar()['ok'])
            self.assertTrue(api.profile()['connected'])
            self.assertFalse(api._avatar_pending)

