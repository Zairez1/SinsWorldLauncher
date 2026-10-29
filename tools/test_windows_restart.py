"""Regression test: actual onefile EXE -> updater helper -> fresh onefile runtime."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time

if sys.platform != 'win32':
    print('Windows restart integration test skipped on this OS')
    raise SystemExit(0)

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
if len(sys.argv) > 1:
    marker, helper = Path(sys.argv[1]), Path(sys.argv[2])
    marker.with_suffix('.old.json').write_text(json.dumps({'runtime': sys._MEIPASS}))
    source = marker.parent / 'replacement.exe'
    shutil.copyfile(sys.executable, source)
    env = dict(os.environ, SINSWORLD_UPDATE_TEST_RESULT=str(marker))
    # Deliberately keep the stale inherited PyInstaller variables. The actual
    # helper must independently force fresh extraction, even for an older caller.
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File', str(helper),
        '-ParentPid', str(os.getpid()), '-BootloaderPid', str(os.getppid()),
        '-Source', str(source), '-Target', sys.executable,
        '-ExpectedSha256', hashlib.sha256(source.read_bytes()).hexdigest(),
        '-ReadySignal', str(marker.with_suffix('.ready.json')), '-DeleteBackup'], env=env,
        creationflags=subprocess.CREATE_NO_WINDOW)
else:
    marker = Path(os.environ['SINSWORLD_UPDATE_TEST_RESULT'])
    import encodings, ctypes
    marker.write_text(json.dumps({'runtime': sys._MEIPASS, 'imports': True}))
    Path(os.environ['SINSWORLD_UPDATE_READY_FILE']).write_text(json.dumps({'sha256':os.environ['SINSWORLD_UPDATE_EXPECTED_HASH']}))
'''
with tempfile.TemporaryDirectory(prefix='sinsworld-restart-') as temp:
    temp = Path(temp)
    script = temp / 'probe.py'
    script.write_text(PROBE)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--onefile', '--noconsole',
                    '--distpath', str(temp / 'dist'), '--workpath', str(temp / 'build'),
                    '--specpath', str(temp), str(script)], check=True)
    marker = temp / 'result.json'
    exe = temp / 'dist' / 'probe.exe'
    subprocess.run([str(exe), str(marker), str(ROOT / 'apply_update.ps1')], check=True, timeout=40)
    deadline = time.monotonic() + 45
    while not marker.exists() and time.monotonic() < deadline:
        log = temp / 'update.log'
        if log.exists() and 'Update installed successfully' not in log.read_text(errors='replace'):
            raise AssertionError(log.read_text(errors='replace'))
        time.sleep(.25)
    if not marker.exists():
        log = temp / 'update.log'
        raise AssertionError('Replacement did not start: ' + (log.read_text(errors='replace') if log.exists() else 'no helper log'))
    old = json.loads(marker.with_suffix('.old.json').read_text())
    new = json.loads(marker.read_text())
    assert new['imports'] and new['runtime'] != old['runtime'], (old, new)
    assert not Path(old['runtime']).exists(), 'Old runtime was not cleaned up before restart'
    deadline = time.monotonic() + 10
    while exe.with_name(exe.name + '.previous').exists() and time.monotonic() < deadline: time.sleep(.1)
    assert not exe.with_name(exe.name + '.previous').exists(), 'Old EXE was not removed after ready acknowledgement'
    time.sleep(1)  # Allow the short-lived replacement bootloader to finish cleanup.
    print('PASS: real Windows onefile self-update restarted after old runtime cleanup; fresh imports and automatic old EXE removal verified')
