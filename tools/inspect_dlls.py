"""Report frozen Windows DLL import symbols absent from collected libraries."""
from pathlib import Path
import pefile

root = Path(__file__).resolve().parents[1] / 'dist' / 'PortLab' / '_internal'
locations = [root / 'PySide6', root / 'shiboken6', root, root / 'panda3d', Path('C:/Windows/System32')]
exports = {}

for target in (root / 'PySide6' / 'QtCore.pyd', root / 'PySide6' / 'Qt6Core.dll',
               root / 'PySide6' / 'pyside6.abi3.dll', root / 'shiboken6' / 'Shiboken.pyd'):
    if not target.is_file():
        continue
    image = pefile.PE(str(target))
    print('\n' + target.name)
    for dependency in image.DIRECTORY_ENTRY_IMPORT:
        library = dependency.dll.decode()
        for location in locations:
            candidate = location / library
            if not candidate.is_file():
                continue
            if candidate not in exports:
                pe = pefile.PE(str(candidate))
                exports[candidate] = {row.name for row in pe.DIRECTORY_ENTRY_EXPORT.symbols} if hasattr(pe, 'DIRECTORY_ENTRY_EXPORT') else set()
            missing = [row.name.decode() for row in dependency.imports if row.name and row.name not in exports[candidate]]
            print(str(candidate), 'missing', missing)
