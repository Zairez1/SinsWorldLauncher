# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
base = Path(SPECPATH)
datas = [('SinsWorldLauncher.ico', '.'), ('config.json', '.'), ('apply_update.ps1', '.'), ('ui', 'ui')]
# Optional private auth integration files, never downloaded from arbitrary URLs.
if (base / 'authlib-injector.jar').is_file():
    datas.append(('authlib-injector.jar', '.'))
binaries, hiddenimports = [], []
for package in ('webview', 'minecraft_launcher_lib', 'keyring'):
    collected = collect_all(package)
    datas += collected[0]; binaries += collected[1]; hiddenimports += collected[2]
a = Analysis(['launcher.py'], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='SinsWorldLauncher',
          debug=False, strip=False, upx=False, console=False, icon=['SinsWorldLauncher.ico'])
