# PortLab third-party notices

The desktop application uses the unmodified dependencies listed below. Their
copyright notices and license texts accompany this folder. The application
code and build instructions are delivered with the project.

| Component | Version | License / upstream source |
|---|---|---|
| CPython | 3.12 | [Python Software Foundation license](https://docs.python.org/3.12/license.html) |
| PySide6 / Qt Core, GUI, Widgets / Shiboken6 | 6.11.2 | LGPL v3; [PySide source](https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.11.2), [Qt source](https://code.qt.io/cgit/qt/qtbase.git/?h=v6.11.2) |
| Panda3D | 1.10.16 | Modified BSD; [source](https://github.com/panda3d/panda3d/tree/v1.10.16) |
| NVIDIA Cg runtime | Supplied by Panda3D wheel | [NVIDIA redistributable binaries](https://developer.nvidia.com/cg-toolkit-archive); unmodified cg.dll and cgGL.dll |
| SimPy | 4.1.2 | MIT; [source](https://gitlab.com/team-simpy/simpy/-/tree/4.1.2) |
| openpyxl | 3.1.5 | MIT; [source](https://foss.heptapod.net/openpyxl/openpyxl) |
| et_xmlfile | 2.0.0 | MIT; [source](https://foss.heptapod.net/openpyxl/et_xmlfile) |
| psutil | 7.2.2 | BSD 3 clause; [source](https://github.com/giampaolo/psutil) |
| PyInstaller bootloader | 6.22.3 | GPL with bootloader exception; [source](https://github.com/pyinstaller/pyinstaller) |

Qt/PySide/Shiboken are dynamically linked. Their separate DLL/PYD files reside
under `_internal/PySide6` and `_internal/shiboken6`; compatible replacements can
be installed there. No restriction is added on modifying those libraries or
reverse engineering to debug such modifications. The build recipe and Python
application source support rebuilding against compatible modified libraries.
Qt and its use are covered by LGPL v3, with GPL v3 terms incorporated in LGPL.
Qt copyright belongs to The Qt Company Ltd. and its contributors; PySide and
Shiboken copyright belongs to their respective upstream contributors. Included
LGPL/GPL documents state those rights and conditions.

This software is based in part on the work of the FreeType Team and the
Independent JPEG Group. This product includes software written by Tim Hudson
(tjh@cryptsoft.com), Eric Young and the OpenSSL contributors, where incorporated
in the unmodified runtime libraries. Qt and Panda3D additionally use third-party
libraries documented in their [Qt notices](https://doc.qt.io/qt-6/licenses-used-in-qt.html)
and [Panda3D notices](https://docs.panda3d.org/1.10/python/distribution/thirdparty-licenses).
Full upstream source repositories and license texts remain available at those
links. These runtimes retain their original license terms.

The package does not include Panda3D's optional FMOD, FFmpeg, Assimp, physics,
or audio plugins. Windows OpenGL/graphics driver libraries are supplied by the
operating system and GPU driver. User-supplied port models are documented
separately in `assets/README.md` and `assets/manifest.json`.

There is no mandatory cloud service, API subscription, or license purchase
for this delivered application.
