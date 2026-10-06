# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import importlib.util

root = Path(SPECPATH).resolve()
panda = Path(importlib.util.find_spec('panda3d').origin).parent
qt = Path(importlib.util.find_spec('PySide6').origin).parent

# The Panda display module is loaded at runtime, so import analysis cannot find
# it. Its PE dependencies (including cg/cgGL) are collected by PyInstaller.
panda_binaries = [(str(panda / name), 'panda3d')
                  for name in ('libpandagl.dll', 'libp3windisplay.dll')]
datas = [(str(root / name), name) for name in ('assets', 'data', 'templates', 'licenses')]
analysis = Analysis(
    [str(root / 'launcher.py')], pathex=[str(root)],
    binaries=panda_binaries, datas=datas,
    hiddenimports=['panda3d.core', 'panda3d.dtoolconfig', 'portlab.visual'],
    hookspath=[], hooksconfig={}, runtime_hooks=[str(root / 'tools' / 'pyi_runtime_panda.py')],
    # Only native QtCore/Gui/Widgets are used. Avoid collecting the installed
    # 200+ MiB web-engine module or optional simulation/media frameworks.
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtWebEngineQuick', 'PySide6.QtQml', 'PySide6.QtQuick',
              'matplotlib', 'pandas', 'numpy', 'scipy', 'tkinter', 'pytest'],
    noarchive=False,
)
# PyInstaller's QtGui hook discovers every installed input/image plugin. The
# app uses no QML, PDF viewer or virtual keyboard. Keep Windows, native styles,
# JPEG/ICO plugins and built-in PNG support, and omit their unused dependents.
def keep_binary(entry):
    destination = entry[0].replace('\\', '/').lower()
    base = destination.rsplit('/', 1)[-1]
    if destination.startswith('pyside6/plugins/'):
        return ('/styles/' in destination or destination.endswith('/platforms/qwindows.dll')
                or destination.endswith('/imageformats/qjpeg.dll')
                or destination.endswith('/imageformats/qico.dll'))
    # This Qt build imports Windows' unversioned ICU API. PyInstaller can find
    # the unrelated versioned ICU bundled with the Python build first; that
    # copy lacks Qt's unsuffixed symbols. Use Windows 10/11 system ICU instead.
    return not base.startswith(('qt6qml', 'qt6quick', 'qt6virtualkeyboard', 'qt6pdf', 'qt6svg',
                                'icuuc.dll', 'icudt'))

analysis.binaries = [entry for entry in analysis.binaries if keep_binary(entry)]
analysis.binaries = [(entry[0], str(qt / 'msvcp140.dll'), entry[2])
                     if entry[0].replace('\\', '/').lower() == 'panda3d/msvcp140.dll' else entry
                     for entry in analysis.binaries]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='PortLab',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          contents_directory='_internal')
collection = COLLECT(exe, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name='PortLab')
