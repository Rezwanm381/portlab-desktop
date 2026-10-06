"""Create curated source/examples ZIPs and a smoke-checked Windows delivery.

No archived conversation, thread-specific output, or old verification folder
is an input. Source-only packaging works from a fresh clone without an EXE.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_FILES = ('guam_synthetic_test.csv', 'guam_synthetic_test.xlsx',
                 'guam_synthetic_test.settings.json', 'START_HERE.txt', 'IMPORT_CHECK_RESULTS.json')
REVIEW_FILES = ('forecast_audit.md', 'forecast_audit.json', 'actual_vs_predicted.csv',
                'method_comparison.csv', 'latest_forecasts.csv')
ROOT_FILES = ('README.md', 'VALIDATION.md', 'MODEL_REVIEW.md',
              'LICENSE_STATUS.md', 'CITATION.cff', 'CHANGELOG.md', 'CONTRIBUTING.md',
              'requirements.txt', 'requirements.lock.txt', 'PortLab.spec', 'launcher.py', '.gitignore', '.gitattributes')
OPTIONAL_ROOT_FILES = ('USER_GUIDE.md', 'LICENSE')


def write_zip(path, files, base, prefix):
    base = Path(base).resolve()
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted(set(map(Path, files))):
            if not source.resolve().is_relative_to(base):
                raise ValueError(f'Package input must stay within {base}: {source}')
            archive.write(source, (Path(prefix) / source.relative_to(base)).as_posix())


def curated_inputs(root=ROOT):
    example = root / 'examples' / 'synthetic_import_test'
    review = root / 'docs' / 'model_review'
    required = ([example / name for name in EXAMPLE_FILES] +
                [review / name for name in REVIEW_FILES] +
                [root / name for name in ROOT_FILES])
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError('Curated release inputs are missing: ' + ', '.join(missing))
    return example, review


def source_files(root=ROOT):
    """Explicit allowlist of public source folders; exclude local run outputs."""
    curated_inputs(root)
    files = []
    for folder in ('portlab', 'assets', 'data', 'templates', 'tests', 'tools', 'licenses',
                   '.github', 'docs', 'examples'):
        files.extend(path for path in (root / folder).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix != '.pyc')
    files.extend(root / name for name in ROOT_FILES)
    files.extend(root / name for name in OPTIONAL_ROOT_FILES if (root / name).is_file())
    return sorted(set(files))


def stage_release_documents(release, root=ROOT):
    for name in ROOT_FILES + OPTIONAL_ROOT_FILES:
        if name not in ('PortLab.spec', 'launcher.py', '.gitignore', '.gitattributes', 'requirements.txt', 'requirements.lock.txt'):
            if (root / name).is_file():
                shutil.copy2(root / name, release / name)
    for name in ('examples', 'docs'):
        shutil.copytree(root / name, release / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # Keep this convenience folder used by earlier desktop deliveries too.
    example, review = curated_inputs(root)
    shutil.copytree(review, release / 'model_review', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def check_desktop(release, version, reuse_smoke=False, root=ROOT):
    executable = release / 'PortLab.exe'
    if not executable.is_file():
        raise RuntimeError('Build the Windows application first, or use --source-only')
    build_info = json.loads((release / 'BUILD_INFO.json').read_text(encoding='utf-8'))
    if (build_info.get('version') != version or build_info.get('application_sha256') !=
            hashlib.sha256(executable.read_bytes()).hexdigest()):
        raise RuntimeError('The executable differs from its checked build record')
    diagnostics_path = root / 'verification' / ('packaged_v' + version) / 'smoke_diagnostics.json'
    if not reuse_smoke:
        diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        started_ns = time.time_ns()
        subprocess.run([str(executable), '--smoke', '--output', str(diagnostics_path.parent)],
                       cwd=root, check=True, timeout=180)
        if not diagnostics_path.is_file() or diagnostics_path.stat().st_mtime_ns < started_ns:
            raise RuntimeError('The packaged smoke check did not produce fresh diagnostics')
    diagnostics = json.loads(diagnostics_path.read_text(encoding='utf-8'))
    from portlab.engine import REQUIRED_PHYSICAL_CHECKS
    if (diagnostics.get('version') != version or diagnostics.get('visual', {}).get('error') or
            not all(diagnostics.get('checks', {}).get(name) is True for name in REQUIRED_PHYSICAL_CHECKS)):
        raise RuntimeError('Packaged rendering/accounting checks must pass before desktop delivery')


def main():
    sys.path.insert(0, str(ROOT))
    from portlab import __version__
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true',
                        help='Package curated source/examples without building or claiming a tested Windows application')
    parser.add_argument('--reuse-smoke', action='store_true',
                        help='Use an existing passing packaged smoke check for this version instead of running it again')
    args = parser.parse_args()
    example, review = curated_inputs()
    files = source_files()
    release = ROOT / 'dist' / ('v' + __version__) / 'PortLab'
    if not args.source_only:
        stage_release_documents(release)
        check_desktop(release, __version__, args.reuse_smoke)
    delivery = ROOT / 'delivery'
    delivery.mkdir(exist_ok=True)
    source_zip = delivery / f'PortLab_Source_v{__version__}.zip'
    synthetic_zip = delivery / 'PortLab_Synthetic_Test.zip'
    write_zip(source_zip, files, ROOT, 'PortLab_Source')
    write_zip(synthetic_zip, [path for path in example.rglob('*') if path.is_file()
                            and '__pycache__' not in path.parts and path.suffix != '.pyc'],
              example, 'PortLab_Synthetic_Test')
    packages = [source_zip, synthetic_zip]
    if not args.source_only:
        desktop = delivery / f'PortLab_Desktop_v{__version__}_Windows.zip'
        write_zip(desktop, [path for path in release.rglob('*') if path.is_file()
                           and path.relative_to(release).parts[0] not in ('logs', 'projects')],
                  release, 'PortLab')
        packages.insert(0, desktop)
    manifest = []
    for path in packages:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f'Bad ZIP entry: {bad}')
            members = len(archive.infolist())
        manifest.append({'file': path.name, 'bytes': path.stat().st_size, 'entries': members,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (delivery / f'delivery_manifest_v{__version__}.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'README.md', delivery / 'README.md')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
