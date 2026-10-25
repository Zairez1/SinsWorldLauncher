"""Generate GitHub Release manifests from actual files, no hand-entered hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from github_updates import mod_path, version_tuple

def file_entry(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return {'asset': path.name, 'sha256': h.hexdigest(), 'size': path.stat().st_size}

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    launcher = sub.add_parser('launcher')
    launcher.add_argument('--exe', type=Path, required=True)
    launcher.add_argument('--version', required=True)
    launcher.add_argument('--output', type=Path, default=Path('launcher-manifest.json'))
    mods = sub.add_parser('mods')
    mods.add_argument('--folder', type=Path, required=True)
    mods.add_argument('--minecraft', default='1.20.1')
    mods.add_argument('--forge', default='1.20.1-47.3.22')
    mods.add_argument('--output', type=Path, default=Path('mods-manifest.json'))
    args = parser.parse_args()
    if args.command == 'launcher':
        version_tuple(args.version)
        if args.exe.name != 'SinsWorldLauncher.exe':
            parser.error('EXE должен называться SinsWorldLauncher.exe')
        data = {'schema': 1, 'version': args.version, **file_entry(args.exe)}
    else:
        jars = sorted(args.folder.glob('*.jar'))
        if not jars:
            parser.error('В папке нет JAR-модов')
        names = set()
        entries = []
        for path in jars:
            name = 'mods/' + path.name
            mod_path(Path.cwd() / 'validation-root', name)
            if name.casefold() in names:
                parser.error('Имена модов повторяются без учёта регистра')
            names.add(name.casefold())
            entries.append({'path': name, **file_entry(path)})
        data = {'schema': 1, 'minecraft_version': args.minecraft, 'forge_version': args.forge, 'files': entries}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(args.output)

if __name__ == '__main__':
    main()
