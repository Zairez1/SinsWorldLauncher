import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import launcher
from launcher import LauncherAPI

class ThemeAndUpdateTests(unittest.TestCase):
    def test_server_selection_persists_and_naruto_never_launches_sinsworld(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({},Path(d))
            self.assertTrue(api.select_server('naruto')['ok'])
            self.assertEqual(json.loads((Path(d)/'config.json').read_text())['selected_server'],'naruto')
            self.assertFalse(api.play()['ok'])
            self.assertFalse(api.play(True)['ok'])
            self.assertFalse(api.select_server('unknown')['ok'])
            self.assertTrue(api.select_server('sinsworld')['ok'])
            api._busy=True
            self.assertFalse(api.select_server('naruto')['ok'])

    def test_solo_selection_persists_and_blocks_game_and_mod_sync(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({},Path(d))
            self.assertTrue(api.select_server('solo')['ok'])
            self.assertEqual(json.loads((Path(d)/'config.json').read_text())['selected_server'],'solo')
            for only_mods in (False,True):
                result=api.play(only_mods)
                self.assertFalse(result['ok'])
                self.assertIn('Solo Leveling',result['error'])
            api._busy=True
            self.assertFalse(api.select_server('sinsworld')['ok'])

    def test_required_update_blocks_game_and_startup_bypass(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'launcher_repository':'owner/repo','launcher_release':'auth-bridge-current'},Path(d))
            api._task=lambda action,**kw:action()
            api.emit=Mock()
            with patch('launcher.GitHub') as gh:
                gh.return_value.launcher_info.return_value=({'version':'9.0.0'},{'name':'SinsWorldLauncher.exe'})
                api.check_launcher()
            self.assertTrue(api._update_required)
            self.assertEqual(api.emit.call_args.args[0],'update_required')
            self.assertFalse(api.play()['ok'])
            self.assertFalse(api.continue_to_main()['ok'])

    def test_download_button_installs_checked_exe_into_current_path(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'launcher_repository':'owner/repo','launcher_release':'auth-bridge-current'},Path(d))
            api._task=lambda action,**kw:action()
            api._window=Mock()
            manifest={'version':'9.0.0','sha256':'a'*64,'size':10}
            with patch('launcher.sys.platform','win32'),patch.object(launcher.sys,'frozen',True,create=True),patch('launcher.GitHub') as gh,patch('launcher.subprocess.Popen') as proc,patch('launcher.time.sleep'):
                gh.return_value.launcher_info.return_value=(manifest,{'name':'SinsWorldLauncher.exe'})
                def download(asset,path,*args):path.write_bytes(b'new binary')
                gh.return_value.download.side_effect=download
                api.install_launcher_update()
                args=proc.call_args.args[0]
                self.assertIn('-DeleteBackup',args)
                self.assertIn('-ReadySignal',args)
                self.assertEqual(args[args.index('-Target')+1],str(Path(launcher.sys.executable).resolve()))
                self.assertEqual(proc.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'],'1')
                api._window.destroy.assert_called_once()

    def test_legacy_backup_cleanup_only_removes_reserved_windows_exe_backup(self):
        with tempfile.TemporaryDirectory() as d:
            exe=Path(d)/'SinsWorldLauncher.exe';exe.write_bytes(b'MZnew')
            backup=Path(str(exe)+'.previous');backup.write_bytes(b'MZold')
            unrelated=Path(d)/'Other.exe';unrelated.write_bytes(b'MZunrelated')
            api=LauncherAPI({},Path(d)/'data')
            with patch('launcher.sys.platform','win32'),patch.object(launcher.sys,'frozen',True,create=True),patch('launcher.sys.executable',str(exe)):
                api._cleanup_legacy_backup()
                self.assertFalse(backup.exists())
                self.assertTrue(unrelated.exists())
                backup.write_bytes(b'not an exe')
                api._cleanup_legacy_backup()
                self.assertTrue(backup.exists())
