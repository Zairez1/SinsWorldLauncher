import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from auth_bridge import validate_session, cleanup_ticket, server_key
from github_updates import UpdateError
from launcher import LauncherAPI

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.name='Zaire'
        self.id=str(uuid.UUID(bytes=hashlib.md5(b'OfflinePlayer:Zaire').digest(),version=3))
        self.account={'name':self.name,'id':self.id}
        self.session={'mode':'authme_bridge','name':self.name,'uuid':self.id,'ticket':'A'*43,
                      'server':'sinsworld.minecraft.best','expires_at':90000}
    def test_valid_ticket_is_minimal_and_pinned_to_server(self):
        proof=validate_session(self.session,self.account,'sinsworld.minecraft.best:25565',now_ms=10000)
        self.assertEqual(set(proof),{'ticket','server','uuid','expires_at'})
    def test_wrong_identity_server_expiry_and_ticket_rejected(self):
        for change in ({'name':'Other'},{'uuid':str(uuid.uuid4())},{'server':'evil.example.org'},
                       {'expires_at':14000},{'expires_at':150000},{'ticket':'bad'}, {'mode':'unknown'}):
            with self.subTest(change=change), self.assertRaises(UpdateError):
                validate_session({**self.session,**change},self.account,'sinsworld.minecraft.best',now_ms=10000)
    def test_rejects_server_url_injection(self):
        for value in ('user@example.org','https://example.org','evil.org/path','bad host', 'server:99999'):
            with self.assertRaises(UpdateError):server_key(value)
    def test_ticket_cleanup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ticket.json';path.write_text('secret')
            cleanup_ticket(path);cleanup_ticket(path);self.assertFalse(path.exists())
    def test_legacy_short_authme_password_can_login_but_not_register(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_mode':'authme_bridge','auth_api_url':'https://auth.example.org'},Path(d))
            with patch.object(api,'_auth_request',return_value={'account':self.account,'access_token':'t'}):
                self.assertTrue(api.account('login','Zaire','abcde')['ok'])
                self.assertFalse(api.account('register','NewPlayer','abcde','abcde')['ok'])
                self.assertFalse(api.account('register','NewPlayer','A'*31,'A'*31)['ok'])
    def test_exit_removes_ticket_even_when_launcher_is_closed(self):
        import types
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ticket.json';path.write_text('secret')
            api=LauncherAPI({},Path(d));api._cancel.set()
            api._watch_game(types.SimpleNamespace(wait=lambda:0),path)
            self.assertFalse(path.exists())

class RefreshTests(unittest.TestCase):
    def test_access_expiry_refreshes_once_and_never_sends_refresh_as_game_token(self):
        import io, urllib.error, types
        responses=[urllib.error.HTTPError('https://auth.example/v1/game/session',401,'expired',{},io.BytesIO(b'{}')),
                   types.SimpleNamespace(),types.SimpleNamespace()]
        class Response:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return json.dumps(self.data).encode()
        ident={'id':str(uuid.uuid4()),'name':'Zaire'}
        replies=[responses[0],Response({'account':ident,'access_token':'new-access','refresh_token':'new-refresh'}),Response({'ticket':'ticket'})]
        sent=[]
        class Opener:
            def open(self,req,timeout):
                sent.append((req.full_url,req.get_header('Authorization')))
                value=replies.pop(0)
                if isinstance(value,Exception):raise value
                return value
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'auth_api_url':'https://auth.example'},Path(d));api._session={'account':ident,'access_token':'old-access','refresh_token':'old-refresh'}
            with patch('launcher.urllib.request.build_opener',return_value=Opener()), patch.dict('sys.modules',{'keyring':types.SimpleNamespace(get_password=lambda *a:None)}):
                result=api._auth_request('/v1/game/session',{},'old-access')
            self.assertEqual(result,{'ticket':'ticket'})
            self.assertEqual(sent,[('https://auth.example/v1/game/session','Bearer old-access'),('https://auth.example/v1/sessions/refresh',None),('https://auth.example/v1/game/session','Bearer new-access')])

if __name__=='__main__':unittest.main()
