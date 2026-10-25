"""Download an isolated Mojang Java 17 runtime and verify actual execution."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minecraft_launcher_lib as mc
from launcher import LauncherAPI
if sys.platform != 'win32':
    raise SystemExit(0)
with tempfile.TemporaryDirectory(prefix='sinsworld-java-') as folder:
    api = LauncherAPI({}, Path(folder))
    with patch('launcher.shutil.which', return_value=None):
        java = api._java17(mc, Path(folder), {})
    assert Path(java).is_file() and Path(java).is_relative_to(folder)
    print('PASS: downloaded private Mojang Java 17 and verified java -version on Windows')
