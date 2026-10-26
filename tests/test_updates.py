import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from github_updates import GitHub, UpdateError, Cancelled, checksum, mod_path, sync_mods, version_tuple
from launcher import LauncherAPI, restart_environment

class Response(io.BytesIO):
    pass

class Opener:
    def __init__(self, data):
        self.data = data
    def open(self, req, timeout):
        return Response(self.data[req.full_url])

class Fixture:
    def __init__(self, files, forge='1.20.1-47.3.22'):
        self.base = 'https://github.com/owner/repo/releases/download/mods-current/'
        self.entries = [{'path': 'mods/' + name, 'asset': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                        for name, data in files.items()]
        manifest = {'schema': 1, 'minecraft_version': '1.20.1', 'forge_version': forge, 'files': self.entries}
        self.assets = [{'name': name, 'size': len(data), 'browser_download_url': self.base + name} for name, data in files.items()]
        self.assets.append({'name': 'mods-manifest.json', 'browser_download_url': self.base + 'mods-manifest.json', 'size': 1})
        self.data = {self.base + name: value for name, value in files.items()}
        self.data[self.base + 'mods-manifest.json'] = json.dumps(manifest).encode()
        self.data['https://api.github.com/repos/owner/repo/releases/tags/mods-current'] = json.dumps({'assets': self.assets}).encode()
        self.client = GitHub('owner/repo', 'mods-current', opener=Opener(self.data))
    def sync(self, game):
        return sync_mods(self.client, game, '1.20.1', '1.20.1-47.3.22')

class Updates(unittest.TestCase):
    def test_restart_forces_new_pyinstaller_runtime(self):
        import os
        with patch.dict(os.environ, {'_PYI_APPLICATION_HOME_DIR': 'deleted-runtime', 'PYINSTALLER_RESET_ENVIRONMENT': '0'}):
            env = restart_environment()
            self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'], '1')
            self.assertEqual(os.environ['PYINSTALLER_RESET_ENVIRONMENT'], '0')

    def test_versions_numeric(self):
        self.assertGreater(version_tuple('1.10.0'), version_tuple('v1.9.9'))
        for bad in ('1.0', 'latest', None, '1.2.3-beta'):
            with self.assertRaises(UpdateError):
                version_tuple(bad)

    def test_traversal_windows_names_and_case_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            for path in ('../x.jar', '/mods/a.jar', 'mods/../x.jar', 'mods/a/b.jar', 'mods\\a.jar',
                         'mods/CON.jar', 'mods/a:stream.jar', 'mods/./a.jar', 'mods//a.jar', 'config/a.jar'):
                with self.assertRaises(UpdateError, msg=path):
                    mod_path(Path(d), path)
            f = Fixture({'a.jar': b'one', 'A.jar': b'two'})
            with self.assertRaises(UpdateError):
                f.sync(Path(d))

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            game = Path(d)
            try:
                (game / 'mods').symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('Symlinks unavailable')
            with self.assertRaises(UpdateError):
                mod_path(game, 'mods/evil.jar')

    def test_mods_install_skip_and_managed_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            Fixture({'old.jar': b'old', 'keep.jar': b'keep'}).sync(game)
            (game / 'mods' / 'personal.jar').write_bytes(b'my mod')
            f = Fixture({'new.jar': b'new', 'keep.jar': b'keep'})
            f.sync(game)
            self.assertFalse((game / 'mods' / 'old.jar').exists())
            self.assertFalse((game / 'mods' / 'personal.jar').exists())
            with patch.object(f.client, 'download', side_effect=AssertionError('Unchanged mods downloaded')):
                f.sync(game)

    def test_nested_extra_removed_and_other_files_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            extra = game / 'mods' / 'nested' / 'extra.JAR'
            extra.parent.mkdir(parents=True)
            extra.write_bytes(b'extra')
            settings = game / 'mods' / 'settings.json'
            settings.write_bytes(b'config')
            save = game / 'saves' / 'world' / 'level.dat'
            save.parent.mkdir(parents=True)
            save.write_bytes(b'world')
            Fixture({'allowed.jar': b'allowed'}).sync(game)
            self.assertFalse(extra.exists())
            self.assertEqual(settings.read_bytes(), b'config')
            self.assertEqual(save.read_bytes(), b'world')

    def test_unlisted_modified_mod_is_removed_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            Fixture({'old.jar': b'old'}).sync(game)
            (game / 'mods' / 'old.jar').write_bytes(b'user modification')
            Fixture({'new.jar': b'new'}).sync(game)
            self.assertFalse((game / 'mods' / 'old.jar').exists())

    def test_hash_failure_preserves_entire_old_pack(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            Fixture({'first.jar': b'old-one', 'second.jar': b'old-two'}).sync(game)
            (game / 'mods' / 'extra.jar').write_bytes(b'extra')
            state = (game / '.sinsworld-managed-mods.json').read_bytes()
            f = Fixture({'first.jar': b'new-one', 'second.jar': b'new-two'})
            f.data[f.base + 'second.jar'] = b'BAD-two'
            with self.assertRaises(UpdateError):
                f.sync(game)
            self.assertEqual((game / 'mods' / 'first.jar').read_bytes(), b'old-one')
            self.assertEqual((game / 'mods' / 'second.jar').read_bytes(), b'old-two')
            self.assertEqual((game / '.sinsworld-managed-mods.json').read_bytes(), state)
            self.assertEqual((game / 'mods' / 'extra.jar').read_bytes(), b'extra')

    def test_commit_failure_rolls_back(self):
        import github_updates
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            Fixture({'first.jar': b'old-one', 'second.jar': b'old-two'}).sync(game)
            f = Fixture({'first.jar': b'new-one', 'second.jar': b'new-two'})
            original = github_updates.os.replace
            def fail(source, target):
                if Path(source).name == 'second.jar' and Path(source).parent.name.startswith('.sw-mods-'):
                    raise OSError('Simulated disk error')
                return original(source, target)
            with patch.object(github_updates.os, 'replace', side_effect=fail):
                with self.assertRaises(OSError):
                    f.sync(game)
            self.assertEqual((game / 'mods' / 'first.jar').read_bytes(), b'old-one')
            self.assertEqual((game / 'mods' / 'second.jar').read_bytes(), b'old-two')

    def test_forge_mismatch_does_not_install(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(UpdateError):
                Fixture({'a.jar': b'jar'}, forge='1.20.1-47.1.0').sync(Path(d))
            self.assertFalse((Path(d) / 'mods').exists())

    def test_cancelled_update_leaves_no_download(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = Fixture({'a.jar': b'jar'})
            fixture.client.cancel.set()
            with self.assertRaises(Cancelled):
                fixture.sync(Path(d))
            self.assertFalse((Path(d) / 'mods').exists())

    def test_foreign_asset_url_rejected(self):
        f = Fixture({'a.jar': b'a'})
        f.data['https://api.github.com/repos/owner/repo/releases/tags/mods-current'] = json.dumps({'assets': [
            {'name': 'a.jar', 'browser_download_url': 'https://github.com/attacker/repo/releases/download/a/a.jar'}]}).encode()
        with self.assertRaises(UpdateError):
            f.client.get_release()

    def test_launcher_version_and_hash(self):
        payload = b'MZ-test-executable'
        base = 'https://github.com/owner/repo/releases/download/launcher-current/'
        manifest = {'schema': 1, 'version': '1.1.0', 'asset': 'SinsWorldLauncher.exe',
                    'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}
        assets = [{'name': name, 'size': len(data), 'browser_download_url': base + name}
                  for name, data in [('SinsWorldLauncher.exe', payload), ('launcher-manifest.json', json.dumps(manifest).encode())]]
        data = {base + 'SinsWorldLauncher.exe': payload, base + 'launcher-manifest.json': json.dumps(manifest).encode(),
                'https://api.github.com/repos/owner/repo/releases/tags/launcher-current': json.dumps({'assets': assets}).encode()}
        client = GitHub('owner/repo', 'launcher-current', opener=Opener(data))
        self.assertIsNone(client.launcher_info('1.1.0'))
        info, asset = client.launcher_info('1.0.0')
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'SinsWorldLauncher.exe'
            client.download(asset, path, info['sha256'], info['size'])
            self.assertEqual(path.read_bytes(), payload)
            path.write_bytes(b'previous')
            with self.assertRaises(UpdateError):
                client.download(asset, path, '0' * 64, info['size'])
            self.assertEqual(path.read_bytes(), b'previous')
            self.assertFalse(path.with_name(path.name + '.part').exists())

    def test_settings_persist_and_auth_not_faked(self):
        with tempfile.TemporaryDirectory() as d:
            config = {'ram_mb': 6144, 'resolution_width': 1280, 'resolution_height': 720, 'auth_api_url': ''}
            api = LauncherAPI(config, Path(d))
            result = api.save_settings({'launcher_repository': 'owner/launcher', 'mods_repository': 'owner/mods', 'ram_mb': 4096})
            self.assertTrue(result['ok'], result)
            self.assertEqual(json.loads((Path(d) / 'config.json').read_text())['ram_mb'], 4096)
            result = api.account('register', 'Steve', 'long password 123', 'long password 123')
            self.assertFalse(result['ok'])
            self.assertIsNone(api._session)
            result = api.account('local', 'Steve')
            self.assertTrue(result['ok'])
            self.assertFalse(result['profile']['connected'])

    def test_missing_or_wrong_system_java_installs_private_java17(self):
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({'java_path': ''}, Path(d))
            runtime = types.SimpleNamespace(get_executable_path=unittest.mock.Mock(side_effect=[None, '/private/java']),
                                            install_jvm_runtime=unittest.mock.Mock())
            mc = types.SimpleNamespace(runtime=runtime)
            with patch('launcher.shutil.which', return_value='/system/java'), \
                 patch('launcher.subprocess.run', side_effect=[
                     types.SimpleNamespace(returncode=0, stdout='', stderr='openjdk version "21.0.1"'),
                     types.SimpleNamespace(returncode=0, stdout='', stderr='openjdk version "17.0.12"')]):
                self.assertEqual(api._java17(mc, Path(d), {}), '/private/java')
                runtime.install_jvm_runtime.assert_called_once_with('java-runtime-gamma', d, callback={})

    def test_java_download_failure_never_returns_invalid_java(self):
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({}, Path(d))
            runtime = types.SimpleNamespace(get_executable_path=unittest.mock.Mock(return_value=None),
                                            install_jvm_runtime=unittest.mock.Mock(side_effect=OSError('network failed')))
            with patch('launcher.shutil.which', return_value=None), self.assertRaises(OSError):
                api._java17(types.SimpleNamespace(runtime=runtime), Path(d), {})

    def test_existing_minecraft_launch_keeps_java_and_quickplay(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            version = '1.20.1-forge-47.3.22'
            path = data / 'game' / 'versions' / version
            path.mkdir(parents=True)
            (path / (version + '.json')).write_text('{}')
            base = data / 'game' / 'versions' / '1.20.1'
            base.mkdir(parents=True)
            (base / '1.20.1.jar').write_bytes(b'client')
            (data / 'game' / '.sinsworld-forge-ready.json').write_text(json.dumps({'forge': '1.20.1-47.3.22'}))
            config = {'minecraft_version': '1.20.1', 'forge_version': '1.20.1-47.3.22',
                      'ram_mb': 4096, 'resolution_width': 1280, 'resolution_height': 720,
                      'java_path': '/fake/java', 'check_updates': False, 'fullscreen': True,
                      'nickname': 'Steve', 'auth_api_url': '', 'server_address': 'play.example.org:25565', 'mods_repository': 'owner/repo'}
            api = LauncherAPI(config, data)
            get_command = unittest.mock.Mock(return_value=['/fake/java', '-jar', 'client.jar'])
            mc = types.SimpleNamespace(command=types.SimpleNamespace(get_minecraft_command=get_command),
                                       install=types.SimpleNamespace(install_minecraft_version=unittest.mock.Mock()))
            with patch.dict(sys.modules, {'minecraft_launcher_lib': mc}), patch('launcher.sync_mods') as sync, \
                 patch.object(api, '_task', side_effect=lambda f, **kw: f() or {'ok': True}), \
                 patch('launcher.subprocess.run', return_value=types.SimpleNamespace(returncode=0, stdout='', stderr='openjdk version "17.0.12"')), \
                 patch('launcher.subprocess.Popen') as popen:
                api.play()
                command = popen.call_args.args[0]
                sync.assert_called_once()
                self.assertIn('--quickPlayMultiplayer', command)
                self.assertIn('play.example.org:25565', command)
                self.assertIn('--fullscreen', command)
                self.assertEqual(get_command.call_args.args[2]['jvmArguments'], ['-Xmx4096M'])

    def test_missing_client_never_starts_forge(self):
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({'minecraft_version': '1.20.1', 'forge_version': '1.20.1-47.3.22'}, Path(d))
            mc = types.SimpleNamespace(install=types.SimpleNamespace(install_minecraft_version=unittest.mock.Mock()),
                                       forge=types.SimpleNamespace(install_forge_version=unittest.mock.Mock()))
            with self.assertRaises(UpdateError):
                api._prepare_game(mc, Path(d))
            mc.forge.install_forge_version.assert_not_called()

    def test_interrupted_forge_profile_is_repaired(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d)
            api = LauncherAPI({'minecraft_version': '1.20.1', 'forge_version': '1.20.1-47.3.22'}, game)
            for version in ('1.20.1', '1.20.1-forge-47.3.22'):
                folder = game / 'versions' / version
                folder.mkdir(parents=True)
                (folder / (version + '.json')).write_text('{}')
            (game / 'versions' / '1.20.1' / '1.20.1.jar').write_bytes(b'client')
            mc = types.SimpleNamespace(install=types.SimpleNamespace(install_minecraft_version=unittest.mock.Mock()),
                                       forge=types.SimpleNamespace(install_forge_version=unittest.mock.Mock()),
                                       command=types.SimpleNamespace(get_minecraft_command=unittest.mock.Mock(return_value=['java'])))
            with patch.object(api, '_java17', return_value='/private/java'):
                api._prepare_game(mc, game)
                mc.forge.install_forge_version.assert_called_once()
                mc.forge.install_forge_version.reset_mock()
                api._prepare_game(mc, game)
                mc.forge.install_forge_version.assert_not_called()

    def test_forge_processor_failure_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({}, Path(d))
            original = object()
            forge = types.SimpleNamespace(subprocess=original)
            def install(*args, **kwargs):
                forge.subprocess.run(['java', 'processor'])
            forge.install_forge_version = install
            with patch('launcher.subprocess.run', return_value=types.SimpleNamespace(returncode=1)), self.assertRaises(UpdateError):
                api._install_forge(types.SimpleNamespace(forge=forge), '1.20.1-47.3.22', Path(d), 'java')
            self.assertIs(forge.subprocess, original)

    def test_failed_background_task_retains_error_for_polling(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({}, Path(d))
            def fail():
                raise OSError('Test network failure')
            api._task(fail)
            deadline = time.monotonic() + 3
            while api._busy and time.monotonic() < deadline:
                time.sleep(.01)
            state = api.get_launch_state()
            self.assertFalse(state['busy'])
            self.assertEqual(state['kind'], 'error')
            self.assertIn('Test network failure', state['message'])
            self.assertIn('Test network failure', (Path(d) / 'launcher.log').read_text())

    def test_launcher_hides_then_restores_after_real_child_exit_and_crash(self):
        import subprocess
        for code in (0, 1):
            with self.subTest(exit_code=code), tempfile.TemporaryDirectory() as d:
                api = LauncherAPI({}, Path(d))
                window = unittest.mock.Mock()
                api._window = window
                process = subprocess.Popen([sys.executable, '-c', 'import time,sys;time.sleep(.05);sys.exit(' + str(code) + ')'])
                with patch.object(api, 'emit') as emit:
                    api._hide_for_game()
                    api._watch_game(process)
                self.assertEqual(window.method_calls, [unittest.mock.call.hide(), unittest.mock.call.show(), unittest.mock.call.restore()])
                self.assertEqual(emit.call_args.args[0], 'game_exited')
                self.assertEqual('ошибкой' in emit.call_args.kwargs['message'], code != 0)
                window.destroy.assert_not_called()

    def test_explicit_launcher_close_prevents_window_resurrection(self):
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({}, Path(d))
            api._window = unittest.mock.Mock()
            api._cancel.set()
            api._watch_game(types.SimpleNamespace(wait=lambda: 0))
            api._window.show.assert_not_called()

    def test_monitor_error_still_restores_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({}, Path(d))
            api._window = unittest.mock.Mock()
            api._watch_game(types.SimpleNamespace(wait=unittest.mock.Mock(side_effect=OSError('wait failed'))))
            api._window.show.assert_called_once()
            api._window.restore.assert_called_once()

    def test_startup_checks_launcher_only_and_preserves_no_update_state(self):
        with tempfile.TemporaryDirectory() as d:
            api = LauncherAPI({'launcher_repository': 'owner/repo'}, Path(d))
            with patch.object(api, '_task', side_effect=lambda f, **kw: f() or {'ok': True}), \
                 patch('launcher.GitHub') as gh, patch('launcher.time.sleep'), patch.object(api, 'emit') as emit, \
                 patch('launcher.sync_mods', side_effect=AssertionError('Mods touched during launcher startup')):
                gh.return_value.launcher_info.return_value = None
                api.check_launcher()
                states = [call.kwargs.get('state') for call in emit.call_args_list if call.args[0] == 'splash']
                self.assertEqual(states, ['checking', 'no-update'])
                self.assertTrue(any(call.args[0] == 'main' for call in emit.call_args_list))

if __name__ == '__main__':
    unittest.main()
