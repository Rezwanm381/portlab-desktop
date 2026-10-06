"""Repeatable Windows folder build, preserving replaceable dynamic Qt DLLs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true', help='Test the built executable and its renderer')
    parser.add_argument('--output', type=Path, help='Smoke diagnostics folder; defaults to verification/packaged_vVERSION')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from portlab import __version__
    if sys.platform != 'win32':
        parser.error('Build this release on Windows')
    if not (root / 'licenses' / 'THIRD_PARTY_NOTICES.md').is_file():
        parser.error('The local license notices are missing')
    dist = root / 'dist' / ('v' + __version__)
    build = root / 'build' / ('v' + __version__)
    for target in (dist, build):
        if not target.resolve().is_relative_to(root):
            raise ValueError('Build output must remain within the project')
    environment = os.environ.copy()
    environment['PYINSTALLER_CONFIG_DIR'] = str(build / 'pyinstaller-cache')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--distpath', str(dist), '--workpath', str(build),
                    str(root / 'PortLab.spec')], cwd=root, env=environment, check=True)
    release = dist / 'PortLab'
    # Put input templates and notices beside the executable as convenient user
    # documents; internal copies remain available to bundled application code.
    for name in ('templates', 'licenses', 'examples', 'docs'):
        if (root / name).is_dir():
            shutil.copytree(root / name, release / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('README.md', 'USER_GUIDE.md', 'MODEL_REVIEW.md', 'VALIDATION.md',
                 'LICENSE_STATUS.md', 'CITATION.cff', 'CHANGELOG.md', 'CONTRIBUTING.md'):
        source = root / name
        if source.is_file():
            shutil.copy2(source, release / name)
    files = [path for path in release.rglob('*') if path.is_file()]
    record = {'version': __version__, 'built_utc': datetime.now(timezone.utc).isoformat(),
              'python': sys.version, 'platform': sys.platform, 'architecture': 'x64',
              'release_bytes': sum(path.stat().st_size for path in files), 'release_files': len(files),
              'entrypoint': 'PortLab.exe', 'format': 'directory; keep _internal alongside executable',
              'application_sha256': hashlib.sha256((release / 'PortLab.exe').read_bytes()).hexdigest(),
              'packages': {name: importlib.metadata.version(name) for name in
                           ('PySide6', 'shiboken6', 'Panda3D', 'simpy', 'openpyxl', 'psutil', 'pyinstaller')}}
    (release / 'BUILD_INFO.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))
    if args.smoke:
        requested_output = args.output or Path('verification') / ('packaged_v' + __version__)
        output = requested_output if requested_output.is_absolute() else root / requested_output
        output.mkdir(parents=True, exist_ok=True)
        run_started_ns = time.time_ns()
        subprocess.run([str(release / 'PortLab.exe'), '--smoke', '--output', str(output)],
                       cwd=root, check=True, timeout=180)
        report = output / 'smoke_diagnostics.json'
        if not report.is_file():
            raise RuntimeError('Packaged smoke test did not produce diagnostics')
        if report.stat().st_mtime_ns < run_started_ns:
            raise RuntimeError('Packaged smoke test left stale diagnostics from an earlier run')
        diagnostics = json.loads(report.read_text(encoding='utf-8'))
        if diagnostics.get('visual', {}).get('error'):
            raise RuntimeError('Packaged renderer failed: ' + diagnostics['visual']['error'])
        from portlab.engine import REQUIRED_PHYSICAL_CHECKS
        if diagnostics.get('version') != __version__ or not all(diagnostics.get('checks', {}).get(name) is True for name in REQUIRED_PHYSICAL_CHECKS):
            raise RuntimeError('Packaged version/accounting checks failed')
        print('Packaged integration and OpenGL rendering passed')


if __name__ == '__main__':
    main()
