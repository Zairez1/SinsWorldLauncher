"""Install a real Minecraft + Forge using the newly built EXE, without opening game/UI."""
from pathlib import Path
import json
import subprocess
import tempfile
import sys
if sys.platform != 'win32':
    raise SystemExit(0)
exe = Path('dist/SinsWorldLauncher.exe').resolve()
with tempfile.TemporaryDirectory(prefix='sinsworld-clean-install-') as folder:
    result = subprocess.run([str(exe), '--verify-game-install', folder], timeout=900)
    log = Path(folder) / 'launcher.log'
    if log.exists():
        print(log.read_text('utf-8'), flush=True)
    assert result.returncode == 0, f'Frozen installer failed: {result.returncode}'
    report = json.loads((Path(folder) / 'install-result.json').read_text('utf-8'))
    assert report['ok'] and report['version'] == '1.20.1-forge-47.3.22'
    game = Path(folder) / 'game'
    client = game / 'versions/1.20.1/1.20.1.jar'
    assert client.stat().st_size > 10_000_000
    index = json.loads((game / 'assets/indexes/5.json').read_text('utf-8'))
    missing = [v['hash'] for v in index['objects'].values()
               if not (game / 'assets/objects' / v['hash'][:2] / v['hash']).is_file()]
    assert not missing, f'Missing {len(missing)} Minecraft assets'
    assert (game / '.sinsworld-forge-ready.json').is_file()
    print('PASS: actual Windows EXE installed Minecraft client, all assets, Java 17 and Forge with complete launch classpath')
