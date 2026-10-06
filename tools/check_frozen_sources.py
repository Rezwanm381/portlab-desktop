"""Verify frozen PortLab code against current source without executing it.

Only co_filename is ignored. All other code-object fields, including nested
functions, constants, line tables and exception tables, must match. Run with
the same Python interpreter/version used to build the frozen application.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import types
import warnings

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader


def normalize(value):
    if isinstance(value, types.CodeType):
        return value.replace(co_filename='<normalized source filename>',
                             co_consts=tuple(normalize(item) for item in value.co_consts))
    if isinstance(value, tuple):
        return tuple(normalize(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(normalize(item) for item in value)
    return value


def code_differences(expected, actual, path='<module>', limit=12):
    differences = []
    if not isinstance(actual, types.CodeType):
        return [f'{path}: frozen value is not a Python code object']
    def field_value(code, name):
        # co_lnotab is a deprecated derived field, but compare it too: the
        # verifier deliberately ignores only filenames.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            return getattr(code, name)
    for field in sorted(name for name in dir(expected)
                        if name.startswith('co_') and name != 'co_filename' and
                        not callable(field_value(expected, name))):
        left, right = field_value(expected, field), field_value(actual, field)
        if field == 'co_consts':
            if len(left) != len(right):
                differences.append(f'{path}.{field}: constants count {len(left)} != {len(right)}')
            for index, (source_constant, frozen_constant) in enumerate(zip(left, right)):
                constant_path = f'{path}.const[{index}]'
                if isinstance(source_constant, types.CodeType):
                    constant_path = f'{path}.{source_constant.co_name}'
                    differences.extend(code_differences(source_constant, frozen_constant,
                                                        constant_path, limit - len(differences)))
                elif normalize(source_constant) != normalize(frozen_constant):
                    differences.append(f'{constant_path}: constant differs')
                if len(differences) >= limit:
                    break
        elif left != right:
            differences.append(f'{path}.{field}: differs')
        if len(differences) >= limit:
            return differences[:limit]
    return differences


def verify(reader, source_root: Path, optimize: int = 0):
    modules = {}
    for path in sorted(source_root.rglob('*.py')):
        parts = list(path.relative_to(source_root).with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
        module = '.'.join([source_root.name] + parts)
        modules[module] = path
    frozen_modules = {name for name in reader.toc
                      if name == source_root.name or name.startswith(source_root.name + '.')}
    rows = []
    for name, path in modules.items():
        source_bytes = path.read_bytes()
        row = {'module': name, 'source_path': str(path),
               'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
               'source_modified_utc': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
        if name not in reader.toc:
            row.update(matches=False, differences=['Module missing from frozen archive'])
        else:
            expected = compile(source_bytes, str(path), 'exec', dont_inherit=True, optimize=optimize)
            actual = reader.extract(name)
            differences = code_differences(expected, actual)
            # The normalized equality independently checks nested code objects;
            # field diagnostics must also be empty before declaring a match.
            matches = isinstance(actual, types.CodeType) and normalize(expected) == normalize(actual) and not differences
            row.update(matches=matches, differences=differences)
        rows.append(row)
    unexpected = sorted(frozen_modules - set(modules))
    return {'matches': all(row['matches'] for row in rows) and not unexpected,
            'modules': rows, 'unexpected_frozen_modules': unexpected}


def main():
    workspace = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(workspace))
    from portlab import __version__
    version_tag = 'v' + __version__
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path,
                        default=workspace / 'build' / version_tag / 'PortLab' / 'PYZ-00.pyz')
    parser.add_argument('--exe', type=Path,
                        default=workspace / 'dist' / version_tag / 'PortLab' / 'PortLab.exe')
    parser.add_argument('--source', type=Path, default=workspace / 'portlab')
    parser.add_argument('--output', type=Path,
                        default=workspace / 'verification' / ('frozen_source_check_' + version_tag + '.json'))
    parser.add_argument('--optimize', type=int, choices=(0, 1, 2), default=0)
    args = parser.parse_args()
    for path in (args.archive, args.exe):
        if not path.is_file():
            parser.error(f'Frozen build file is missing: {path}. Run tools/build_release.py or pass --archive/--exe.')
    report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'python': sys.version,
              'comparison': 'All recursively normalized code object fields; only co_filename ignored',
              'optimize': args.optimize, 'archives': []}
    for label, path in [('build_pyz', args.archive), ('executable_pyz', args.exe)]:
        path = path.resolve()
        if label == 'build_pyz':
            reader = ZlibArchiveReader(str(path), check_pymagic=True)
        else:
            package = CArchiveReader(str(path))
            names = [name for name, entry in package.toc.items() if entry[-1] == 'z']
            if len(names) != 1:
                raise ValueError(f'Expected one embedded PYZ archive in {path}; found {names}')
            reader = package.open_embedded_archive(names[0])
        result = verify(reader, args.source.resolve(), args.optimize)
        result.update(kind=label, path=str(path), file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      archive_modified_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
        report['archives'].append(result)
    report['matches'] = all(archive['matches'] for archive in report['archives'])
    report['clean_rebuild_required'] = not report['matches']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'matches': report['matches'], 'clean_rebuild_required': report['clean_rebuild_required'],
                      'archives': [{"kind": archive['kind'], "modules_checked": len(archive['modules']),
                                    "mismatched_modules": [row['module'] for row in archive['modules'] if not row['matches']]}
                                   for archive in report['archives']],
                      'report': str(args.output.resolve())}, indent=2))
    return 0 if report['matches'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
