from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import launcher
from launcher import LauncherAPI
from test_skin_avatar import PNG,URL

class SkinChangeTests(unittest.TestCase):
    def session(self,id='185fb4a0-40e2-32d2-acaf-60e26d2869ba',name='Zaire'):
        return {'account':{'id':id,'name':name},'access_token':'account-token'}

    def test_picker_requires_account_validates_png_and_upload_is_owned(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_mode':'authme_bridge'},Path(d));api._window=Mock()
            self.assertFalse(api.choose_skin_png()['ok'])
            api._accept_session(self.session());p=Path(d)/'skin.png';p.write_bytes(PNG)
            api._window.create_file_dialog.return_value=[str(p)]
            result=api.choose_skin_png();self.assertTrue(result['ok']);self.assertEqual(result['name'],'skin.png')
            api._accept_session(self.session('00000000-0000-0000-0000-000000000001','Other'))
            self.assertFalse(api.change_skin('png')['ok'])

    def test_skin_is_confirmed_before_avatar_and_no_client_identity_is_sent(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_mode':'authme_bridge'},Path(d));api._accept_session(self.session());api.emit=Mock()
            api._task=lambda work:(work() or {'ok':True})
            api._auth_request=Mock(side_effect=[{'job':'job-id'},{'state':'done','texture_url':URL}])
            with patch.object(api._cancel,'wait',return_value=False),patch('launcher.fetch_skin',return_value=PNG):
                self.assertTrue(api.change_skin('name','Notch','classic')['ok'])
            route,body,token=api._auth_request.call_args_list[0].args
            self.assertEqual(route,'/v1/profile/skin/change');self.assertEqual(body,{'mode':'name','value':'Notch','variant':'classic'})
            self.assertEqual(token,'account-token')
            self.assertTrue(api.profile()['skin_data'].startswith('data:image/png;base64,'))
            self.assertEqual(api.emit.call_args.kwargs['state'],'done')

    def test_no_success_message_if_generation_fails(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_mode':'authme_bridge'},Path(d));api._accept_session(self.session());api.emit=Mock();api._task=lambda work:(work() or {'ok':True})
            api._auth_request=Mock(side_effect=[{'job':'job-id'},{'state':'error','message':'Generation unavailable'}])
            with patch.object(api._cancel,'wait',return_value=False):api.change_skin('name','Notch')
            self.assertEqual(api.emit.call_args.kwargs['state'],'error')
            self.assertIsNone(api.profile()['skin_data'])
