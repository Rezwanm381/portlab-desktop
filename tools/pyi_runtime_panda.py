"""Locate bundled Panda display DLLs before creating the first graphics pipe."""
import os
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    root = Path(sys._MEIPASS)
    panda_directory = root / 'panda3d'
    # Windowed executables have no console. Preserve startup errors and smoke
    # diagnostics in a local log before importing either graphics framework.
    try:
        log_directory = Path(sys.executable).resolve().parent / 'logs'
        log_directory.mkdir(exist_ok=True)
        log_path = log_directory / 'startup.log'
        mode = 'w' if log_path.is_file() and log_path.stat().st_size > 2_000_000 else 'a'
        sys._portlab_log = log_path.open(mode, encoding='utf-8', buffering=1)
        if sys.stdout is None:
            sys.stdout = sys._portlab_log
        if sys.stderr is None:
            sys.stderr = sys._portlab_log
    except OSError:
        pass
    if hasattr(os, 'add_dll_directory'):
        # Keep handles alive: closing one removes that directory from DLL search.
        sys._portlab_dll_directories = [os.add_dll_directory(str(path))
                                      for path in (root, root / 'PySide6', panda_directory) if path.is_dir()]
    # Load Qt first, matching the source app. Panda wheels include an older
    # Microsoft C++ runtime with the same DLL name; Qt requires newer symbols.
    from PySide6 import QtCore, QtGui, QtWidgets
    from panda3d.core import Filename, getPluginPath, loadPrcFileData
    getPluginPath().appendDirectory(Filename.fromOsSpecific(str(panda_directory)))
    getPluginPath().appendDirectory(Filename.fromOsSpecific(str(root)))
    loadPrcFileData('portlab packaged runtime', 'load-display pandagl\naudio-library-name null\nmodel-cache-dir\n')
