"""Build a Windows onedir bundle with licenses, provenance and a SHA256 ZIP."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('Build on Windows x64; PyInstaller is not a cross compiler.')
    if platform.python_version() != '3.13.9':
        parser.error('Use CPython 3.13.9 to reproduce this release.')
    pins = {}
    for line in (ROOT / 'requirements-build.txt').read_text().splitlines():
        if line and not line.startswith('#'):
            name, version = line.split('==')
            if metadata.version(name) != version:
                parser.error(f'{name} must be {version}; install requirements-build.txt')
            pins[name] = version
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True, cwd=ROOT)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / 'TF-Decoder'
    if bundle.exists():
        parser.error(f'{bundle} already exists; move it before building (no destructive cleanup).')
    build_environment = os.environ.copy()
    for key in list(build_environment):
        if key.startswith(('PYTHON', 'CONDA', 'QT_', 'QML')):
            del build_environment[key]
    # Do not resolve DLLs from unrelated software on the developer's PATH.
    build_environment['PATH'] = os.pathsep.join([
        str(Path(sys.executable).parent), sys.base_prefix,
        str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']])
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(output), '--workpath', str(ROOT / 'build' / 'portable'),
                    str(ROOT / 'packaging' / 'tf-decoder.spec')], check=True, cwd=ROOT,
                   env=build_environment)
    for name in ('使用说明.txt', 'THIRD-PARTY-NOTICES.txt'):
        shutil.copy2(ROOT / 'packaging' / name, bundle / name)
    licenses = bundle / 'licenses'
    shutil.copytree(ROOT / 'packaging' / 'licenses', licenses)
    for name in pins:
        dist = metadata.distribution(name)
        destination = licenses / name
        destination.mkdir(exist_ok=True)
        for item in dist.files or ():
            relative = Path(str(item))
            if '.dist-info' in str(relative) and (
                    any(word in str(relative).lower() for word in ('license', 'copying', 'notice'))
                    or relative.name == 'METADATA'):
                # Keep the license tree, including bundled OpenBLAS/runtime notices.
                source = Path(dist.locate_file(item))
                target = destination / Path(*relative.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE_PYTHON.txt'
    if not python_license.exists():
        python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    (licenses / 'Python').mkdir()
    shutil.copy2(python_license, licenses / 'Python' / 'LICENSE.txt')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    provenance = {
        'application': 'TF Decoder', 'version': version,
        'source_revision': run('git', 'rev-parse', 'HEAD'),
        'source_dirty': bool(run('git', 'status', '--porcelain', '--untracked-files=normal')),
        'built_utc': datetime.now(timezone.utc).isoformat(),
        'platform': platform.platform(), 'architecture': platform.machine(),
        'python': sys.version, 'build_interpreter': sys.executable,
        'distributions': pins, 'format': 'PyInstaller onedir, windowed, unmodified dynamic Qt',
        'clean_machine_acceptance': 'pending: development host has Python and MATLAB',
    }
    (bundle / 'BUILD-INFO.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2),
                                            encoding='utf-8')
    files = {str(path.relative_to(bundle)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(bundle.rglob('*')) if path.is_file()}
    (bundle / 'FILES-SHA256.json').write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = output / f'TF-Decoder-{version}-windows-x64.zip'
    if archive.exists():
        parser.error(f'{archive} already exists; move it before building.')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zip_file:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                zip_file.write(path, path.relative_to(output))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'sha256': digest, 'bytes': archive.stat().st_size}))


if __name__ == '__main__':
    main()
