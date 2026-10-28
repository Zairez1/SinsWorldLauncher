import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import launcher
from launcher import LauncherAPI

class AuthUpdaterTests(unittest.TestCase):
    def test_existing_preview_settings_migrate_to_auth_update_channel(self):
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'config.json'
            config.write_text(json.dumps({'launcher_repository':'old/repo','launcher_release':'launcher-current','ram_mb':8192}))
            with patch.object(launcher,'CONFIG_FILE',config):result=launcher.load_config()
            self.assertEqual(result['launcher_repository'],'Zairez1/SinsWorldLauncher')
            self.assertEqual(result['launcher_release'],'auth-bridge-current')
            self.assertEqual(result['ram_mb'],8192)

    def test_preview_profile_no_longer_skips_startup_update_checks(self):
        with tempfile.TemporaryDirectory() as d:
            api=LauncherAPI({'launcher_repository':'owner/repo','launcher_release':'auth-bridge-current'},Path(d))
            api._preview=True
            api._task=lambda action,**kw:action()
            api.emit=Mock();api._restore_session=Mock()
            with patch('launcher.GitHub') as github,patch('launcher.time.sleep'):
                github.return_value.launcher_info.return_value=None
                api.check_launcher()
                self.assertEqual(github.call_args.args,('owner/repo','auth-bridge-current'))
                github.return_value.launcher_info.assert_called_once_with(launcher.VERSION)
            self.assertEqual([c.args[0] for c in api.emit.call_args_list],['splash','splash','main'])
            api._restore_session.assert_called_once()
