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
                       {'expires_at':14000},{'expires_at':700000},{'ticket':'bad'}, {'mode':'unknown'}):
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

class AuthLaunchTests(unittest.TestCase):
    def test_ticket_is_issued_after_downloads_and_not_put_on_command_line(self):
        import types,sys,time
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as d:
            data=Path(d);name='Zaire';ident='185fb4a0-40e2-32d2-acaf-60e26d2869ba'
            cfg={'auth_mode':'authme_bridge','auth_api_url':'https://auth.example','minecraft_version':'1.20.1','forge_version':'1.20.1-47.3.22',
                 'server_address':'sinsworld.minecraft.best','mods_repository':'owner/repo','ram_mb':4096,'resolution_width':1280,'resolution_height':720}
            api=LauncherAPI(cfg,data);api._session={'account':{'name':name,'id':ident},'access_token':'private-access','refresh_token':'private-refresh'}
            order=[];seen={}
            def command(version,game,options):seen.update(options);return ['java','game']
            mc=types.SimpleNamespace(command=types.SimpleNamespace(get_minecraft_command=command))
            response={'mode':'authme_bridge','name':name,'uuid':ident,'access_token':'0','ticket':'T'*43,'server':'sinsworld.minecraft.best','expires_at':int(time.time()*1000)+90000}
            def session(*a):order.append('ticket');return response
            def prepare(*a):order.append('prepare');return ('forge-profile','java')
            def start(*a,**kw):
                arg=next(a for a in seen['jvmArguments'] if a.startswith('-Dsinsworld.auth.file='))
                file=Path(arg.split('=',1)[1]);self.assertEqual(json.loads(file.read_text())['ticket'],'T'*43);seen['file']=file
                return Mock()
            with patch.dict(sys.modules,{'minecraft_launcher_lib':mc}),patch.object(api,'_task',side_effect=lambda f,**kw:f() or {'ok':True}), \
                 patch.object(api,'_prepare_game',side_effect=prepare),patch('launcher.sync_mods',side_effect=lambda *a,**kw:order.append('mods')), \
                 patch('auth_bridge.install_client',side_effect=lambda *a,**kw:order.append('client')),patch.object(api,'_auth_request',side_effect=session), \
                 patch('launcher.subprocess.Popen',side_effect=start),patch.object(api,'_watch_game'):
                self.assertTrue(api.play()['ok'])
            self.assertEqual(order,['prepare','mods','client','ticket'])
            text=' '.join(seen['jvmArguments'])
            self.assertNotIn('T'*43,text);self.assertNotIn('private-access',text);self.assertEqual(seen['token'],'0')
            cleanup_ticket(seen['file'])

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

class DirectTlsContextTests(unittest.TestCase):
    def test_custom_certificate_never_disables_verification(self):
        import ssl
        from auth_bridge import auth_ssl_context
        context = auth_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        with self.assertRaises(UpdateError):
            auth_ssl_context('invalid', 'https://old:8765', 'https://new:8765')
        for pem in ('garbage', '-----BEGIN PRIVATE KEY-----secret', '-----BEGIN CERTIFICATE-----bad-----END CERTIFICATE-----'):
            with self.subTest(pem=pem), self.assertRaises(UpdateError):
                auth_ssl_context(pem, 'https://127.0.0.1:8765', 'https://127.0.0.1:8765')
